#!/usr/bin/env python3
"""
出品と CIP の「作るもの」（共通商品コード）を結ぶ（段階2・ADR-146・FR-280〜286 の一部・PL-56）。

- 種は CIP の店コードの対応（`seisan.store_codes()`・読み取りだけ）。**CIP には何も書かない**
- 店ごとに、CIP の店コードが台帳のどの識別子に当たるかは 2026-11-13 本番で数えて決めた（`MATCH`）:
  自社 EC は商品番号（URLコードとほぼ同じ）、楽天は商品番号と SKU、Yahoo は code、Amazon は seller-sku、ギフトモールは規格の商品コード
- **毎回まるごと作り直す**（CIP の対応表が正。こちらで直した結び付けは持たない＝段階2の範囲では人が直す口は無い）
- 1つの店コードが複数の共通商品コードに割れていれば、そのまま複数行で持つ
- 持つのはコードと分類・形・サイズ・入数だけ（CIP の商品名・店別の仕様は持たない・CLAUDE.md §3-1）
- アットフレーム・overseas は CIP の対応表に無い（結べない）。Amazon の FBA（amazon-fba）は amazon に寄せる
"""
from __future__ import annotations

import json

from app import store

# CIP の店名 → (台帳の店, 当てる識別子の順)
MATCH = {
    "グッズ本店": (["goods"], ["ext_sub", "ext_id"]),
    "うちわ本店": (["uchiwa"], ["ext_sub", "ext_id"]),
    "楽天": (["rakuten"], ["ext_sub", "ext_id", "option"]),
    "Yahoo": (["yahoo_goods", "yahoo_frame"], ["ext_id"]),
    "amazon": (["amazon"], ["ext_id"]),
    "amazon-fba": (["amazon"], ["ext_id"]),
    "ギフトモール": (["giftmall"], ["option"]),
}
OPTION_SRC = {"rakuten": ("rakuten_sku",), "giftmall": ("gm_class",)}


def build(items: list[dict]) -> tuple[list[tuple], dict]:
    """対応表から結び付けの行を作る。返すもの: 行の並びと、CIP の店ごとの {codes, linked, unmatched}。"""
    rows, per = [], {}
    idx = {}
    for cs, (shops, how) in MATCH.items():
        q = ",".join("?" * len(shops))
        ix = {"ext_id": {}, "ext_sub": {}, "option": {}}
        for r in store.q(f"SELECT id, ext_id, ext_sub FROM listing WHERE shop IN ({q}) AND gone_import_id IS NULL", shops):
            ix["ext_id"].setdefault(r["ext_id"], []).append((r["id"], None))
            if r["ext_sub"]:
                ix["ext_sub"].setdefault(r["ext_sub"], []).append((r["id"], None))
        srcs = sorted({s for sh in shops for s in OPTION_SRC.get(sh, ())})
        if srcs:
            for r in store.q(f"SELECT o.id, o.listing_id, o.code FROM listing_option o JOIN listing l ON l.id=o.listing_id "
                             f"WHERE l.shop IN ({q}) AND l.gone_import_id IS NULL AND o.code IS NOT NULL "
                             f"AND o.src IN ({','.join('?' * len(srcs))})", shops + srcs):
                ix["option"].setdefault(r["code"], []).append((r["listing_id"], r["id"]))
        idx[cs] = (how, ix)
    for it in items:
        cs = it.get("store")
        if cs not in idx:
            per.setdefault(cs or "（空）", {"codes": 0, "linked": 0, "unmatched": 0, "unknown_store": True})["codes"] += 1
            continue
        how, ix = idx[cs]
        p = per.setdefault(cs, {"codes": 0, "linked": 0, "unmatched": 0})
        p["codes"] += 1
        hits, by = [], None
        for h in how:
            hits = ix[h].get(it["store_code"]) or []
            if hits:
                by = h
                break
        if not hits:
            p["unmatched"] += 1
            continue
        p["linked"] += 1
        for lid, oid in hits:
            rows.append((lid, oid, cs, it["store_code"], it["product_code"], by, it.get("cat1"), it.get("cat2"), it.get("cat3"),
                         it.get("shape"), it.get("size"), it.get("pack_qty")))
    return rows, per


def run(apply: bool, by: str, fetch=None) -> dict:
    from app import gate, listing, seisan
    if apply and not listing.IMPORT_ROLES & set(gate.roles_of(by)):
        raise PermissionError("結び直せるのは社長の業務ロールの人だけです")
    j = (fetch or seisan.store_codes)()
    rows, per = build(j.get("items") or [])
    listings = len({r[0] for r in rows})
    counts = {"cip_rows": len(j.get("items") or []), "link_rows": len(rows), "listings": listings, "per_store": per,
              "product_codes": len({r[4] for r in rows})}
    if apply:
        now = store.now_s()
        with store.tx() as c:
            c.execute("DELETE FROM listing_cip_link")
            c.executemany("INSERT INTO listing_cip_link (listing_id,option_id,cip_store,store_code,product_code,matched_by,"
                          "cat1,cat2,cat3,shape,size,pack_qty,as_of,linked_at,linked_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          [r + (j.get("as_of"), now, by) for r in rows])
            c.execute("INSERT INTO listing_cip_run (as_of,counts_json,by_user,at) VALUES (?,?,?,?)",
                      (j.get("as_of"), json.dumps(counts, ensure_ascii=False), by, now))
        store.audit(by, "listing.cip_link", "", {k: v for k, v in counts.items() if k != "per_store"})
    return {"as_of": j.get("as_of"), "applied": apply, **counts}


def last_run() -> dict | None:
    r = store.one("SELECT * FROM listing_cip_run ORDER BY id DESC LIMIT 1")
    if r is None:
        return None
    d = dict(r)
    d["counts"] = json.loads(d.pop("counts_json"))
    return d
