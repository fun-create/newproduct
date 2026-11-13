#!/usr/bin/env python3
"""
楽天・Yahoo の登録用ファイルの書き出しと往復の検査（段階6の下ごしらえ・ADR-153・FR-292・295）。

- **列の並びは取り込んだ出力ファイルの並び**（`export_layout`）。説明文・画像・在庫の列は書き出さない
- 楽天（商品一括編集）は1つのファイルに 商品の行・商品オプションの行・SKU の行 が混ざる。台帳では商品オプションを
  値ごとの行に分けて持っているので、書き出すときに「商品オプション選択肢N」の列へ戻す（N は値の並び順＝取り込んだ列の番号）
- Yahoo（商品データ）は1出品1行。options 列も元の文字のまま持っている
- ギフトモールは4種（商品・規格・商品へのオプション付け・オプションのマスタ）。オプション付けは、台帳ではマスタの選択肢ごとの行に
  分けて持っているので、1つ（商品 × オプションID）に戻す。マスタは `shop_option_master` の元の行
- **取り込みの形（操作の列・必須の列）は未確認**。往復の検査で中身が戻ることだけを確かめる。店には入れない
"""
from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path

from app import listing_parse as lp
from app import store

CARTS = ("rakuten", "yahoo", "giftmall")
UNVERIFIED = "楽天・Yahoo・ギフトモールの取り込みの形（操作の列・必須の列）は未確認です。往復の検査は中身が戻ることだけを確かめます"


def _shop(shop: str) -> dict:
    s = store.one("SELECT * FROM shop WHERE code=?", (shop,))
    if s is None:
        raise LookupError(f"店がありません: {shop}")
    if s["cart"] not in CARTS:
        raise ValueError("楽天・Yahoo の店ではありません")
    return dict(s)


def _rakuten_rows(lid: int, listing_raw: dict) -> list[dict]:
    out = [listing_raw]
    groups = {}
    for o in store.q("SELECT src, value, sort, raw_json FROM listing_option WHERE listing_id=? ORDER BY id", (lid,)):
        raw = json.loads(o["raw_json"])
        if o["src"] == "rakuten_sku":
            out.append(raw)
            continue
        if o["src"] != "rakuten_option":
            continue
        base = {k: v for k, v in raw.items() if k != "値"}
        key = json.dumps(base, ensure_ascii=False, sort_keys=True)
        g = groups.setdefault(key, dict(base))
        if "値" in raw:
            g[f"商品オプション選択肢{o['sort']}"] = raw["値"]
    # 商品の行 → 商品オプションの行 → SKU の行（店の出力と同じ並びのまとまり）
    skus = out[1:]
    return [listing_raw] + list(groups.values()) + skus


def gm_rows(shop: str, kind: str) -> list[dict]:
    """ギフトモールの種類ごとの行（item / class / option / master）。"""
    if kind == "master":
        return [json.loads(r["raw_json"]) for r in store.q("SELECT raw_json FROM shop_option_master WHERE shop=? AND gone_import_id IS NULL ORDER BY id", (shop,))]
    if kind == "item":
        return [json.loads(r["raw_json"]) for r in store.q("SELECT raw_json FROM listing WHERE shop=? AND gone_import_id IS NULL ORDER BY id", (shop,))]
    src = {"class": "gm_class", "option": "gm_option"}[kind]
    out, seen = [], set()
    for r in store.q("SELECT o.listing_id, o.raw_json FROM listing_option o JOIN listing l ON l.id=o.listing_id "
                     "WHERE l.shop=? AND l.gone_import_id IS NULL AND o.src=? ORDER BY o.id", (shop, src)):
        raw = {k: v for k, v in json.loads(r["raw_json"]).items() if k != "値"}
        if kind == "option":
            k = (r["listing_id"], raw.get("オプションID"))
            if k in seen:
                continue
            seen.add(k)
        out.append(raw)
    return out


