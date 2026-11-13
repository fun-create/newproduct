#!/usr/bin/env python3
"""
FutureShop の登録用ファイルの書き出しと、書き出す前の検査（段階4・ADR-151・FR-291〜294）。

- **列の並びは、取り込んだ出力ファイルの並び**（`export_layout`）。説明文・画像・在庫の列は書き出さない
  （取り込んでいない。既存商品の説明文と画像は店の値のまま＝11-12 20:57 十文字さん）
- **取り込みの形（必須の列・コントロールカラムの書き方）が出力の形と同じかは未確認**。管理画面で試しの1件を入れて確かめるまで、
  この書き出しを店に入れない（`UNVERIFIED` を画面と記録に出す）
- 往復の検査（FR-294）: 台帳から書き出したものが、取り込んだ元のファイル（落とした列を除く）と同じ中身になるか。
  行の順は問わない（台帳は出品ごとに選択肢をまとめて持つため）
- 書き出す前の検査（FR-291）: 必須の欄・ステータスの値・価格・Shift_JIS で書けない文字・識別子の重なり・
  カートの決まり（`cart_rule` の `max_chars:<列>`）・CIP と結んだか（知らせだけ）
- お客さまの情報は扱わない（台帳に無い）
"""
from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path

from app import listing_parse as lp
from app import store

UNVERIFIED = ("FutureShop の取り込みの形は、管理画面で試しの1件を入れて確かめるまで未確認です"
              "（マニュアルの標準CSV は UTF-8 だが、うちの店の出力は Shift_JIS。見出しは店の出力と同じ並びで、取り込まない列は空にしている）")
KIND_SRC = {"selection": "selection", "opt_basic": "opt_basic", "opt_select": "opt_select"}
KINDS = ("goods", "selection", "opt_basic", "opt_select")
FILE_PREFIX = {"goods": "ccGoods", "selection": "goodsSelection", "opt_basic": "goodsOptionPriceBasic",
               "opt_select": "goodsOptionPriceSelection"}


def _shop_fs(shop: str) -> dict:
    s = store.one("SELECT * FROM shop WHERE code=?", (shop,))
    if s is None:
        raise LookupError(f"店がありません: {shop}")
    if s["cart"] != "futureshop":
        raise ValueError("FutureShop の店ではありません")
    return dict(s)


def layout(shop: str, kind: str) -> list[str]:
    """書き出しの見出し。**落とした列も含めた店の出力と同じ並び**（ADR-154: 見出しが管理画面の設定と違うと取り込めない。
    更新で空の欄は「変えない」）。全体の並びを覚える前に取り込んだ店は、取り込んだ列だけ（取り込み直すと全体になる）。"""
    r = store.one("SELECT columns_json, full_json FROM export_layout WHERE shop=? AND kind=?", (shop, kind))
    if r is None:
        raise LookupError(f"{shop} の {FILE_PREFIX[kind]} の列の並びがありません（店の出力ファイルを一度取り込むと覚えます）")
    return json.loads(r["full_json"] or r["columns_json"])


def encoding(shop: str, kind: str) -> str:
    r = store.one("SELECT encoding FROM export_layout WHERE shop=? AND kind=?", (shop, kind))
    return "utf-8-sig" if r and r["encoding"].startswith("utf-8") else "cp932"


def _rules() -> dict:
    return {r["rule_key"]: r["value"] for r in store.q("SELECT rule_key, value FROM cart_rule WHERE cart='futureshop'")}


