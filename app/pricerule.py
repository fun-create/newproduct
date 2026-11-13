#!/usr/bin/env python3
"""
価格の決まり（数量で割引・段階3・ADR-149・FR-265・PL-25）。

店は数量割引を選択肢で表している（例: 「基本単価 （1個購入）」「15%引き （3個購入）」「25%引き （5～19個購入）」に
マイナスの価格）。割引の額は商品ごとに違う（単価 × 率）ので、**持つのは「何個で何 %」の段だけ**にし、選択肢の定義とは別にする。
- 段は値の文字から読む（`parse_tier`）。読めない値が多いまとまりは、価格の決まりの候補にしない
- **同じ項目名でも出品によって段の表が違う**（11-13 グッズ本店「単価割引率」は3種類）。出品ごとに段の表を読み、
  **表ごとに価格の決まりを1つ**作り、出品をその表に結ぶ（`price_rule_listing`）。1つの出品の中で食い違う値があれば、その出品は結ばない
- 店の項目名を寄せた記録（`price_rule_alias`）を残す。寄せた項目名は「選択肢の整理」から消える
- 作る・寄せる・外すのは社長と商品開発部管理者
"""
from __future__ import annotations

import json
import re
import unicodedata

from app import store

EDIT_ROLES = {"president", "admin"}


def parse_tier(label: str) -> dict | None:
    """「15%引き （3個購入）」→ {min:3, max:3, rate:15}。「基本単価 （1個購入）」→ rate 0。読めなければ None。"""
    s = unicodedata.normalize("NFKC", label or "").strip()
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:引き|off|割引)", s, re.I)
    rate = float(m.group(1)) if m else (0.0 if re.search(r"基本単価|通常単価|定価", s) else None)
    q = re.search(r"(\d+)\s*(?:[~～〜\-]\s*(\d+))?\s*個(以上)?", s)
    if rate is None or q is None:
        return None
    lo = int(q.group(1))
    hi = int(q.group(2)) if q.group(2) else (None if q.group(3) else lo)
    return {"min": lo, "max": hi, "rate": int(rate) if rate == int(rate) else rate}


def tiers_from(values: list[str]) -> tuple[list[dict], list[str], list[str]]:
    """値の並びから段を作る。返すもの: 段（数量順）・読めなかった値・食い違い。"""
    got, bad, conflict = {}, [], []
    for v in values:
        if not (v or "").strip():
            continue
        t = parse_tier(v)
        if t is None:
            bad.append(v)
            continue
        k = (t["min"], t["max"])
        if k in got and got[k] != t["rate"]:
            conflict.append(f"{t['min']}〜{t['max'] or ''}個: {got[k]}% と {t['rate']}%")
        got.setdefault(k, t["rate"])
    tiers = [{"min": a, "max": b, "rate": r} for (a, b), r in sorted(got.items(), key=lambda kv: (kv[0][0], kv[0][1] or 10**9))]
    return tiers, bad, conflict


def tables(members: list[tuple[str, str, str]]) -> dict:
    """寄せる項目名の出品ごとに段の表を読み、同じ表をまとめる。
    返すもの: {"tables": [{"tiers", "listings": [(listing_id, shop, src, item)]}], "bad_listings": 食い違い・読めない出品の数}"""
    by, bad = {}, 0
    for shop, src, item in members:
        cur = {}
        for r in store.q("SELECT o.listing_id, o.value FROM listing_option o JOIN listing l ON l.id=o.listing_id "
                         "WHERE l.shop=? AND o.src=? AND o.item_name=? AND l.gone_import_id IS NULL", (shop, src, item)):
            cur.setdefault(r["listing_id"], []).append(r["value"])
        for lid, vs in cur.items():
            vs = [v for v in vs if (v or "").strip()]
            t, b, conflict = tiers_from(vs)
            if conflict or len(t) < 2 or len(b) > len(vs) * 0.2:
                bad += 1
                continue
            key = json.dumps(t, sort_keys=True)
            by.setdefault(key, {"tiers": t, "listings": []})["listings"].append((lid, shop, src, item))
    out = sorted(by.values(), key=lambda x: -len(x["listings"]))
    return {"tables": out, "bad_listings": bad}


def suggest(members: list[tuple[str, str, str]]) -> list[dict] | None:
    """段の表として読めるなら、表ごとの段と出品数を返す（読める出品が8割以上のときだけ）。"""
    r = tables(members)
    n = sum(len(t["listings"]) for t in r["tables"])
    if not n or r["bad_listings"] > (n + r["bad_listings"]) * 0.2:
        return None
    return [{"tiers": t["tiers"], "n_listings": len(t["listings"])} for t in r["tables"]]


def _need(uid: str):
    from app import gate
    if not EDIT_ROLES & set(gate.roles_of(uid)):
        raise PermissionError("価格の決まりを作る・直すのは、社長と商品開発部管理者の業務ロールの人です")