def rows(shop: str, listing_ids: list[int] | None = None) -> list[dict]:
    s = _shop(shop)
    where, ps = "shop=? AND gone_import_id IS NULL", [shop]
    if listing_ids is not None:
        if not listing_ids:
            return []
        where += f" AND id IN ({','.join('?' * len(listing_ids))})"
        ps += list(listing_ids)
    out = []
    for r in store.q(f"SELECT id, raw_json FROM listing WHERE {where} ORDER BY id", ps):
        raw = json.loads(r["raw_json"])
        out += _rakuten_rows(r["id"], raw) if s["cart"] == "rakuten" else [raw]
    return out


def to_csv(shop: str, rs: list[dict], kind: str = "item") -> bytes:
    _shop(shop)
    lay = store.one("SELECT columns_json FROM export_layout WHERE shop=? AND kind=?", (shop, kind))
    if lay is None:
        raise LookupError(f"{shop} の列の並びがありません（店の出力ファイルを一度取り込むと覚えます）")
    cols = json.loads(lay["columns_json"])
    b = io.StringIO()
    w = csv.writer(b, lineterminator="\r\n")
    w.writerow(cols)
    for r in rs:
        extra = set(r) - set(cols)
        if extra:
            raise ValueError(f"列の並びに無い列があります: {', '.join(sorted(extra))[:200]}")
        w.writerow([r.get(c, "") for c in cols])
    return b.getvalue().encode("cp932")


def roundtrip(shop: str, paths) -> dict:
    """台帳から書き出した中身と、取り込んだ元のファイル（落とした列・空の欄・専用ページを除く）を、行の順を問わず比べる。"""
    s = _shop(shop)
    files = []
    for x in paths:
        x = Path(x)
        files += sorted(y for y in x.iterdir() if y.is_file()) if x.is_dir() else [x]
    p = lp.parse(s["cart"], files)
    excluded = set(p.get("excluded") or [])
    key = "商品管理番号（商品URL）" if s["cart"] == "rakuten" else "code"
    orig = []
    for f in files:
        kind, _ = lp.kind_of(s["cart"], f.name)
        if kind != "item":
            continue
        rs, _meta = lp._read(s["cart"], f, kind)
        orig += [r for r in rs if r.get(key) not in excluded]
    if s["cart"] == "giftmall":
        return _gm_roundtrip(shop, files, excluded)
    mine = rows(shop)
    a = Counter(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in orig)
    b = Counter(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in mine)
    back = list(csv.DictReader(io.StringIO(to_csv(shop, mine).decode("cp932"), newline="")))
    back = Counter(json.dumps({k: v for k, v in r.items() if v != ""}, ensure_ascii=False, sort_keys=True) for r in back)
    return {"shop": shop, "excluded": len(excluded), "orig": sum(a.values()), "mine": sum(b.values()),
            "only_in_file": sum((a - b).values()), "only_in_ledger": sum((b - a).values()),
            "csv_reread_same": back == b, "ok": a == b and back == b}


def _gm_roundtrip(shop: str, files: list[Path], excluded: set) -> dict:
    kinds = {}
    for f in files:
        kind, _ = lp.kind_of("giftmall", f.name)
        if kind is None:
            continue
        orig, _meta = lp._read("giftmall", f, kind)
        if kind != "master":
            orig = [r for r in orig if r.get("商品ID") not in excluded]
        mine = gm_rows(shop, kind)
        a = Counter(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in orig)
        b = Counter(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in mine)
        back = list(csv.DictReader(io.StringIO(to_csv(shop, mine, kind).decode("cp932"), newline="")))
        back = Counter(json.dumps({k: v for k, v in r.items() if v != ""}, ensure_ascii=False, sort_keys=True) for r in back)
        kinds[kind] = {"file": f.name, "orig": sum(a.values()), "mine": sum(b.values()), "only_in_file": sum((a - b).values()),
                       "only_in_ledger": sum((b - a).values()), "csv_reread_same": back == b, "same": a == b}
    return {"shop": shop, "excluded": len(excluded), "kinds": kinds,
            "ok": bool(kinds) and all(v["same"] and v["csv_reread_same"] for v in kinds.values())}