def rows(shop: str, kind: str, listing_ids: list[int] | None = None) -> list[dict]:
    """台帳から、その種類のファイルの行を作る（列名 → 値）。listing_ids を渡せばその出品だけ。"""
    _shop_fs(shop)
    where, ps = "l.shop=? AND l.gone_import_id IS NULL", [shop]
    if listing_ids is not None:
        if not listing_ids:
            return []
        where += f" AND l.id IN ({','.join('?' * len(listing_ids))})"
        ps += list(listing_ids)
    if kind == "goods":
        return [json.loads(r["raw_json"]) for r in store.q(f"SELECT l.raw_json FROM listing l WHERE {where} ORDER BY l.id", ps)]
    src = KIND_SRC[kind]
    return [json.loads(r["raw_json"]) for r in store.q(
        f"SELECT o.raw_json FROM listing_option o JOIN listing l ON l.id=o.listing_id WHERE {where} AND o.src=? ORDER BY l.id, o.id",
        ps + [src])]


def to_csv(shop: str, kind: str, rs: list[dict], control: str | None = None) -> bytes:
    """店の出力と同じ見出し・同じ文字コード（うちの店は Shift_JIS）・CRLF で書く。取り込まない列は空。
    control を渡すとコントロールカラムに入れる（n 新規・u 更新）。書けない文字があれば止める（検査で先に見つける）。"""
    cols = layout(shop, kind)
    if control is not None:
        allowed = (_rules().get(f"control:{kind}") or "").split(",")
        if control not in allowed:
            raise ValueError(f"{FILE_PREFIX[kind]} のコントロールカラムに「{control}」は使えません（使えるのは {'・'.join(x for x in allowed if x) or 'なし'}）")
    b = io.StringIO()
    w = csv.writer(b, lineterminator="\r\n")
    w.writerow(cols)
    for r in rs:
        row = dict(r)
        if control is not None:
            row["コントロールカラム"] = control
        w.writerow([row.get(c, "") for c in cols])
    return b.getvalue().encode(encoding(shop, kind))


def check(shop: str, listing_ids: list[int] | None = None) -> dict:
    """書き出す前の検査。errors は書き出しを止めるもの、warnings は知らせだけ。どちらも件数と識別子（値は出さない）。"""
    _shop_fs(shop)
    errors, warnings = [], []
    rules = _rules()
    goods = rows(shop, "goods", listing_ids)
    ids = Counter(g.get("商品URLコード", "") for g in goods)
    for k, n in ids.items():
        if n > 1:
            errors.append(f"商品URLコードが重なっています: {k}（{n} 件）")
    for g in goods:
        key = g.get("商品URLコード") or "（商品URLコードなし）"
        for col in ("商品URLコード", "商品名", "ステータス"):
            if not (g.get(col) or "").strip():
                errors.append(f"{key}: {col} が空です")
        if g.get("ステータス") not in (None, "", "0", "1"):
            errors.append(f"{key}: ステータスは 0（公開）か 1（非公開）です")
        price = (g.get("本体価格") or "").replace(",", "")
        if price and not price.isdigit():
            errors.append(f"{key}: 本体価格が数ではありません")
    import re as _re
    for kind in KINDS:
        rs = rows(shop, kind, listing_ids)
        mr = rules.get(f"max_rows:{kind}")
        if mr and len(rs) > int(mr):
            errors.append(f"{FILE_PREFIX[kind]} が {len(rs)} 行で、1ファイルの上限 {mr} 行を超えます（分けて出してください）")
        if kind == "opt_basic" and rules.get("max_items:opt_basic"):
            per = Counter(r.get("商品URLコード") for r in rs)
            for k, n in per.items():
                if n > int(rules["max_items:opt_basic"]):
                    errors.append(f"{k}: オプション項目が {n} あり、上限 {rules['max_items:opt_basic']} を超えます")
        enc = encoding(shop, kind) if store.one("SELECT 1 FROM export_layout WHERE shop=? AND kind=?", (shop, kind)) else "cp932"
        for r in rs:
            key = r.get("商品URLコード") or "?"
            for col, v in r.items():
                v = str(v)
                try:
                    v.encode(enc)
                except UnicodeEncodeError:
                    errors.append(f"{key}: {col} に {'Shift_JIS' if enc == 'cp932' else enc} で書けない文字があります（{FILE_PREFIX[kind]}）")
            for rk, rv in rules.items():
                parts = rk.split(":", 2)
                if len(parts) == 2 and parts[0] == "max_chars" and kind == "goods":
                    parts = ["max_chars", "goods", parts[1]]          # 古い形（列だけ）は商品ファイル
                if len(parts) != 3 or parts[1] != kind:
                    continue
                typ, _, col = parts
                v = str(r.get(col) or "")
                if not v and typ != "pattern":
                    continue
                if typ == "max_chars" and len(v) > int(rv):
                    errors.append(f"{key}: {col} が {rv} 文字を超えています（{len(v)} 文字）")
                elif typ == "pattern" and v and not _re.fullmatch(rv, v):
                    errors.append(f"{key}: {col} の形が決まりに合いません（{rv}）")
                elif typ == "forbid" and any(ch in v for ch in rv):
                    errors.append(f"{key}: {col} に使えない文字（{' '.join(rv)}）があります")
                elif typ == "only_br" and _re.search(r"<(?!br\s*/?>)[a-zA-Z/!]", v, _re.I):
                    errors.append(f"{key}: {col} に <br> 以外の HTML があります")
    where, ps = "l.shop=? AND l.gone_import_id IS NULL", [shop]
    if listing_ids is not None and listing_ids:
        where += f" AND l.id IN ({','.join('?' * len(listing_ids))})"
        ps += list(listing_ids)
    n_nolink = store.val(f"SELECT COUNT(*) FROM listing l WHERE {where} AND NOT EXISTS (SELECT 1 FROM listing_cip_link k WHERE k.listing_id=l.id)", ps)
    if n_nolink:
        warnings.append(f"CIP の商品と結んでいない出品が {n_nolink} 件あります（注文が来ても作るものが決まりません）")
    if not rules:
        warnings.append("カートの決まり（文字数の上限など）がまだ表にありません。文字数は検査していません")
    elif rules.get("no_update:selection"):
        warnings.append("項目選択肢は更新できません（n 新規・d 削除だけ）。既存商品の更新では項目選択肢のファイルを出しません")
    warnings.append(UNVERIFIED)
    return {"listings": len(goods), "errors": errors, "warnings": warnings}


