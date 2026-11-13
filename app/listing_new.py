#!/usr/bin/env python3
"""
NEW PRODUCT で新しい出品を作る（段階4・ADR-155・FR-275・256・PL-40）。いまは FutureShop の3店だけ。

**似た既存の出品を雛形にして写す**（商品開発がふだん似た商品をコピーして作るのと同じ）。写したうえで、
商品URLコード・商品番号・商品名・本体価格・キャッチコピー・ステータス（既定は 1＝非公開）を入れる。
- 写すもの: 雛形の商品の行（グループ・レイアウト・送料などの設定）と選択肢の行（項目選択肢・オプション価格）
- 写さないもの: 登録日時・最終更新日時・商品URL・JANコード・外部連携の名前（その商品のもの）。キャッチコピーは入れなければ空
- 作った出品は `first_import_id = 0`（店にまだ無い）。店への書き出しは「新規」（n）になり、承認者は新規の人
- 店のファイルに出てきたら（取り込み）、ふつうの出品になる（ADR-155・`listing.apply`）。それまで「消えた」にはしない
- 直せるのは、NEW PRODUCT で作って店にまだ無い出品だけ（店にある出品の直しは、項目名を変えたときの警告＝FR-296 と一緒に作る）
- 作る・直すのは商品開発部・社長。お客さまの情報は扱わない
"""
from __future__ import annotations

import json
import re

from app import listing_parse as lp
from app import store

ROLES = {"member", "admin", "devdept", "president"}
SHOPS = ("goods", "uchiwa", "frame")
NOT_COPIED = ("登録日時", "最終更新日時", "商品URL", "JANコード", "外部連携商品名", "外部連携任意項目")


def _need(uid: str):
    from app import gate
    if not ROLES & set(gate.roles_of(uid)):
        raise PermissionError("新しい出品を作る・直すのは、商品開発部・社長の業務ロールの人です")


def _validate(shop: str, f: dict, own_id: int | None = None) -> dict:
    code = (f.get("url_code") or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z_-]{2,32}", code):
        raise ValueError("商品URLコードは 2〜32 文字の半角英数字と - _ です")
    dup = store.one("SELECT id FROM listing WHERE shop=? AND ext_id=?", (shop, code))
    if dup and dup["id"] != own_id:
        raise ValueError(f"この店にはもう商品URLコード「{code}」があります")
    no = (f.get("item_no") or "").strip()
    if len(no) > 32 or "~" in no:
        raise ValueError("商品番号は 32 文字まで・~ は使えません")
    title = (f.get("title") or "").strip()
    if not title:
        raise ValueError("商品名が空です")
    if len(title) > 300:
        raise ValueError("商品名は 300 文字までです")
    price = str(f.get("price") or "").replace(",", "").strip()
    if not price.isdigit():
        raise ValueError("本体価格は円の整数で入れてください")
    catch = (f.get("catch") or "").strip()
    if len(catch) > 200:
        raise ValueError("キャッチコピーは 200 文字までです")
    status = str(f.get("status") or "1")
    if status not in ("0", "1"):
        raise ValueError("ステータスは 0（公開）か 1（非公開）です")
    sid = (f.get("sellable_id") or "").strip() or None
    if sid and store.one("SELECT 1 FROM sellable WHERE id=?", (sid,)) is None:
        raise LookupError("その売るものはありません")
    return {"code": code, "no": no, "title": title, "price": int(price), "catch": catch, "status": status, "sid": sid}


def _rec(shop: str, raw: dict, options: list[dict]) -> dict:
    rec = {"ext_id": raw["商品URLコード"], "ext_sub": raw.get("商品番号"), "title": raw.get("商品名"), "price": lp._int(raw.get("本体価格")),
           "state_raw": raw.get("ステータス"), "sale_from": raw.get("販売期間(From)"), "sale_to": raw.get("販売期間(to)"),
           "category": raw.get("メイングループ"), "layout": raw.get("レイアウト割当名"), "raw": raw, "options": options}
    rec["state"] = lp.STATE_MAP["futureshop"].get(rec["state_raw"] or "", "そのほか")
    rec["hash"] = lp.rec_hash(rec)
    return rec


