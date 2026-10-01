#!/usr/bin/env python3
"""
売上フィード（経営管理 ADR-018/020）を読む。売上実績の段階B（ADR-045）。

2026-10-01 経営管理の了解「NEW PRODUCT も読み手」。守ること（経営管理から）:

- `file:…?mode=ro` で**読むたびに開き直す**（毎晩 02:30 に写しが差し替わる）
- `meta.contract_version` が "1" でなければ**止まる**（黙って読み違えない）
- `orders.status` が CANCELLED・REFUNDED・CANCEL_PENDING の注文を除く
- 明細の金額は税込。FutureShop は (unit_price＋option_price)×qty、楽天は option_price が 0
- 店は funcreate・handmadeshopyou・rakuten、**2025-05-01 から**。Amazon は入っていない
- 店をまたいで商品をそろえる紐付けは持たない → **生産管理（seisan）の紐付け表が正**
  （`seisan.store_codes()`）。当たらない明細は「分類なし」
- `out_crm` には触らない

**名前は読まない**（売上フィードは名前を持たない。表示名は別途・ADR-045）。
"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import time
from collections import defaultdict

PATH = "/srv/salesfeed/out/sales_v1.sqlite"
CONTRACT = "1"
SINCE = "2025-05-01"
EXCLUDED = ("CANCELLED", "REFUNDED", "CANCEL_PENDING")

# 売上実績のサイトキー → (売上フィードの店, seisan の店名, 商品番号の列)
# 識別子は 2026-10-01 の実測で決めた（8月・金額ベースで seisan の紐付けに当たる割合）:
#   グッズ本店 sku_no 85% ／ うちわ本店 sku_no 97% ／ 楽天 item_code 90%（sku_no だと 29%）
SHOPS = {
    "goods": ("funcreate", "グッズ本店", "sku_no"),
    "uchiwa": ("handmadeshopyou", "うちわ本店", "sku_no"),
    "rakuten": ("rakuten", "楽天", "item_code"),
}

_MAP = {"at": 0.0, "map": None, "err": None}
MAP_TTL = 3600


class Unavailable(RuntimeError):
    """売上フィードが読めない／約束（版）が違う。理由を言葉で持つ。"""


def path() -> str:
    return os.environ.get("NEWPRODUCT_SALESFEED") or PATH


def _open() -> tuple[sqlite3.Connection, dict]:
    p = path()
    if not os.path.exists(p):
        raise Unavailable(f"売上フィード（{p}）がありません")
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        meta = dict(c.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error as e:
        raise Unavailable(f"売上フィードを読めません: {e}") from None
    if str(meta.get("contract_version")) != CONTRACT:
        c.close()
        raise Unavailable(f"売上フィードの版が {meta.get('contract_version')!r} です"
                          f"（このアプリは {CONTRACT!r} を前提にしています。読まずに止めました）")
    c.row_factory = sqlite3.Row
    return c, meta


def code_map() -> tuple[dict | None, str | None]:
    """{(seisan の店名, 店の商品番号): {"cats": {(cat1,cat2,cat3)}, "codes": {共通商品コード}}}。
    **seisan の紐付け表が正。**1時間は手元で使い回す（夜に1回で足りる量だが、画面の速さのため）。"""
    now = time.time()
    if _MAP["map"] is not None and now - _MAP["at"] < MAP_TTL:
        return _MAP["map"], None
    from app import seisan
    try:
        j = seisan.store_codes()
    except (seisan.NotConfigured, seisan.Refused) as e:
        return None, f"seisan の紐付け表が読めません: {e}"
    m: dict = defaultdict(lambda: {"cats": set(), "codes": set()})
    for it in j["items"]:
        k = (it.get("store"), it.get("store_code"))
        m[k]["cats"].add((it.get("cat1") or "", it.get("cat2") or "", it.get("cat3") or ""))
        if it.get("product_code"):
            m[k]["codes"].add(it["product_code"])
    _MAP.update(at=now, map=dict(m))
    return _MAP["map"], None


def _month_add(m: str, n: int) -> str:
    y, mo = int(m[:4]), int(m[5:7]) + n
    y += (mo - 1) // 12
    return f"{y:04d}-{(mo - 1) % 12 + 1:02d}"


def _today() -> _dt.date:
    t = os.environ.get("NEWPRODUCT_TODAY")
    return _dt.date.fromisoformat(t) if t else _dt.date.today()


def months() -> list[str]:
    """2025-05 から今月まで。"""
    out, m, end = [], SINCE[:7], _today().strftime("%Y-%m")
    while m <= end:
        out.append(m)
        m = _month_add(m, 1)
    return out


def complete(month: str) -> bool:
    """**丸1か月か。**今月（途中）は False。"""
    return month < _today().strftime("%Y-%m")


def monthly(site: str) -> tuple[dict, dict]:
    """(月→合計, meta)。合計は税込の商品代（取消・返金を除く）。"""
    shop, _st, col = SHOPS[site]
    c, meta = _open()
    try:
        rows = c.execute(
            f"""SELECT substr(o.order_date,1,7) m,
                       SUM((COALESCE(l.unit_price,0)+COALESCE(l.option_price,0))*COALESCE(l.qty,0)) amt
                FROM order_lines l JOIN orders o USING (shop, order_no)
                WHERE l.shop=? AND o.order_date>=? AND COALESCE(o.status,'') NOT IN (?,?,?)
                GROUP BY m""", (shop, SINCE, *EXCLUDED)).fetchall()
        return {r["m"]: float(r["amt"] or 0) for r in rows}, meta
    finally:
        c.close()


def by_code(site: str, month: str) -> dict[str, dict]:
    """その月の、店の商品番号ごとの {revenue, qty, lines}。"""
    shop, _st, col = SHOPS[site]
    lo, hi = month + "-01", _month_add(month, 1) + "-01"
    c, _meta = _open()
    try:
        rows = c.execute(
            f"""SELECT COALESCE(l.{col},'') k,
                       SUM((COALESCE(l.unit_price,0)+COALESCE(l.option_price,0))*COALESCE(l.qty,0)) amt,
                       SUM(COALESCE(l.qty,0)) q, COUNT(*) n
                FROM order_lines l JOIN orders o USING (shop, order_no)
                WHERE l.shop=? AND o.order_date>=? AND o.order_date<?
                  AND COALESCE(o.status,'') NOT IN (?,?,?)
                GROUP BY k""", (shop, lo, hi, *EXCLUDED)).fetchall()
        return {r["k"]: {"revenue": float(r["amt"] or 0), "qty": float(r["q"] or 0),
                         "lines": int(r["n"])} for r in rows}
    finally:
        c.close()


def names(site: str, keys: list[str]) -> dict[str, str]:
    """店の商品番号 → 商品マスタの名前（経営管理 ADR-030・`products` 表）。

    **表がまだ無ければ空**（画面は「未取得」）。FutureShop は sku_no（枝番付きにも親の名前が入る）、
    楽天は item_code で引く。**商品マスタの名前だけ**で、注文の名前（お客さまの文字が混ざる）ではない。
    """
    keys = [k for k in keys if k]
    if not keys:
        return {}
    shop, _st, col = SHOPS[site]
    c, _meta = _open()
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(products)")}
        if not cols or "name" not in cols or col not in cols:
            return {}
        q = (f"SELECT {col} k, name FROM products WHERE shop=? AND {col} IN "
             f"({','.join('?' * len(keys))}) AND COALESCE(name,'')!=''")
        return {r["k"]: r["name"] for r in c.execute(q, (shop, *keys))}
    finally:
        c.close()