def roundtrip(shop: str, paths) -> dict:
    """往復の検査（FR-294）: 台帳から書き出した中身と、元のファイルの中身（落とした列・空の欄を除く）を比べる。
    専用ページとして外した出品（PL-64）は元のほうからも除く。行の順は問わない。"""
    _shop_fs(shop)
    files = []
    for x in paths:
        x = Path(x)
        files += sorted(y for y in x.iterdir() if y.is_file()) if x.is_dir() else [x]
    p = lp.parse("futureshop", files)
    excluded = set(p.get("excluded") or [])
    out = {}
    for f in files:
        kind, _ = lp.kind_of("futureshop", f.name)
        if kind is None:
            continue
        orig, _meta = lp._read("futureshop", f, kind)
        orig = [r for r in orig if r.get("商品URLコード") not in excluded]
        mine = rows(shop, kind)
        a = Counter(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in orig)
        b = Counter(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in mine)
        only_orig, only_mine = a - b, b - a
        out[kind] = {"file": f.name, "orig": sum(a.values()), "mine": sum(b.values()),
                     "only_in_file": sum(only_orig.values()), "only_in_ledger": sum(only_mine.values()), "same": only_orig == only_mine == Counter()}
        # 書いたファイルを読み直しても同じか（Shift_JIS・引用符・改行の往復）
        data = to_csv(shop, kind, mine)
        back = list(csv.DictReader(io.StringIO(data.decode("cp932"), newline="")))
        back = [{k: v for k, v in r.items() if v != ""} for r in back]
        out[kind]["csv_reread_same"] = Counter(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in back) == b
    return {"shop": shop, "excluded": len(excluded), "kinds": out, "ok": all(v["same"] and v["csv_reread_same"] for v in out.values())}