def create_from_template(template_id: int, f: dict, uid: str) -> dict:
    _need(uid)
    t = store.one("SELECT * FROM listing WHERE id=?", (template_id,))
    if t is None:
        raise LookupError("雛形の出品がありません")
    if t["shop"] not in SHOPS:
        raise ValueError("いま新しく作れるのは FutureShop の3店（グッズ本店・うちわ本店・アットフレーム）だけです")
    v = _validate(t["shop"], f)
    raw = {k: val for k, val in json.loads(t["raw_json"]).items() if k not in NOT_COPIED and k != "キャッチコピー"}
    raw.update({"商品URLコード": v["code"], "商品名": v["title"], "本体価格": str(v["price"]), "ステータス": v["status"]})
    if v["no"]:
        raw["商品番号"] = v["no"]
    else:
        raw.pop("商品番号", None)
    if v["catch"]:
        raw["キャッチコピー"] = v["catch"]
    options = []
    for o in store.q("SELECT src, item_name, value, code, price, sort, raw_json FROM listing_option WHERE listing_id=? ORDER BY id", (template_id,)):
        r = {k: val for k, val in json.loads(o["raw_json"]).items() if k not in NOT_COPIED}
        if "商品URLコード" in r:
            r["商品URLコード"] = v["code"]
        options.append({"src": o["src"], "item_name": o["item_name"], "value": o["value"], "code": o["code"], "price": o["price"],
                        "sort": o["sort"], "raw": r})
    rec = _rec(t["shop"], raw, options)
    with store.tx() as c:
        lid = c.execute("INSERT INTO listing (shop,ext_id,ext_sub,title,price,state,state_raw,sale_from,sale_to,category,layout,raw_json,row_hash,"
                        "sellable_id,first_import_id,last_import_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)",
                        (t["shop"], rec["ext_id"], rec["ext_sub"], rec["title"], rec["price"], rec["state"], rec["state_raw"], rec["sale_from"],
                         rec["sale_to"], rec["category"], rec["layout"], json.dumps(raw, ensure_ascii=False, separators=(",", ":")), rec["hash"],
                         v["sid"])).lastrowid
        c.executemany("INSERT INTO listing_option (listing_id,src,item_name,value,code,price,sort,raw_json) VALUES (?,?,?,?,?,?,?,?)",
                      [(lid, o["src"], o["item_name"], o["value"], o["code"], o["price"], o["sort"],
                        json.dumps(o["raw"], ensure_ascii=False, separators=(",", ":"))) for o in options])
    store.audit(uid, "listing.new", t["shop"], {"id": lid, "template": template_id, "options": len(options)})
    return {"id": lid, "options": len(options)}


def update_new(lid: int, f: dict, uid: str) -> dict:
    """NEW PRODUCT で作って店にまだ無い出品だけ直せる。"""
    _need(uid)
    r = store.one("SELECT * FROM listing WHERE id=?", (lid,))
    if r is None:
        raise LookupError("出品がありません")
    if r["first_import_id"] != 0:
        raise PermissionError("店にある出品は、ここでは直せません（店の値が正。直すのは店の管理画面か、更新の書き出し）")
    v = _validate(r["shop"], f, own_id=lid)
    raw = json.loads(r["raw_json"])
    old_code = raw.get("商品URLコード")
    raw.update({"商品URLコード": v["code"], "商品名": v["title"], "本体価格": str(v["price"]), "ステータス": v["status"]})
    for col, val in (("商品番号", v["no"]), ("キャッチコピー", v["catch"])):
        if val:
            raw[col] = val
        else:
            raw.pop(col, None)
    options = []
    for o in store.q("SELECT id, src, item_name, value, code, price, sort, raw_json FROM listing_option WHERE listing_id=? ORDER BY id", (lid,)):
        orw = json.loads(o["raw_json"])
        if orw.get("商品URLコード") == old_code:
            orw["商品URLコード"] = v["code"]
        options.append({"id": o["id"], "src": o["src"], "item_name": o["item_name"], "value": o["value"], "code": o["code"],
                        "price": o["price"], "sort": o["sort"], "raw": orw})
    rec = _rec(r["shop"], raw, [{k: o[k] for k in ("src", "item_name", "value", "code", "price", "sort", "raw")} for o in options])
    with store.tx() as c:
        c.execute("UPDATE listing SET ext_id=?, ext_sub=?, title=?, price=?, state=?, state_raw=?, raw_json=?, row_hash=?, sellable_id=? WHERE id=?",
                  (rec["ext_id"], rec["ext_sub"], rec["title"], rec["price"], rec["state"], rec["state_raw"],
                   json.dumps(raw, ensure_ascii=False, separators=(",", ":")), rec["hash"], v["sid"], lid))
        c.executemany("UPDATE listing_option SET raw_json=? WHERE id=?",
                      [(json.dumps(o["raw"], ensure_ascii=False, separators=(",", ":")), o["id"]) for o in options])
    store.audit(uid, "listing.new_update", r["shop"], {"id": lid})
    return {"ok": True}
