#!/usr/bin/env python3
"""
店の出品の台帳（段階1・ADR-141・第17章 FR-253〜290）。

- 取り込み（FR-287〜289）: 先に `preview()` で増える・変わる・同じ・消えるの件数を見せ、`apply()` で入れる。
  **同じファイルを2度入れても何も変わらない**（`row_hash` が同じなら触らない）。`undo()` でその店の最後の取り込みを戻せる
- 消えた出品は消さない（`gone_import_id` を立てる）。次にまた出てきたら戻す
- **取り込めるのは管理者だけ**（FR-302）。画面からは見るだけ（段階1）
- お客さまの情報は入らない前提だが、入れる前にメールアドレス・電話番号の形を数え、1件でもあれば止める（値は出さない）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app import listing_parse as lp
from app import store

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\d/._-])0\d{1,4}-\d{1,4}-\d{3,4}(?![\d/._-])")
PER_PAGE = 50


def _j(d) -> str:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


def shops() -> list[dict]:
    return store.rows(store.q("SELECT * FROM shop ORDER BY sort"))


def shop(code: str) -> dict:
    r = store.one("SELECT * FROM shop WHERE code=?", (code,))
    if r is None:
        raise LookupError(f"店がありません: {code}")
    return dict(r)


def _pii_counts(p: dict) -> dict:
    n = {"email": 0, "phone": 0}

    def scan(d):
        for v in d.values():
            s = str(v)
            n["email"] += bool(EMAIL.search(s))
            n["phone"] += bool(PHONE.search(s))
    for r in p["recs"].values():
        scan(r["raw"])
        for o in r["options"]:
            scan(o["raw"])
    for m in p["masters"].values():
        scan(m["raw"])
    return n


def _diff(code: str, p: dict) -> dict:
    cur = {r["ext_id"]: (r["row_hash"], r["gone_import_id"]) for r in store.q(
        "SELECT ext_id, row_hash, gone_import_id FROM listing WHERE shop=?", (code,))}
    added, changed, same, back = [], [], [], []
    for k, r in p["recs"].items():
        if k not in cur:
            added.append(k)
        elif cur[k][1] is not None:
            back.append(k)                       # 消えていたものが戻った（変わった扱い）
        elif cur[k][0] != r["hash"]:
            changed.append(k)
        else:
            same.append(k)
    gone = [k for k, (_, g) in cur.items() if g is None and k not in p["recs"]]
    mcur = {r["ext_id"]: (r["row_hash"], r["gone_import_id"]) for r in store.q(
        "SELECT ext_id, row_hash, gone_import_id FROM shop_option_master WHERE shop=?", (code,))}
    m_add = [k for k in p["masters"] if k not in mcur]
    m_chg = [k for k, m in p["masters"].items() if k in mcur and (mcur[k][1] is not None or mcur[k][0] != m["hash"])]
    m_gone = [k for k, (_, g) in mcur.items() if g is None and k not in p["masters"]]
    return {"added": added, "changed": changed + back, "same": same, "gone": gone, "returned": back,
            "m_added": m_add, "m_changed": m_chg, "m_gone": m_gone}


def _files_in(paths) -> list[Path]:
    out = []
    for x in paths:
        x = Path(x)
        out += sorted(y for y in x.iterdir() if y.is_file()) if x.is_dir() else [x]
    return out


def preview(code: str, paths) -> dict:
    s = shop(code)
    p = lp.parse(s["cart"], _files_in(paths))
    d = _diff(code, p)
    pii = _pii_counts(p)
    errors = list(p["errors"])
    if pii["email"] or pii["phone"]:
        errors.append(f"お客さまの情報らしい値があります（メールアドレスの形 {pii['email']} 件・電話番号の形 {pii['phone']} 件）。"
                      "取り込みません。どの列かを確かめてください")
    counts = {k: len(v) for k, v in d.items()}
    counts["options"] = sum(len(r["options"]) for r in p["recs"].values())
    counts["listings"] = len(p["recs"])
    counts["masters"] = len(p["masters"])
    counts["orphans"] = sum(p["orphans"].values())
    states = {}
    for r in p["recs"].values():
        states[r["state"]] = states.get(r["state"], 0) + 1
    files = [{k: v for k, v in f.items() if k != "dropped_columns"} | {"dropped": len(f.get("dropped_columns") or [])}
             for f in p["files"]]
    return {"shop": s, "files": files, "counts": counts, "states": states, "orphans": p["orphans"], "pii": pii,
            "errors": errors, "state_checked": p["state_checked"], "_parsed": p, "_diff": d}


def _snapshot(lid: int) -> dict:
    r = dict(store.one("SELECT * FROM listing WHERE id=?", (lid,)))
    r["options"] = store.rows(store.q("SELECT src,item_name,value,code,price,sort,raw_json,def_value_id FROM listing_option "
                                      "WHERE listing_id=? ORDER BY id", (lid,)))
    return r


def _write_options(c, lid: int, opts: list[dict]):
    c.execute("DELETE FROM listing_option WHERE listing_id=?", (lid,))
    c.executemany("INSERT INTO listing_option (listing_id,src,item_name,value,code,price,sort,raw_json) VALUES (?,?,?,?,?,?,?,?)",
                  [(lid, o["src"], o["item_name"], o["value"], o["code"], o["price"], o["sort"],
                    _j(o["raw"])) for o in opts])


def apply(code: str, paths, by: str) -> dict:
    from app import gate
    if "admin" not in gate.roles_of(by):
        raise PermissionError("取り込めるのは管理者だけです（FR-302）")
    pv = preview(code, paths)
    if pv["errors"]:
        raise ValueError("取り込めません: " + " ／ ".join(pv["errors"][:5]))
    p, d = pv["_parsed"], pv["_diff"]
    now = store.now_s()
    counts = {k: v for k, v in pv["counts"].items()}
    files = pv["files"]
    with store.tx() as c:
        cur = c.execute("INSERT INTO listing_import (shop,files_json,counts_json,state,by_user,at) VALUES (?,?,?,?,?,?)",
                        (code, json.dumps(files, ensure_ascii=False), json.dumps(counts, ensure_ascii=False), "適用", by, now))
        iid = cur.lastrowid
        ids = {r["ext_id"]: r["id"] for r in c.execute("SELECT id, ext_id FROM listing WHERE shop=?", (code,))}
        prev = []
        for k in d["changed"] + d["gone"]:
            prev.append((iid, "listing", k, "changed" if k in p["recs"] else "gone",
                         json.dumps(_snapshot(ids[k]), ensure_ascii=False)))
        for k in d["added"]:
            prev.append((iid, "listing", k, "added", None))
        for k in d["m_changed"] + d["m_gone"]:
            r = dict(c.execute("SELECT * FROM shop_option_master WHERE shop=? AND ext_id=?", (code, k)).fetchone())
            prev.append((iid, "shop_option_master", k, "changed" if k in p["masters"] else "gone", json.dumps(r, ensure_ascii=False)))
        for k in d["m_added"]:
            prev.append((iid, "shop_option_master", k, "added", None))
        c.executemany("INSERT INTO listing_import_prev (import_id,tbl,ext_id,action,before_json) VALUES (?,?,?,?,?)", prev)
        for k in d["added"] + d["changed"]:
            r = p["recs"][k]
            vals = (r["ext_sub"], r["title"], r["price"], r["state"], r["state_raw"], r["sale_from"], r["sale_to"],
                    r["category"], r["layout"], _j(r["raw"]), r["hash"])
            if k in ids:
                c.execute("UPDATE listing SET ext_sub=?,title=?,price=?,state=?,state_raw=?,sale_from=?,sale_to=?,category=?,"
                          "layout=?,raw_json=?,row_hash=?,last_import_id=?,gone_import_id=NULL WHERE id=?", vals + (iid, ids[k]))
                lid = ids[k]
            else:
                lid = c.execute("INSERT INTO listing (shop,ext_id,ext_sub,title,price,state,state_raw,sale_from,sale_to,category,"
                                "layout,raw_json,row_hash,first_import_id,last_import_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (code, k) + vals + (iid, iid)).lastrowid
            _write_options(c, lid, r["options"])
        if d["same"]:
            c.executemany("UPDATE listing SET last_import_id=? WHERE id=?", [(iid, ids[k]) for k in d["same"]])
        c.executemany("UPDATE listing SET gone_import_id=? WHERE id=?", [(iid, ids[k]) for k in d["gone"]])
        for k in d["m_added"] + d["m_changed"]:
            m = p["masters"][k]
            c.execute("INSERT INTO shop_option_master (shop,ext_id,name,raw_json,row_hash,first_import_id,last_import_id) "
                      "VALUES (?,?,?,?,?,?,?) ON CONFLICT(shop,ext_id) DO UPDATE SET name=excluded.name, raw_json=excluded.raw_json,"
                      " row_hash=excluded.row_hash, last_import_id=excluded.last_import_id, gone_import_id=NULL",
                      (code, k, m["name"], _j(m["raw"]), m["hash"], iid, iid))
        c.executemany("UPDATE shop_option_master SET gone_import_id=? WHERE shop=? AND ext_id=?",
                      [(iid, code, k) for k in d["m_gone"]])
    store.audit(by, "listing.import", code, {"import_id": iid, "counts": counts,
                                             "files": [{"name": f["name"], "sha256": f.get("sha256")} for f in files]})
    return {"import_id": iid, "counts": counts}


def undo(iid: int, by: str) -> dict:
    """その店の**最後の**取り込みだけを戻す（間の取り込みを飛ばして戻すと、前の値がずれる）。"""
    from app import gate
    if "admin" not in gate.roles_of(by):
        raise PermissionError("取り消せるのは管理者だけです")
    imp = store.one("SELECT * FROM listing_import WHERE id=?", (iid,))
    if imp is None:
        raise LookupError("取り込みの記録がありません")
    if imp["state"] != "適用":
        raise ValueError("もう取り消してあります")
    last = store.val("SELECT MAX(id) FROM listing_import WHERE shop=? AND state='適用'", (imp["shop"],))
    if last != iid:
        raise ValueError(f"この店の最後の取り込み（#{last}）から順に取り消してください")
    code = imp["shop"]
    n = {"removed": 0, "restored": 0}
    with store.tx() as c:
        for r in c.execute("SELECT * FROM listing_import_prev WHERE import_id=?", (iid,)).fetchall():
            if r["tbl"] == "listing":
                if r["action"] == "added":
                    lid = c.execute("SELECT id FROM listing WHERE shop=? AND ext_id=?", (code, r["ext_id"])).fetchone()[0]
                    c.execute("DELETE FROM listing_option WHERE listing_id=?", (lid,))
                    c.execute("DELETE FROM listing WHERE id=?", (lid,))
                    n["removed"] += 1
                else:
                    b = json.loads(r["before_json"])
                    opts = b.pop("options")
                    cols = [k for k in b if k != "id"]
                    c.execute(f"UPDATE listing SET {', '.join(k + '=?' for k in cols)} WHERE id=?", [b[k] for k in cols] + [b["id"]])
                    c.execute("DELETE FROM listing_option WHERE listing_id=?", (b["id"],))
                    c.executemany("INSERT INTO listing_option (listing_id,src,item_name,value,code,price,sort,raw_json,def_value_id) "
                                  "VALUES (?,?,?,?,?,?,?,?,?)",
                                  [(b["id"], o["src"], o["item_name"], o["value"], o["code"], o["price"], o["sort"], o["raw_json"],
                                    o["def_value_id"]) for o in opts])
                    n["restored"] += 1
            else:
                if r["action"] == "added":
                    c.execute("DELETE FROM shop_option_master WHERE shop=? AND ext_id=?", (code, r["ext_id"]))
                else:
                    b = json.loads(r["before_json"])
                    cols = [k for k in b if k != "id"]
                    c.execute(f"UPDATE shop_option_master SET {', '.join(k + '=?' for k in cols)} WHERE id=?",
                              [b[k] for k in cols] + [b["id"]])
        # 「同じ」で last_import_id だけ進めた行は、ひとつ前の取り込みへ戻す
        prev_iid = store.val("SELECT MAX(id) FROM listing_import WHERE shop=? AND state='適用' AND id<?", (code, iid))
        c.execute("UPDATE listing SET last_import_id=? WHERE shop=? AND last_import_id=?", (prev_iid, code, iid))
        c.execute("UPDATE listing_import SET state='取り消し', undone_by=?, undone_at=? WHERE id=?", (by, store.now_s(), iid))
    store.audit(by, "listing.undo", code, {"import_id": iid, **n})
    return n


# ── 見る（段階1は見るだけ）──────────────────────────────────
def summary() -> dict:
    out = []
    for s in shops():
        c = {r["state"]: r["n"] for r in store.q("SELECT state, COUNT(*) n FROM listing WHERE shop=? AND gone_import_id IS NULL "
                                                 "GROUP BY state", (s["code"],))}
        last = store.one("SELECT id, at, by_user, counts_json FROM listing_import WHERE shop=? AND state='適用' ORDER BY id DESC LIMIT 1",
                         (s["code"],))
        out.append({**s, "counts": c, "total": sum(c.values()),
                    "gone": store.val("SELECT COUNT(*) FROM listing WHERE shop=? AND gone_import_id IS NOT NULL", (s["code"],)),
                    "linked": store.val("SELECT COUNT(*) FROM listing WHERE shop=? AND sellable_id IS NOT NULL AND gone_import_id IS NULL",
                                        (s["code"],)),
                    "last_import": dict(last) if last else None,
                    "state_checked": s["cart"] in lp.STATE_CHECKED})
    return {"shops": out}


def listings(args: dict) -> dict:
    where, ps = ["1=1"], []
    code = args.get("shop") or ""
    if code:
        shop(code)
        where.append("l.shop=?"); ps.append(code)
    st = args.get("state") or ""
    if st == "消えた":
        where.append("l.gone_import_id IS NOT NULL")
    else:
        where.append("l.gone_import_id IS NULL")
        if st in ("公開", "非公開", "そのほか"):
            where.append("l.state=?"); ps.append(st)
    lk = args.get("linked") or ""
    if lk == "yes":
        where.append("l.sellable_id IS NOT NULL")
    elif lk == "no":
        where.append("l.sellable_id IS NULL")
    for w in (args.get("q") or "").split():
        where.append("(l.ext_id LIKE ? OR l.ext_sub LIKE ? OR l.title LIKE ?)"); ps += [f"%{w}%"] * 3
    page = max(1, int(args.get("page") or 1))
    w = " AND ".join(where)
    total = store.val(f"SELECT COUNT(*) FROM listing l WHERE {w}", ps)
    rs = store.rows(store.q(
        f"SELECT l.id, l.shop, s.label AS shop_label, l.ext_id, l.ext_sub, l.title, l.price, l.state, l.layout, l.category, "
        f"l.sellable_id, l.gone_import_id, i.at AS last_at, (SELECT COUNT(*) FROM listing_option o WHERE o.listing_id=l.id) AS n_options "
        f"FROM listing l JOIN shop s ON s.code=l.shop LEFT JOIN listing_import i ON i.id=l.last_import_id "
        f"WHERE {w} ORDER BY s.sort, l.ext_id LIMIT ? OFFSET ?", ps + [PER_PAGE, (page - 1) * PER_PAGE]))
    return {"rows": rs, "total": total, "page": page, "per_page": PER_PAGE, "pages": max(1, -(-total // PER_PAGE))}


def detail(lid: int) -> dict:
    r = store.one("SELECT l.*, s.label AS shop_label, s.id_label FROM listing l JOIN shop s ON s.code=l.shop WHERE l.id=?", (lid,))
    if r is None:
        raise LookupError("出品がありません")
    d = dict(r)
    d["raw"] = json.loads(d.pop("raw_json"))
    d["options"] = [dict(o, raw=json.loads(o.pop("raw_json"))) for o in store.rows(store.q(
        "SELECT id, src, item_name, value, code, price, sort, raw_json FROM listing_option WHERE listing_id=? ORDER BY src, sort, id",
        (lid,)))]
    d["imports"] = store.rows(store.q("SELECT id, at, by_user, state FROM listing_import WHERE id IN (?,?,?)",
                                      (d["first_import_id"], d["last_import_id"], d["gone_import_id"] or 0)))
    return d


def imports(code: str = "") -> list[dict]:
    rs = store.rows(store.q("SELECT i.*, s.label AS shop_label FROM listing_import i JOIN shop s ON s.code=i.shop "
                            + ("WHERE i.shop=? " if code else "") + "ORDER BY i.id DESC LIMIT 100", (code,) if code else ()))
    for r in rs:
        r["counts"] = json.loads(r.pop("counts_json"))
        r["files"] = json.loads(r.pop("files_json"))
    return rs
