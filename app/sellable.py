#!/usr/bin/env python3
"""
売るもの（基準の商品）と、店をまたいで出品を束ねる（段階2・ADR-147・FR-256〜259・280・290）。

- **束ねる候補**（FR-290・PL-63）: CIP の共通商品コードが同じ出品を、店をまたいで1つの候補にする（台帳と CIP の結び付け＝ADR-146 から）。
  **決めるのは人**（選んだ候補だけ売るものにする）
- **選択肢で作るものが変わる出品**（1つの出品が複数の共通商品コードに結ばれている）は候補に入れない。段階3（選択肢の整理）で扱う
- 売るものの名前は**社内の呼び名**。CIP の商品名は読めない（CLAUDE.md §3-1）ので、既定は「共通商品コード 分類 サイズ 入数」。あとで直す
- 状態の既定: 束ねた出品に公開のものが1つでもあれば「販売中」、無ければ「終了」
- 束ねる・外す・直すのは、社長と商品開発部管理者（店のマスタに近い操作。要件案 3章の「管理者」）
- 変更はすべて `sellable_revision` に残す（FR-259）
"""
from __future__ import annotations

from app import store

EDIT_ROLES = {"president", "admin"}
STATES = ("準備中", "販売中", "終了")
PER_PAGE = 50


def _need(uid: str):
    from app import gate
    if not EDIT_ROLES & set(gate.roles_of(uid)):
        raise PermissionError("売るものを作る・直すのは、社長と商品開発部管理者の業務ロールの人です")


def _rev(c, sid: str, uid: str, what: str, detail: str = ""):
    c.execute("INSERT INTO sellable_revision (sellable_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
              (sid, store.now_s(), uid, what, detail or None))


# 1つの共通商品コードだけに結ばれた、まだ束ねていない出品
_SINGLE = ("SELECT k.listing_id, MIN(k.product_code) AS product_code FROM listing_cip_link k JOIN listing l ON l.id=k.listing_id "
           "WHERE l.gone_import_id IS NULL AND l.sellable_id IS NULL GROUP BY k.listing_id HAVING COUNT(DISTINCT k.product_code)=1")


