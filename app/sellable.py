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
    """束ねる候補（共通商品コードごと）。並びは店の数が多い順。"""
    min_shops = max(1, int(args.get("min_shops") or 1))
    q = (args.get("q") or "").strip()
    page = max(1, int(args.get("page") or 1))
    base = (f"WITH s AS ({_SINGLE}) SELECT s.product_code, COUNT(*) AS n_listings, COUNT(DISTINCT l.shop) AS n_shops, "
            "SUM(l.state='公開') AS n_public FROM s JOIN listing l ON l.id=s.listing_id "
            + ("WHERE s.product_code LIKE ? " if q else "") + "GROUP BY s.product_code HAVING COUNT(DISTINCT l.shop) >= ?")
    ps = ([f"%{q}%"] if q else []) + [min_shops]
    total = store.val(f"SELECT COUNT(*) FROM ({base})", ps)
    groups = store.rows(store.q(base + " ORDER BY n_shops DESC, n_listings DESC, s.product_code LIMIT ? OFFSET ?",
                                ps + [PER_PAGE, (page - 1) * PER_PAGE]))
    for g in groups:
        k = store.one("SELECT cat1, cat2, cat3, shape, size, pack_qty FROM listing_cip_link WHERE product_code=? LIMIT 1",
                      (g["product_code"],))
        g.update(dict(k) if k else {})
        g["listings"] = store.rows(store.q(
            f"WITH s AS ({_SINGLE}) SELECT l.id, l.shop, sh.label AS shop_label, l.ext_id, l.title, l.price, l.state FROM s "
            "JOIN listing l ON l.id=s.listing_id JOIN shop sh ON sh.code=l.shop WHERE s.product_code=? ORDER BY sh.sort, l.ext_id",
            (g["product_code"],)))
    held = store.val("SELECT COUNT(*) FROM (SELECT k.listing_id FROM listing_cip_link k JOIN listing l ON l.id=k.listing_id "
                     "WHERE l.gone_import_id IS NULL AND l.sellable_id IS NULL GROUP BY k.listing_id "
                     "HAVING COUNT(DISTINCT k.product_code) > 1)")
    return {"rows": groups, "total": total, "page": page, "per_page": PER_PAGE, "pages": max(1, -(-total // PER_PAGE)),
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
