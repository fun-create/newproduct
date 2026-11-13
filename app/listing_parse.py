#!/usr/bin/env python3
"""
店ごとの出力ファイルを読む（段階1・FR-287〜289・ADR-141）。**書かない。**読んで形をそろえるだけ。

- 9店・6カート（FutureShop・楽天・Yahoo・ギフトモール・Amazon・Shopify）の出力の形を、1つの形にする
  （出品1件 = `Rec`。付いている選択肢は元のファイルの1行ずつ）
- **既存商品の説明文と画像は取り込まない**（2026-11-12 20:57 十文字さん）。`DROP` の列は読んだ時点で捨てる
- **在庫の数は持たない**（CIP の範囲）。毎回変わり、変わっていない出品まで「変わった」に数えてしまう
- 件数の突き合わせ: どのファイルも「読んだ行 = 使った行 + 親の無い行 + 読まなかった行」になることを返す（段階1の終わりの条件）
- お客さまの情報は入っていない前提だが、その前提は取り込む側（`app/listing.py`）がメールアドレス・電話番号の形で確かめる

ファイルの見分けはファイル名の頭で行う（店が付ける名前のまま）。知らないファイルは「読まなかった」に理由つきで出す。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

csv.field_size_limit(10 ** 9)

# 公開・非公開の読み方。**出どころ**: futureshop は 2026-11-12 EC統合が公開ページで確かめた（0=公開・1=非公開）。
# それ以外はカートの仕様（楽天の倉庫指定 1=倉庫＝非公開、Yahoo の display 1=公開、Amazon の status、Shopify の Status、
# ギフトモールは語そのまま）。**画面で確かめていないものは STATE_CHECKED に入れない**
STATE_MAP = {
    "futureshop": {"0": "公開", "1": "非公開"},
    "rakuten": {"0": "公開", "1": "非公開"},
    "yahoo": {"1": "公開", "0": "非公開"},
    "giftmall": {"公開": "公開", "非公開": "非公開"},
    "amazon": {"Active": "公開", "Inactive": "非公開"},
    "shopify": {"active": "公開", "draft": "非公開", "archived": "非公開"},
}
STATE_CHECKED = {"futureshop", "giftmall"}

# 落とす列（説明文・画像・在庫の数）。正規表現は列名の全体に当てる
_STOCK = r"(現在在庫数|調整在庫数|在庫数|お気に入り登録数|quantity|pending-quantity|Variant Inventory Qty)"
DROP = {
    "futureshop": r"(商品説明（大）|商品説明（小）|独自コメント（\d+）|商品一言説明\(コマースクリエイター\)|外部連携商品説明|"
                  r"Description\(コマースクリエイター\)|説明文（PC）.*|説明文（モバイル）.*|" + _STOCK + ")",
    "rakuten": r"(PC用商品説明文|スマートフォン用商品説明文|PC用販売説明文|商品画像.*|ギフト用サムネイル画像\d+|SKU画像.*|動画.*|在庫数.*|"
               + _STOCK + ")",
    "yahoo": r"(caption|abstract|explanation|additional\d|sp-additional|meta-desc|item-image-urls|video|" + _STOCK + ")",
    "giftmall": r"(説明文|店舗メモ欄|備考|メッセージについて|注意点|配送について|包装について|納品の流れ|お召し上がり方|原材料|アレルギー|"
                + _STOCK + ")",
    "amazon": r"(item-note|" + _STOCK + ")",
    "shopify": r"(Body \(HTML\)|Image Src|Image Position|Image Alt Text|Variant Image|SEO Description|" + _STOCK + ")",
}

# ファイル名の頭 → 種類。None は読まない（理由つき）
KINDS = {
    "futureshop": [(r"^ccGoods_", "goods"), (r"^goodsSelection_", "selection"),
                   (r"^goodsOptionPriceBasic_", "opt_basic"), (r"^goodsOptionPriceSelection_", "opt_select")],
    "rakuten": [(r"^dl-normal-item", "item")],
    "yahoo": [(r"^data\d+", "item")],
    "giftmall": [(r"^item_\d", "item"), (r"^item_class_", "class"), (r"^item_option_", "option"),
                 (r"^option_master_", "master"),
                 (r"^class_category_master_", None, "規格分類のマスタ（選択肢の整理の段階3で使う）"),
                 (r"\.xlsx$", None, "表計算のファイルは読まない（オプション登録の手順のメモ）")],
    "amazon": [(r"出品商品レポート", "item"), (r"FBA在庫", None, "在庫は CIP の範囲"),
               (r"^\d+\.txt$", None, "在庫レポートと同じ列（在庫は CIP の範囲）")],
    "shopify": [(r"^products_export", "item")],
}
DELIM = {"amazon": "\t"}
REQUIRED = {
    ("futureshop", "goods"): ["商品URLコード", "ステータス", "商品名"],
    ("futureshop", "selection"): ["商品URLコード"], ("futureshop", "opt_basic"): ["商品URLコード"],
    ("futureshop", "opt_select"): ["商品URLコード"],
    ("rakuten", "item"): ["商品管理番号（商品URL）", "商品名", "倉庫指定"],
    ("yahoo", "item"): ["code", "name", "display"],
    ("giftmall", "item"): ["商品ID", "商品名", "公開・非公開"], ("giftmall", "class"): ["商品ID"],
    ("giftmall", "option"): ["商品ID", "オプションID"], ("giftmall", "master"): ["オプションID"],
    ("amazon", "item"): ["seller-sku", "item-name", "status"],
    ("shopify", "item"): ["Handle", "Title", "Status"],
}


class ParseError(ValueError):
    pass


def _decode(b: bytes) -> tuple[str, str]:
    for e in ("utf-8-sig", "cp932"):
        try:
            return b.decode(e), e
        except UnicodeDecodeError:
            pass
    raise ParseError("文字コードを読めません（UTF-8 でも Shift_JIS でもありません）")


def _int(s) -> int | None:
    s = re.sub(r"[,円\s]", "", str(s or ""))
    if re.fullmatch(r"-?\d+(\.0+)?", s):
        return int(float(s))
    return None


def kind_of(cart: str, name: str):
    for x in KINDS[cart]:
        if re.search(x[0], name):
            return x[1], (x[2] if len(x) > 2 else "")
    return None, "この店のファイルとして知らない名前"


def _read(cart: str, path: Path, kind: str):
    b = path.read_bytes()
    t, enc = _decode(b)
    rows = list(csv.reader(io.StringIO(t, newline=""), delimiter=DELIM.get(cart, ",")))
    if not rows:
        raise ParseError(f"{path.name}: 空のファイルです")
    head = rows[0]
    miss = [c for c in REQUIRED.get((cart, kind), []) if c not in head]
    if miss:
        raise ParseError(f"{path.name}: 要る列がありません: {'、'.join(miss)}")
    drop = re.compile(r"^" + DROP[cart] + r"$")
    keep = [i for i, c in enumerate(head) if not drop.match(c)]
    dropped = [c for c in head if drop.match(c)]
    out = []
    for r in rows[1:]:
        if not any(x.strip() for x in r):
            continue                                    # 空の行は数えない（末尾の改行など）
        d = {head[i]: r[i] for i in keep if i < len(r) and r[i] != ""}
        out.append(d)
    return out, {"name": path.name, "sha256": hashlib.sha256(b).hexdigest(), "encoding": enc,
                 "rows": len(out), "columns": len(head), "dropped_columns": dropped}


def _rec(ext_id, **f):
    return {"ext_id": ext_id, "ext_sub": f.get("ext_sub"), "title": f.get("title"), "price": f.get("price"),
            "state_raw": f.get("state_raw"), "sale_from": f.get("sale_from"), "sale_to": f.get("sale_to"),
            "category": f.get("category"), "layout": f.get("layout"), "raw": f.get("raw") or {}, "options": []}


def _opt(src, raw, item=None, value=None, code=None, price=None, sort=0):
    return {"src": src, "item_name": item or None, "value": value or None, "code": code or None,
            "price": price, "sort": sort, "raw": raw}


def parse(cart: str, paths: list[Path]) -> dict:
    """1店ぶんのファイルを読む。返すもの: recs（ext_id → 出品）・masters・files・orphans・errors。"""
    if cart not in KINDS:
        raise ParseError(f"知らないカートです: {cart}")
    files, data = [], {}
    for p in sorted(paths, key=lambda x: x.name):
        k, why = kind_of(cart, p.name)
        if k is None:
            files.append({"name": p.name, "kind": None, "rows": None, "used": False, "reason": why})
            continue
        rows, meta = _read(cart, p, k)
        meta.update(kind=k, used=True)
        files.append(meta)
        data.setdefault(k, []).extend((meta["name"], r) for r in rows)
    main = "goods" if cart == "futureshop" else "item"
    if main not in data:
        raise ParseError("出品のファイル（" + ("ccGoods_" if cart == "futureshop" else "商品のファイル") + "）がありません")
    recs, masters, orphans, accounted, errors = {}, {}, {}, {}, []
    sm = STATE_MAP[cart]

    def acc(fname, n=1):
        accounted[fname] = accounted.get(fname, 0) + n

    def attach(fname, ext_id, opt, kind):
        r = recs.get(ext_id)
        if r is None:
            orphans[kind] = orphans.get(kind, 0) + 1
            acc(fname)        # 数えたうえで、親の無い行として出す
            return
        opt["sort"] = opt["sort"] or len(r["options"])
        r["options"].append(opt)
        acc(fname)

    def put(fname, rec):
        if rec["ext_id"] in recs:
            errors.append(f"{fname}: 識別子が2回出てきます: {rec['ext_id']}")
        recs[rec["ext_id"]] = rec
        acc(fname)

    if cart == "futureshop":
        for fn, r in data["goods"]:
            put(fn, _rec(r["商品URLコード"], ext_sub=r.get("商品番号"), title=r.get("商品名"), price=_int(r.get("本体価格")),
                         state_raw=r.get("ステータス"), sale_from=r.get("販売期間(From)"), sale_to=r.get("販売期間(to)"),
                         category=r.get("メイングループ"), layout=r.get("レイアウト割当名"), raw=r))
        for fn, r in data.get("selection", []):
            attach(fn, r["商品URLコード"], _opt("selection", r, r.get("セレクト／ラジオボタン用項目名"),
                                                  r.get("セレクト／ラジオボタン用選択肢"), r.get("選択肢タイプ")), "selection")
        for fn, r in data.get("opt_basic", []):
            attach(fn, r["商品URLコード"], _opt("opt_basic", r, r.get("項目名【基本情報】"), sort=_int(r.get("表示順")) or 0),
                   "opt_basic")
        for fn, r in data.get("opt_select", []):
            pr = _int(r.get("価格【選択肢・価格設定】"))
            attach(fn, r["商品URLコード"], _opt("opt_select", r, r.get("項目名【基本情報】"), r.get("選択肢【選択肢・価格設定】"),
                                                  r.get("選択肢コード【選択肢・価格設定／価格設定】"),
                                                  pr if pr is not None else _int(r.get("価格【価格設定】")),
                                                  _int(r.get("表示順【選択肢・価格設定】")) or 0), "opt_select")
    elif cart == "rakuten":
        rows = data["item"]
        for fn, r in rows:                                   # 先に商品の行（管理番号＋商品名）
            if r.get("商品名"):
                put(fn, _rec(r["商品管理番号（商品URL）"], ext_sub=r.get("商品番号"), title=r.get("商品名"),
                             state_raw=r.get("倉庫指定"), sale_from=r.get("販売期間指定（開始日時）"),
                             sale_to=r.get("販売期間指定（終了日時）"), category=r.get("ジャンルID"), raw=r))
        for fn, r in rows:
            if r.get("商品名"):
                continue
            mid = r.get("商品管理番号（商品URL）", "")
            if r.get("商品オプション項目名"):
                vals = [(int(m.group(1)), v) for c, v in r.items() if (m := re.fullmatch(r"商品オプション選択肢(\d+)", c))]
                base = {k: v for k, v in r.items() if not k.startswith("商品オプション選択肢")}
                if mid not in recs:
                    orphans["rakuten_option"] = orphans.get("rakuten_option", 0) + 1
                    acc(fn)
                    continue
                acc(fn)
                if not vals:                                 # 自由入力など値の無い項目も1行で持つ
                    recs[mid]["options"].append(_opt("rakuten_option", base, r["商品オプション項目名"], sort=0))
                for n, v in sorted(vals):
                    recs[mid]["options"].append(_opt("rakuten_option", {**base, "値": v}, r["商品オプション項目名"], v, sort=n))
            elif r.get("SKU管理番号"):
                v = " / ".join(f"{r.get(f'バリエーション項目キー{i}', '')}:{r[f'バリエーション項目選択肢{i}']}"
                               for i in range(1, 7) if r.get(f"バリエーション項目選択肢{i}"))
                attach(fn, mid, _opt("rakuten_sku", r, None, v, r["SKU管理番号"], _int(r.get("通常購入販売価格"))), "rakuten_sku")
            else:
                orphans["rakuten_unknown"] = orphans.get("rakuten_unknown", 0) + 1
                acc(fn)
        for rec in recs.values():                            # 価格は SKU のいちばん安い値（楽天は SKU に価格がある）
            ps = [o["price"] for o in rec["options"] if o["src"] == "rakuten_sku" and o["price"] is not None]
            rec["price"] = min(ps) if ps else None
    elif cart == "yahoo":
        for fn, r in data["item"]:
            rec = _rec(r["code"], ext_sub=r.get("sub-code"), title=r.get("name"), price=_int(r.get("price")),
                       state_raw=r.get("display"), sale_from=r.get("sale-period-start"), sale_to=r.get("sale-period-end"),
                       category=r.get("product-category"), raw=r)
            if r.get("options"):
                rec["options"].append(_opt("yahoo_options", {"options": r["options"]}, None, r["options"]))
            put(fn, rec)
    elif cart == "giftmall":
        for fn, r in data["item"]:
            put(fn, _rec(r["商品ID"], ext_sub=r.get("商品スラグ"), title=r.get("商品名"), state_raw=r.get("公開・非公開"), raw=r))
        for fn, r in data.get("class", []):
            item = " / ".join(x for x in (r.get("規格分類名1"), r.get("規格分類名2")) if x)
            val = " / ".join(x for x in (r.get("規格名1"), r.get("規格名2")) if x)
            attach(fn, r["商品ID"], _opt("gm_class", r, item, val, r.get("商品コード"), _int(r.get("販売価格"))), "gm_class")
        for fn, r in data.get("option", []):
            attach(fn, r["商品ID"], _opt("gm_option", r, r.get("オプション名 (フロント用)"), None, r.get("オプションID")), "gm_option")
        for fn, r in data.get("master", []):
            if r["オプションID"] in masters:
                errors.append(f"{fn}: オプションIDが2回出てきます: {r['オプションID']}")
            masters[r["オプションID"]] = {"ext_id": r["オプションID"], "name": r.get("オプション名 (フロント用)"), "raw": r}
            acc(fn)
        for rec in recs.values():
            ps = [o["price"] for o in rec["options"] if o["src"] == "gm_class" and o["price"] is not None]
            rec["price"] = min(ps) if ps else None
    elif cart == "amazon":
        for fn, r in data["item"]:
            put(fn, _rec(r["seller-sku"], ext_sub=r.get("product-id") or r.get("listing-id"), title=r.get("item-name"),
                         price=_int(r.get("price")), state_raw=r.get("status"), raw=r))
    elif cart == "shopify":
        names = {}
        for fn, r in data["item"]:
            h = r["Handle"]
            if r.get("Title"):
                names[h] = [r.get(f"Option{i} Name", "") for i in (1, 2, 3)]
                put(fn, _rec(h, title=r["Title"], state_raw=r.get("Status"), category=r.get("Type") or r.get("Product Category"),
                             raw=r))
        for fn, r in data["item"]:
            h = r["Handle"]
            is_var = any(r.get(c) for c in ("Option1 Value", "Option2 Value", "Option3 Value", "Variant SKU", "Variant Price"))
            if r.get("Title") and not is_var:
                continue
            if not is_var:
                acc(fn)                                      # 画像だけの行（画像は取り込まない）
                continue
            nm = names.get(h, ["", "", ""])
            v = " / ".join(f"{nm[i - 1] or ('Option' + str(i))}:{r[f'Option{i} Value']}" for i in (1, 2, 3)
                           if r.get(f"Option{i} Value"))
            opt = _opt("shopify_variant", r, None, v, r.get("Variant SKU"), _int(r.get("Variant Price")))
            if r.get("Title"):                               # 1行目は出品と1つ目の組み合わせを兼ねる（数えるのは出品の側で1回）
                if h in recs:
                    recs[h]["options"].append(opt)
            else:
                attach(fn, h, opt, "shopify_variant")
        for rec in recs.values():
            ps = [o["price"] for o in rec["options"] if o["price"] is not None]
            rec["price"] = min(ps) if ps else None

    for rec in recs.values():
        rec["state"] = sm.get((rec["state_raw"] or "").strip(), "そのほか")
        rec["hash"] = rec_hash(rec)
    for m in masters.values():
        m["hash"] = hashlib.sha256(json.dumps(m["raw"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    for f in files:
        if f.get("used"):
            f["accounted"] = accounted.get(f["name"], 0)
            if f["accounted"] != f["rows"]:
                errors.append(f"{f['name']}: 読んだ行 {f['rows']} と数えた行 {f['accounted']} が合いません")
    return {"cart": cart, "recs": recs, "masters": masters, "files": files, "orphans": orphans, "errors": errors,
            "state_checked": cart in STATE_CHECKED}


def rec_hash(rec: dict) -> str:
    body = {k: rec[k] for k in ("ext_sub", "title", "price", "state_raw", "sale_from", "sale_to", "category", "layout", "raw")}
    body["options"] = [{k: o[k] for k in ("src", "item_name", "value", "code", "price", "sort", "raw")} for o in rec["options"]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