def create(name: str, members: list[tuple[str, str, str]], uid: str) -> dict:
    """段の表ごとに価格の決まりを作る（2つ以上なら「名前 A」「名前 B」…）。出品を表に結ぶ。"""
    _need(uid)
    name = (name or "").strip()
    if not name:
        raise ValueError("名前が空です")
    if not members:
        raise ValueError("寄せる項目名を1つ以上選んでください")
    for shop, src, item in members:
        if store.one("SELECT 1 FROM listing_option o JOIN listing l ON l.id=o.listing_id WHERE l.shop=? AND o.src=? AND o.item_name=? LIMIT 1",
                     (shop, src, item)) is None:
            raise LookupError(f"台帳にその項目名がありません: {shop} {item}")
    r = tables(members)
    if not r["tables"]:
        raise ValueError("数量の段が2つ以上読める出品がありません。価格の決まりではなく選択肢の定義にしてください")
    labels = [name] if len(r["tables"]) == 1 else [f"{name} {chr(65 + i)}" for i in range(len(r["tables"]))]
    now = store.now_s()
    made = []
    with store.tx() as c:
        for lb in labels:
            if c.execute("SELECT 1 FROM price_rule WHERE name=?", (lb,)).fetchone():
                raise ValueError(f"同じ名前の価格の決まりがあります: {lb}")
        for shop, src, item in members:
            for t in ("price_rule_alias", "option_alias"):
                if c.execute(f"SELECT 1 FROM {t} WHERE shop=? AND src=? AND item_name=?", (shop, src, item)).fetchone():
                    raise ValueError(f"「{item}」（{shop}）はもう寄せてあります。先に外してください")
        first = None
        for lb, t in zip(labels, r["tables"]):
            rid = c.execute("INSERT INTO price_rule (name,kind,tiers_json,created_by,created_at,updated_by,updated_at) VALUES (?,?,?,?,?,?,?)",
                            (lb, "数量で割引", json.dumps(t["tiers"], ensure_ascii=False), uid, now, uid, now)).lastrowid
            first = first or rid
            c.executemany("INSERT OR REPLACE INTO price_rule_listing (listing_id,rule_id,shop,src,item_name) VALUES (?,?,?,?,?)",
                          [(lid, rid, s_, src, it) for lid, s_, src, it in t["listings"]])
            c.execute("INSERT INTO price_rule_revision (rule_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                      (rid, now, uid, "価格の決まりを作った", f"出品 {len(t['listings'])} 件・" + "、".join(f"{s}:{i}" for s, _, i in members)))
            made.append({"id": rid, "name": lb, "tiers": t["tiers"], "listings": len(t["listings"])})
        # 項目名は最初の決まりに寄せた印にする（「選択肢の整理」から消すため。出品ごとの結びは price_rule_listing）
        c.executemany("INSERT INTO price_rule_alias (shop,src,item_name,rule_id,by_user,at) VALUES (?,?,?,?,?,?)",
                      [(s, src, i, first, uid, now) for s, src, i in members])
    store.audit(uid, "pricerule.create", ",".join(str(m["id"]) for m in made), {"rules": len(made), "bad_listings": r["bad_listings"]})
    return {"rules": made, "bad_listings": r["bad_listings"]}


def detach(rid: int, shop: str, src: str, item: str, uid: str) -> dict:
    _need(uid)
    with store.tx() as c:
        if c.execute("DELETE FROM price_rule_alias WHERE rule_id=? AND shop=? AND src=? AND item_name=?", (rid, shop, src, item)).rowcount == 0:
            raise LookupError("その項目名はこの価格の決まりに寄せていません")
        c.execute("DELETE FROM price_rule_listing WHERE shop=? AND src=? AND item_name=?", (shop, src, item))
        c.execute("INSERT INTO price_rule_revision (rule_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                  (rid, store.now_s(), uid, "項目名を外した", f"{shop}:{item}"))
    return {"ok": True}


def rules() -> list[dict]:
    out = []
    for r in store.q("SELECT p.*, (SELECT COUNT(*) FROM price_rule_alias a WHERE a.rule_id=p.id) n_alias, "
                     "(SELECT COUNT(*) FROM price_rule_listing x WHERE x.rule_id=p.id) n_listings FROM price_rule p ORDER BY p.name"):
        d = dict(r)
        d["tiers"] = json.loads(d.pop("tiers_json"))
        d["aliases"] = store.rows(store.q("SELECT a.shop, s.label shop_label, a.src, a.item_name FROM price_rule_alias a JOIN shop s ON s.code=a.shop "
                                          "WHERE a.rule_id=? ORDER BY s.sort, a.item_name", (r["id"],)))
        out.append(d)
    return out