def candidates(args: dict) -> dict:
    """束ねる候補（共通商品コードごと）。並びは店の数が多い順。

    **まとめて1回だけ数える**（11-13 本番で、候補ごとに数え直す形が1ページ 3 秒かかった）。"""
    min_shops = max(1, int(args.get("min_shops") or 1))
    q = (args.get("q") or "").strip()
    page = max(1, int(args.get("page") or 1))
    single = {r["listing_id"]: r["product_code"] for r in store.q(_SINGLE)}
    info = {}
    if single:
        for r in store.q("SELECT l.id, l.shop, sh.label AS shop_label, sh.sort, l.ext_id, l.title, l.price, l.state FROM listing l "
                         "JOIN shop sh ON sh.code=l.shop WHERE l.sellable_id IS NULL AND l.gone_import_id IS NULL"):
            if r["id"] in single:
                info[r["id"]] = dict(r)
    by = {}
    for lid, code in single.items():
        if q and q.lower() not in code.lower():
            continue
        by.setdefault(code, []).append(info[lid])
    groups = []
    for code, ls in by.items():
        shops = {x["shop"] for x in ls}
        if len(shops) < min_shops:
            continue
        groups.append({"product_code": code, "n_listings": len(ls), "n_shops": len(shops),
                       "n_public": sum(1 for x in ls if x["state"] == "公開"), "listings": ls})
    groups.sort(key=lambda g: (-g["n_shops"], -g["n_listings"], g["product_code"]))
    total = len(groups)
    rows = groups[(page - 1) * PER_PAGE: page * PER_PAGE]
    if rows:
        codes = [g["product_code"] for g in rows]
        cats = {r["product_code"]: dict(r) for r in store.q(
            f"SELECT product_code, MIN(cat1) cat1, MIN(cat2) cat2, MIN(cat3) cat3, MIN(shape) shape, MIN(size) size, "
            f"MIN(pack_qty) pack_qty FROM listing_cip_link WHERE product_code IN ({','.join('?' * len(codes))}) GROUP BY product_code", codes)}
        for g in rows:
            g.update({k: v for k, v in cats.get(g["product_code"], {}).items() if k != "product_code"})
            g["listings"] = [{k: x[k] for k in ("id", "shop", "shop_label", "ext_id", "title", "price", "state")}
                             for x in sorted(g["listings"], key=lambda x: (x["sort"], x["ext_id"]))]
    held = store.val("SELECT COUNT(*) FROM (SELECT k.listing_id FROM listing_cip_link k JOIN listing l ON l.id=k.listing_id "
                     "WHERE l.gone_import_id IS NULL AND l.sellable_id IS NULL GROUP BY k.listing_id "
                     "HAVING COUNT(DISTINCT k.product_code) > 1)")
    return {"rows": rows, "total": total, "page": page, "per_page": PER_PAGE, "pages": max(1, -(-total // PER_PAGE)),
            "held_multi": held, "bundled": store.val("SELECT COUNT(*) FROM sellable")}


def _default_label(code: str, k) -> str:
    if k is None:
        return code
    parts = [code] + [x for x in (k["cat1"], k["cat2"], k["cat3"]) if x] + [x for x in (k["size"],) if x]
    if k["pack_qty"] and int(k["pack_qty"]) > 1:
        parts.append(f"{k['pack_qty']}個入")
    return " ".join(str(x) for x in parts)


def bundle(codes: list[str], uid: str) -> dict:
    """選んだ共通商品コードごとに売るものを1つ作り、そのコードだけに結ばれた出品を束ねる。"""
    _need(uid)
    codes = [c.strip() for c in codes if c and c.strip()]
    if not codes:
        raise ValueError("候補を選んでください")
    made, skipped = [], []
    now = store.now_s()
    with store.tx() as c:
        for code in dict.fromkeys(codes):
            ls = [r for r in c.execute(f"WITH s AS ({_SINGLE}) SELECT l.id, l.state FROM s JOIN listing l ON l.id=s.listing_id "
                                       "WHERE s.product_code=?", (code,))]
            if not ls:
                skipped.append(code)               # もう束ねてある・候補でなくなった
                continue
            k = c.execute("SELECT cat1, cat2, cat3, shape, size, pack_qty FROM listing_cip_link WHERE product_code=? LIMIT 1",
                          (code,)).fetchone()
            sid = store.new_id("sellable", 6)
            state = "販売中" if any(r["state"] == "公開" for r in ls) else "終了"
            c.execute("INSERT INTO sellable (id,label,cat1,cat2,cat3,state,created_by,created_at,updated_by,updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (sid, _default_label(code, k), k["cat1"] if k else None, k["cat2"] if k else None, k["cat3"] if k else None,
                       state, uid, now, uid, now))
            c.execute("INSERT INTO sellable_make (sellable_id, combo, product_code, qty) VALUES (?,?,?,1)", (sid, "", code))
            c.executemany("UPDATE listing SET sellable_id=? WHERE id=?", [(sid, r["id"]) for r in ls])
            _rev(c, sid, uid, "束ねて作った", f"共通商品コード {code} の出品 {len(ls)} 件")
            made.append({"id": sid, "product_code": code, "listings": len(ls)})
    store.audit(uid, "sellable.bundle", "", {"made": len(made), "skipped": skipped, "codes": [m["product_code"] for m in made]})
    return {"made": made, "skipped": skipped}


def listing_(args: dict) -> dict:
    where, ps = ["1=1"], []
    st = args.get("state") or ""
    if st in STATES:
        where.append("s.state=?"); ps.append(st)
    for w in (args.get("q") or "").split():
        where.append("(s.id LIKE ? OR s.label LIKE ? OR EXISTS (SELECT 1 FROM sellable_make m WHERE m.sellable_id=s.id AND m.product_code LIKE ?))")
        ps += [f"%{w}%"] * 3
    page = max(1, int(args.get("page") or 1))
    w = " AND ".join(where)
    total = store.val(f"SELECT COUNT(*) FROM sellable s WHERE {w}", ps)
    rows = store.rows(store.q(
        f"SELECT s.id, s.label, s.state, s.cat1, s.cat2, s.cat3, s.base_price, s.project_id, "
        f"(SELECT GROUP_CONCAT(DISTINCT m.product_code) FROM sellable_make m WHERE m.sellable_id=s.id) AS codes, "
        f"(SELECT COUNT(*) FROM listing l WHERE l.sellable_id=s.id) AS n_listings, "
        f"(SELECT COUNT(DISTINCT l.shop) FROM listing l WHERE l.sellable_id=s.id) AS n_shops "
        f"FROM sellable s WHERE {w} ORDER BY s.created_at DESC, s.id LIMIT ? OFFSET ?", ps + [PER_PAGE, (page - 1) * PER_PAGE]))
    counts = {r["state"]: r["n"] for r in store.q("SELECT state, COUNT(*) n FROM sellable GROUP BY state")}
    return {"rows": rows, "total": total, "page": page, "per_page": PER_PAGE, "pages": max(1, -(-total // PER_PAGE)), "counts": counts}


def detail(sid: str) -> dict:
    r = store.one("SELECT * FROM sellable WHERE id=?", (sid,))
    if r is None:
        raise LookupError("売るものがありません")
    d = dict(r)
    d["makes"] = store.rows(store.q("SELECT combo, product_code, qty FROM sellable_make WHERE sellable_id=? ORDER BY combo, product_code", (sid,)))
    d["listings"] = store.rows(store.q("SELECT l.id, l.shop, sh.label AS shop_label, l.ext_id, l.title, l.price, l.state, l.gone_import_id "
                                       "FROM listing l JOIN shop sh ON sh.code=l.shop WHERE l.sellable_id=? ORDER BY sh.sort, l.ext_id", (sid,)))
    d["revisions"] = store.rows(store.q("SELECT changed_at, changed_by, what, detail FROM sellable_revision WHERE sellable_id=? "
                                        "ORDER BY id DESC LIMIT 50", (sid,)))
    d["states"] = list(STATES)
    d.update(options_of(sid))
    d["all_defs"] = store.rows(store.q("SELECT id, name, kind FROM option_def ORDER BY name"))
    return d


def update(sid: str, f: dict, uid: str) -> dict:
    _need(uid)
    cur = store.one("SELECT * FROM sellable WHERE id=?", (sid,))
    if cur is None:
        raise LookupError("売るものがありません")
    ch = {}
    if "label" in f:
        v = (f.get("label") or "").strip()
        if not v:
            raise ValueError("呼び名が空です")
        ch["label"] = v[:200]
    if "state" in f:
        if f["state"] not in STATES:
            raise ValueError("状態は 準備中・販売中・終了 のどれかです")
        ch["state"] = f["state"]
    if "base_price" in f:
        v = str(f.get("base_price") or "").replace(",", "").strip()
        if v and not v.isdigit():
            raise ValueError("基準の価格は円の整数で入れてください（未定なら空）")
        ch["base_price"] = int(v) if v else None             # 0 で埋めない（未定は空）
    ch = {k: v for k, v in ch.items() if cur[k] != v}
    if not ch:
        return {"ok": True, "changed": []}
    now = store.now_s()
    with store.tx() as c:
        c.execute(f"UPDATE sellable SET {', '.join(k + '=?' for k in ch)}, updated_by=?, updated_at=? WHERE id=?",
                  list(ch.values()) + [uid, now, sid])
        for k, v in ch.items():
            _rev(c, sid, uid, {"label": "呼び名", "state": "状態", "base_price": "基準の価格"}[k] + "を直した", f"前: {cur[k]} → {v}")
    store.audit(uid, "sellable.update", sid, {"fields": list(ch)})
    return {"ok": True, "changed": list(ch)}


def unlink(sid: str, listing_id: int, uid: str) -> dict:
    """出品を売るものから外す。外した出品は、また束ねる候補に戻る。"""
    _need(uid)
    r = store.one("SELECT ext_id, shop FROM listing WHERE id=? AND sellable_id=?", (listing_id, sid))
    if r is None:
        raise LookupError("その出品はこの売るものに束ねていません")
    with store.tx() as c:
        c.execute("UPDATE listing SET sellable_id=NULL WHERE id=?", (listing_id,))
        _rev(c, sid, uid, "出品を外した", f"{r['shop']}：{r['ext_id']}")
    store.audit(uid, "sellable.unlink", sid, {"listing_id": listing_id})
    return {"ok": True}


# ── 売るものに付ける選択肢（段階3・ADR-150・FR-263）────────────────────────
# 使う値を絞り、価格を商品ごとに上書きする。価格が空なら定義の追加の価格を使う（0 で埋めない）

def _sellable(sid: str):
    r = store.one("SELECT id FROM sellable WHERE id=?", (sid,))
    if r is None:
        raise LookupError("売るものがありません")


def options_of(sid: str) -> dict:
    """付けた選択肢（定義ごとに値・使うか・価格）と、束ねた出品が使っている価格の決まり。"""
    used = {r["def_value_id"]: r["n"] for r in store.q(
        "SELECT o.def_value_id, COUNT(DISTINCT o.listing_id) n FROM listing_option o JOIN listing l ON l.id=o.listing_id "
        "WHERE l.sellable_id=? AND o.def_value_id IS NOT NULL GROUP BY o.def_value_id", (sid,))}
    out = []
    for d in store.q("SELECT d.id, d.name, d.kind, d.input_type, so.sort FROM sellable_option so JOIN option_def d ON d.id=so.def_id "
                     "WHERE so.sellable_id=? ORDER BY so.sort, d.name", (sid,)):
        vals = []
        for v in store.q("SELECT v.id, v.code, v.label, v.add_price, sv.enabled, sv.price FROM option_value v "
                         "LEFT JOIN sellable_option_value sv ON sv.value_id=v.id AND sv.sellable_id=? WHERE v.def_id=? ORDER BY v.sort",
                         (sid, d["id"])):
            vals.append({"id": v["id"], "code": v["code"], "label": v["label"], "def_price": v["add_price"],
                         "enabled": bool(v["enabled"]), "price": v["price"],
                         "effective": v["price"] if v["price"] is not None else v["add_price"], "listings": used.get(v["id"], 0)})
        out.append({**dict(d), "values": vals})
    attached = {o["id"] for o in out}
    unattached = store.rows(store.q(
        "SELECT DISTINCT d.id, d.name FROM listing_option o JOIN listing l ON l.id=o.listing_id JOIN option_value v ON v.id=o.def_value_id "
        "JOIN option_def d ON d.id=v.def_id WHERE l.sellable_id=? ORDER BY d.name", (sid,)))
    rules = store.rows(store.q(
        "SELECT p.id, p.name, p.tiers_json, COUNT(DISTINCT x.listing_id) n FROM price_rule_listing x JOIN listing l ON l.id=x.listing_id "
        "JOIN price_rule p ON p.id=x.rule_id WHERE l.sellable_id=? GROUP BY p.id ORDER BY p.name", (sid,)))
    import json as _json
    for r in rules:
        r["tiers"] = _json.loads(r.pop("tiers_json"))
    return {"options": out, "in_listings_not_attached": [x for x in unattached if x["id"] not in attached],
            "price_rules": rules}


def copy_from_listings(sid: str, uid: str) -> dict:
    """束ねた出品が使っている定義の値を、売るものの選択肢として写す（既にあるものは触らない）。
    価格は出品でいちばん多い価格。定義の追加の価格と同じなら空（定義に従う）。"""
    _need(uid)
    _sellable(sid)
    from collections import Counter
    rows = store.q("SELECT v.def_id, o.def_value_id vid, o.price, v.add_price FROM listing_option o JOIN listing l ON l.id=o.listing_id "
                   "JOIN option_value v ON v.id=o.def_value_id WHERE l.sellable_id=?", (sid,))
    prices, defs = {}, []
    for r in rows:
        prices.setdefault(r["vid"], [r["add_price"], Counter()])[1][r["price"]] += 1
        if r["def_id"] not in defs:
            defs.append(r["def_id"])
    n_def = n_val = 0
    with store.tx() as c:
        have = {r[0] for r in c.execute("SELECT def_id FROM sellable_option WHERE sellable_id=?", (sid,))}
        sort = c.execute("SELECT COALESCE(MAX(sort),0) FROM sellable_option WHERE sellable_id=?", (sid,)).fetchone()[0]
        for did in defs:
            if did in have:
                continue
            sort += 1
            c.execute("INSERT INTO sellable_option (sellable_id, def_id, sort) VALUES (?,?,?)", (sid, did, sort))
            n_def += 1
            for vid, (addp, cnt) in prices.items():
                if c.execute("SELECT def_id FROM option_value WHERE id=?", (vid,)).fetchone()[0] != did:
                    continue
                p = cnt.most_common(1)[0][0]
                c.execute("INSERT OR IGNORE INTO sellable_option_value (sellable_id, value_id, enabled, price) VALUES (?,?,1,?)",
                          (sid, vid, None if (p or None) == (addp or None) else p))
                n_val += 1
        if n_def:
            _rev(c, sid, uid, "選択肢を台帳から写した", f"定義 {n_def}・値 {n_val}")
    return {"defs": n_def, "values": n_val}


def attach_def(sid: str, did: int, uid: str) -> dict:
    """定義を付ける（値はすべて使う・価格は定義に従う）。"""
    _need(uid)
    _sellable(sid)
    d = store.one("SELECT name FROM option_def WHERE id=?", (did,))
    if d is None:
        raise LookupError("その定義はありません")
    with store.tx() as c:
        if c.execute("SELECT 1 FROM sellable_option WHERE sellable_id=? AND def_id=?", (sid, did)).fetchone():
            raise ValueError("この選択肢はもう付いています")
        sort = c.execute("SELECT COALESCE(MAX(sort),0)+1 FROM sellable_option WHERE sellable_id=?", (sid,)).fetchone()[0]
        c.execute("INSERT INTO sellable_option (sellable_id, def_id, sort) VALUES (?,?,?)", (sid, did, sort))
        c.execute("INSERT OR IGNORE INTO sellable_option_value (sellable_id, value_id, enabled, price) "
                  "SELECT ?, id, 1, NULL FROM option_value WHERE def_id=?", (sid, did))
        _rev(c, sid, uid, "選択肢を付けた", d["name"])
    return {"ok": True}


def save_values(sid: str, did: int, rows: list[dict], uid: str) -> dict:
    """値ごとに 使う／使わない と価格（空なら定義に従う）を保存する。"""
    _need(uid)
    if store.one("SELECT 1 FROM sellable_option WHERE sellable_id=? AND def_id=?", (sid, did)) is None:
        raise LookupError("この選択肢は付いていません")
    valid = {r["id"] for r in store.q("SELECT id FROM option_value WHERE def_id=?", (did,))}
    ch = []
    with store.tx() as c:
        for r in rows:
            vid = int(r["value_id"])
            if vid not in valid:
                raise ValueError("この選択肢の値ではありません")
            p = str(r.get("price") if r.get("price") is not None else "").replace(",", "").strip()
            if p and not (p.lstrip("-").isdigit()):
                raise ValueError("価格は円の整数で入れてください（定義に従うなら空）")
            price = int(p) if p else None
            en = 1 if r.get("enabled") else 0
            cur = c.execute("SELECT enabled, price FROM sellable_option_value WHERE sellable_id=? AND value_id=?", (sid, vid)).fetchone()
            if ((cur["enabled"], cur["price"]) if cur is not None else (0, None)) == (en, price):
                continue                          # 行が無い＝使わない・価格なし
            c.execute("INSERT INTO sellable_option_value (sellable_id, value_id, enabled, price) VALUES (?,?,?,?) "
                      "ON CONFLICT(sellable_id, value_id) DO UPDATE SET enabled=excluded.enabled, price=excluded.price", (sid, vid, en, price))
            ch.append(vid)
        if ch:
            name = c.execute("SELECT name FROM option_def WHERE id=?", (did,)).fetchone()[0]
            _rev(c, sid, uid, "選択肢の値を直した", f"{name}: {len(ch)} 値")
    return {"changed": len(ch)}


def detach_def(sid: str, did: int, uid: str) -> dict:
    _need(uid)
    with store.tx() as c:
        if c.execute("DELETE FROM sellable_option WHERE sellable_id=? AND def_id=?", (sid, did)).rowcount == 0:
            raise LookupError("この選択肢は付いていません")
        c.execute("DELETE FROM sellable_option_value WHERE sellable_id=? AND value_id IN (SELECT id FROM option_value WHERE def_id=?)", (sid, did))
        name = c.execute("SELECT name FROM option_def WHERE id=?", (did,)).fetchone()
        _rev(c, sid, uid, "選択肢を外した", name[0] if name else str(did))
    return {"ok": True}
