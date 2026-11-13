#!/usr/bin/env python3
"""
選択肢の整理（段階3・ADR-148・FR-260〜266・PL-20〜26）。

取り込んだ店の項目名（台帳の `listing_option`）を、使い回せる「選択肢の定義」に寄せる。
- **似た項目名をまとめて候補に出す**（FR-266）: 空白・記号（□■）・「表面：」「裏面：」・「【1個目】」などを外した形が同じものを1つに。
  まとめ方は案で、**どの項目名を入れるかは人が選ぶ**
- 定義は種類を持つ（FR-261）: 作るもの（作るものが変わる）／追加料金／お客さまの入力。選ぶ型か自由入力か
- 定義にすると、選んだ項目名の値が定義の値になり（FR-262）、台帳のその行が定義の値と結ばれる（`listing_option.def_value_id`）
- 値の追加の価格は、出品ごとに違うことがある。定義には**いちばん多い価格**を置き、出品ごとの違いはそのまま台帳に残る（商品ごとの上書き＝FR-263 は次）
- 対象は、選択肢に値と価格を持つ元のファイル（自社 EC の価格つきと項目選択肢・楽天の商品オプション・ギフトモールの規格とオプション・
  Yahoo の options を項目 × 値に読み分けたもの）。Shopify・楽天の SKU は次
- 定義を作る・寄せる・外すのは社長と商品開発部管理者
- お客さまの入力の値は台帳に無い（取り込むのは店の設定だけ）。自由入力の項目は値なしの定義になる
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from app import store

EDIT_ROLES = {"president", "admin"}
KINDS = ("作るもの", "追加料金", "お客さまの入力")
INPUTS = ("選ぶ", "自由入力")
SRCS = ("opt_select", "selection", "rakuten_option", "gm_class", "gm_option", "yahoo_option")
GROUPS = {"own": ("goods", "uchiwa", "frame"), "rakuten": ("rakuten",), "yahoo": ("yahoo_goods", "yahoo_frame"), "giftmall": ("giftmall",)}
PER_PAGE = 30
_STRIP = re.compile(r"^(表面|裏面|おもて面|うら面)\s*[:：]\s*|^【\d+個目】|^\d+個目[:：]?|[\s□■◆◇●○★☆※]+")


def strip_name(name: str) -> str:
    """表面：・【1個目】・□ などを外した名前（大文字小文字はそのまま）。定義の名前の既定に使う。"""
    s = unicodedata.normalize("NFKC", name or "")
    prev = None
    while prev != s:
        prev, s = s, _STRIP.sub("", s)
    return s.strip("・-_ ")


def norm(name: str) -> str:
    return strip_name(name).lower()


def _need(uid: str):
    from app import gate
    if not EDIT_ROLES & set(gate.roles_of(uid)):
        raise PermissionError("選択肢の定義を作る・直すのは、社長と商品開発部管理者の業務ロールの人です")


def _rev(c, did: int, uid: str, what: str, detail: str = ""):
    c.execute("INSERT INTO option_def_revision (def_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
              (did, store.now_s(), uid, what, detail or None))


def clusters(args: dict) -> dict:
    grp = args.get("group") if args.get("group") in GROUPS else "own"
    shops = GROUPS[grp]
    priced_only = (args.get("priced") or "1") == "1"
    todo_only = (args.get("todo") or "1") == "1"
    page = max(1, int(args.get("page") or 1))
    q = ",".join("?" * len(shops))
    rows = store.q(
        f"SELECT l.shop, o.src, o.item_name, COUNT(DISTINCT o.listing_id) n_listings, COUNT(DISTINCT o.value) n_values, "
        f"SUM(o.price IS NOT NULL AND o.price<>0) n_priced, MIN(o.price) pmin, MAX(o.price) pmax, COALESCE(a.def_id, -pa.rule_id) def_id "
        f"FROM listing_option o JOIN listing l ON l.id=o.listing_id "
        f"LEFT JOIN option_alias a ON a.shop=l.shop AND a.src=o.src AND a.item_name=o.item_name "
        f"LEFT JOIN price_rule_alias pa ON pa.shop=l.shop AND pa.src=o.src AND pa.item_name=o.item_name "
        f"WHERE l.gone_import_id IS NULL AND l.shop IN ({q}) AND o.src IN ({','.join('?' * len(SRCS))}) "
        f"AND o.item_name IS NOT NULL AND o.item_name<>'' GROUP BY l.shop, o.src, o.item_name", list(shops) + list(SRCS))
    by = {}
    for r in rows:
        by.setdefault(norm(r["item_name"]), []).append(dict(r))
    out = []
    for key, ms in by.items():
        if todo_only and all(m["def_id"] for m in ms):
            continue
        if priced_only and not any(m["n_priced"] for m in ms):
            continue
        out.append({"key": key, "members": sorted(ms, key=lambda m: -m["n_listings"]),
                    "n_listings": sum(m["n_listings"] for m in ms), "priced": any(m["n_priced"] for m in ms)})
    out.sort(key=lambda c: (-c["n_listings"], c["key"]))
    total = len(out)
    rows = out[(page - 1) * PER_PAGE: page * PER_PAGE]
    for c in rows:
        for m in c["members"]:
            vs = store.q("SELECT o.value, COUNT(*) n, o.price FROM listing_option o JOIN listing l ON l.id=o.listing_id "
                         "WHERE l.shop=? AND o.src=? AND o.item_name=? AND o.value IS NOT NULL AND l.gone_import_id IS NULL "
                         "GROUP BY o.value, o.price ORDER BY n DESC LIMIT 12", (m["shop"], m["src"], m["item_name"]))
            m["values"] = [{"value": v["value"], "n": v["n"], "price": v["price"]} for v in vs]
        c["suggest_kind"] = "追加料金" if c["priced"] else ("お客さまの入力" if all(not m["n_values"] for m in c["members"]) else "作るもの")
        c["suggest_input"] = "自由入力" if all(not m["n_values"] for m in c["members"]) else "選ぶ"
        c["suggest_name"] = strip_name(c["members"][0]["item_name"]) or c["members"][0]["item_name"]
        from app import pricerule
        # 数量の段として読めるなら「価格の決まり」の候補（ADR-149）。出品ごとの段の表の種類と件数
        c["tier_tables"] = pricerule.suggest([(m["shop"], m["src"], m["item_name"]) for m in c["members"] if not m["def_id"]])
    defs = store.rows(store.q("SELECT id, name, kind FROM option_def ORDER BY name"))
    done = store.val(f"SELECT COUNT(*) FROM option_alias WHERE shop IN ({q})", list(shops))
    return {"group": grp, "rows": rows, "total": total, "page": page, "per_page": PER_PAGE,
            "pages": max(1, -(-total // PER_PAGE)), "defs": defs, "aliased": done}


def _members(raw: list[str]) -> list[tuple[str, str, str]]:
    out = []
    for x in raw:
        p = str(x).split("|", 2)
        if len(p) != 3 or not p[2]:
            raise ValueError("寄せる項目名の形が違います")
        out.append((p[0], p[1], p[2]))
    if not out:
        raise ValueError("寄せる項目名を1つ以上選んでください")
    return out


def assign(fields: dict, members: list[str], uid: str) -> dict:
    """項目名を定義に寄せる。fields に def_id があれば既存の定義へ、無ければ name・kind・input_type で新しく作る。"""
    _need(uid)
    ms = _members(members)
    now = store.now_s()
    with store.tx() as c:
        did = str(fields.get("def_id") or "").strip()
        if did:
            r = c.execute("SELECT id, name FROM option_def WHERE id=?", (int(did),)).fetchone()
            if r is None:
                raise LookupError("その定義はありません")
            did = r["id"]
            what = "項目名を寄せた"
        else:
            name = (fields.get("name") or "").strip()
            kind, inp = fields.get("kind"), fields.get("input_type") or "選ぶ"
            if not name:
                raise ValueError("定義の名前が空です")
            if kind not in KINDS:
                raise ValueError("種類は 作るもの・追加料金・お客さまの入力 のどれかです")
            if inp not in INPUTS:
                raise ValueError("型は 選ぶ・自由入力 のどちらかです")
            if c.execute("SELECT 1 FROM option_def WHERE name=?", (name,)).fetchone():
                raise ValueError(f"同じ名前の定義があります: {name}（既存の定義に寄せてください）")
            did = c.execute("INSERT INTO option_def (name,kind,input_type,created_by,created_at,updated_by,updated_at) "
                            "VALUES (?,?,?,?,?,?,?)", (name, kind, inp, uid, now, uid, now)).lastrowid
            what = "定義を作った"
        for shop, src, item in ms:
            hit = c.execute("SELECT 1 FROM listing_option o JOIN listing l ON l.id=o.listing_id WHERE l.shop=? AND o.src=? AND o.item_name=? LIMIT 1",
                            (shop, src, item)).fetchone()
            if hit is None:
                raise LookupError(f"台帳にその項目名がありません: {shop} {item}")
            if c.execute("SELECT 1 FROM price_rule_alias WHERE shop=? AND src=? AND item_name=?", (shop, src, item)).fetchone():
                raise ValueError(f"「{item}」（{shop}）は価格の決まりに寄せてあります。先に外してください")
            old = c.execute("SELECT def_id FROM option_alias WHERE shop=? AND src=? AND item_name=?", (shop, src, item)).fetchone()
            if old and old["def_id"] != did:
                raise ValueError(f"「{item}」（{shop}）はもう別の定義に寄せてあります。先に外してください")
            c.execute("INSERT OR IGNORE INTO option_alias (shop,src,item_name,def_id,by_user,at) VALUES (?,?,?,?,?,?)",
                      (shop, src, item, did, uid, now))
        n_values, n_linked = _sync_values(c, did)
        _rev(c, did, uid, what, "、".join(f"{s}:{i}" for s, _, i in ms))
    store.audit(uid, "optdef.assign", str(did), {"members": len(ms), "values": n_values, "linked": n_linked})
    return {"def_id": did, "values": n_values, "linked": n_linked}


def _sync_values(c, did: int) -> tuple[int, int]:
    """寄せた項目名の値から定義の値を足し（消さない）、台帳の行を定義の値に結ぶ。"""
    rows = c.execute("SELECT o.id, o.value, o.code, o.price FROM listing_option o JOIN listing l ON l.id=o.listing_id "
                     "JOIN option_alias a ON a.shop=l.shop AND a.src=o.src AND a.item_name=o.item_name "
                     "WHERE a.def_id=? AND o.value IS NOT NULL AND o.value<>''", (did,)).fetchall()
    stats = {}
    for r in rows:
        v = r["value"].strip()
        st = stats.setdefault(v, {"codes": Counter(), "prices": Counter(), "n": 0})
        st["n"] += 1
        if r["code"]:
            st["codes"][r["code"]] += 1
        st["prices"][r["price"]] += 1
    have = {r["label"]: r["id"] for r in c.execute("SELECT id, label FROM option_value WHERE def_id=?", (did,))}
    used = {r["code"] for r in c.execute("SELECT code FROM option_value WHERE def_id=?", (did,))}
    sort = c.execute("SELECT COALESCE(MAX(sort),0) FROM option_value WHERE def_id=?", (did,)).fetchone()[0]
    for v, st in sorted(stats.items(), key=lambda kv: -kv[1]["n"]):
        if v in have:
            continue
        code = st["codes"].most_common(1)[0][0] if len(st["codes"]) == 1 else None
        if not code or code in used:
            n = 1
            while f"v{n:03d}" in used:
                n += 1
            code = f"v{n:03d}"
        used.add(code)
        sort += 1
        price = st["prices"].most_common(1)[0][0]
        have[v] = c.execute("INSERT INTO option_value (def_id,code,label,sort,add_price) VALUES (?,?,?,?,?)",
                            (did, code, v, sort, price if price else None)).lastrowid
    linked = 0
    for r in rows:
        vid = have.get(r["value"].strip())
        if vid:
            c.execute("UPDATE listing_option SET def_value_id=? WHERE id=?", (vid, r["id"]))
            linked += 1
    return len(have), linked


def detach(did: int, shop: str, src: str, item: str, uid: str) -> dict:
    """項目名を定義から外す。台帳の結びを外す（定義の値は消さない）。"""
    _need(uid)
    with store.tx() as c:
        r = c.execute("SELECT 1 FROM option_alias WHERE def_id=? AND shop=? AND src=? AND item_name=?", (did, shop, src, item)).fetchone()
        if r is None:
            raise LookupError("その項目名はこの定義に寄せていません")
        c.execute("UPDATE listing_option SET def_value_id=NULL WHERE id IN (SELECT o.id FROM listing_option o JOIN listing l ON l.id=o.listing_id "
                  "WHERE l.shop=? AND o.src=? AND o.item_name=?)", (shop, src, item))
        c.execute("DELETE FROM option_alias WHERE def_id=? AND shop=? AND src=? AND item_name=?", (did, shop, src, item))
        _rev(c, did, uid, "項目名を外した", f"{shop}:{item}")
    store.audit(uid, "optdef.detach", str(did), {"shop": shop, "src": src})
    return {"ok": True}


def update(did: int, f: dict, uid: str) -> dict:
    _need(uid)
    cur = store.one("SELECT * FROM option_def WHERE id=?", (did,))
    if cur is None:
        raise LookupError("その定義はありません")
    ch = {}
    if "name" in f and (f["name"] or "").strip() and f["name"].strip() != cur["name"]:
        ch["name"] = f["name"].strip()
    if "kind" in f and f["kind"] != cur["kind"]:
        if f["kind"] not in KINDS:
            raise ValueError("種類は 作るもの・追加料金・お客さまの入力 のどれかです")
        ch["kind"] = f["kind"]
    if "input_type" in f and f["input_type"] != cur["input_type"]:
        if f["input_type"] not in INPUTS:
            raise ValueError("型は 選ぶ・自由入力 のどちらかです")
        ch["input_type"] = f["input_type"]
    if not ch:
        return {"ok": True, "changed": []}
    if "name" in ch and store.one("SELECT 1 FROM option_def WHERE name=? AND id<>?", (ch["name"], did)):
        raise ValueError("同じ名前の定義があります")
    with store.tx() as c:
        c.execute(f"UPDATE option_def SET {', '.join(k + '=?' for k in ch)}, updated_by=?, updated_at=? WHERE id=?",
                  list(ch.values()) + [uid, store.now_s(), did])
        for k, v in ch.items():
            _rev(c, did, uid, {"name": "名前", "kind": "種類", "input_type": "型"}[k] + "を直した", f"前: {cur[k]} → {v}")
    return {"ok": True, "changed": list(ch)}


def defs() -> list[dict]:
    return store.rows(store.q(
        "SELECT d.id, d.name, d.kind, d.input_type, (SELECT COUNT(*) FROM option_value v WHERE v.def_id=d.id) n_values, "
        "(SELECT COUNT(*) FROM option_alias a WHERE a.def_id=d.id) n_alias, "
        "(SELECT COUNT(DISTINCT o.listing_id) FROM listing_option o JOIN option_value v ON v.id=o.def_value_id WHERE v.def_id=d.id) n_listings "
        "FROM option_def d ORDER BY d.name"))


def detail(did: int) -> dict:
    r = store.one("SELECT * FROM option_def WHERE id=?", (did,))
    if r is None:
        raise LookupError("その定義はありません")
    d = dict(r)
    d["values"] = store.rows(store.q("SELECT v.id, v.code, v.label, v.sort, v.add_price, (SELECT COUNT(*) FROM listing_option o WHERE o.def_value_id=v.id) n "
                                     "FROM option_value v WHERE v.def_id=? ORDER BY v.sort", (did,)))
    d["aliases"] = store.rows(store.q("SELECT a.shop, s.label shop_label, a.src, a.item_name, a.by_user, a.at FROM option_alias a JOIN shop s ON s.code=a.shop "
                                      "WHERE a.def_id=? ORDER BY s.sort, a.item_name", (did,)))
    d["revisions"] = store.rows(store.q("SELECT changed_at, changed_by, what, detail FROM option_def_revision WHERE def_id=? ORDER BY id DESC LIMIT 50", (did,)))
    d["kinds"], d["inputs"] = list(KINDS), list(INPUTS)
    return d
