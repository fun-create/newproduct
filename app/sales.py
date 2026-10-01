#!/usr/bin/env python3
"""
売上実績（2026-10-01 十文字さん決定・設計 fun-create-ec-agents
`docs/product-dev/2026-09-29_売上実績画面_設計.md`）。

## 何のための画面か

売上の**正式な数字**は経営管理（Business Analysis）の持ち分。ここは
**「何が売れているか（構成）」を見て次の商品を決める**ための画面。
合計を独自に作らない。出どころの集計をそのまま見せる。

## いまの出どころ（段階 A）

Auto GROWTH の月次集計 `insight_products_YYYY-MM.json`（seisan の受注明細を
**分類でだけ**集計したもの。商品名は読まない。毎月2日）。
持っているのは **店別の合計** と **全体の大分類・中分類**。**サイト別の構成は無い。**

段階 B（売上フィード＋seisan の「店の商品番号 → 分類」）で、サイト別の構成と商品別を足す。
それまでは「未計測」と出す。**0 で埋めない**（N-10）。

## 決まっていること

- 金額は**税込**（十文字さん 10-01「税込」）。出どころ（seisan の商品代）のまま
- 見られるのは **NEW PRODUCT の利用者全員**（同上）
- **前年比は丸1か月どうしのときだけ**（`complete=false` の月は出さない）
- Amazon 会社出荷は自社発送だけ。**FBA は含まない**（出どころの注記のまま）
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

EXPORT_DIR = Path(os.environ.get("NEWPRODUCT_AG_EXPORT") or "/opt/autogrowth/data/export")
FILE_RE = re.compile(r"^insight_products_(\d{4}-\d{2})\.json$")

# (キー, 画面の名前, Auto GROWTH の店名)。**並びは十文字さんの依頼の順**
SITES = [
    ("all", "全体", None),
    ("goods", "グッズ本店", "グッズ本店"),
    ("uchiwa", "うちわ本店", "うちわ本店"),
    ("rakuten", "楽天", "楽天"),
    ("amazon", "Amazon 会社出荷", "amazon"),
    ("fba", "Amazon FBA", None),
]
SITE_KEYS = [s[0] for s in SITES]

WHY_SITE_MIX = ("未計測: サイト別の構成は、売上フィード（経営管理）を読めるようになり、"
                "seisan の「店の商品番号 → 分類」の対応が届いてから出します")
WHY_FBA = ("未計測: Amazon FBA は商品別の明細が 2026-04 の1か月分しかありません。"
           "杉浦さんの FBA 毎日出力が売上フィードに入ってから出します")
WHY_PRODUCTS = ("未計測: 商品別は、売上フィード（店の商品番号ごとの明細）を読めるようになってから出します")


def export_dir() -> Path:
    return Path(os.environ.get("NEWPRODUCT_AG_EXPORT") or EXPORT_DIR)


def months() -> list[str]:
    try:
        names = os.listdir(export_dir())
    except OSError:
        return []
    return sorted(m.group(1) for m in map(FILE_RE.match, names) if m)


def _load(month: str) -> dict | None:
    try:
        d = json.loads((export_dir() / f"insight_products_{month}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _yoy(cur, prev, complete: bool):
    """前年比。**丸1か月どうしで、前年が正のときだけ。**それ以外は None（出さない）。"""
    if not complete or cur is None or prev is None or prev <= 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def _share(v, total):
    if v is None or not total:
        return None
    return round(v / total * 100, 1)


def _site_total(d: dict, site: str) -> tuple[float | None, float | None]:
    key, _, ag = next(s for s in SITES if s[0] == site)
    if key == "all":
        t, p = d.get("totals") or {}, d.get("prev_totals") or {}
        return _num(t.get("revenue")), _num(p.get("revenue"))
    if ag is None:
        return None, None
    for s in d.get("stores") or []:
        if s.get("store") == ag:
            return _num(s.get("revenue")), _num(s.get("prev_revenue"))
    return None, None            # 出どころに無い＝未計測（0 ではない）


def _composition(d: dict) -> dict:
    """全体の大分類・中分類。**分類が付かない分を1行で出す**（黙って外すと構成比が膨らむ）。"""
    total = _num((d.get("totals") or {}).get("revenue"))
    classified = _num((d.get("totals") or {}).get("classified_revenue"))
    complete = bool(d.get("complete"))
    cat2 = {}
    for r in d.get("cat2") or []:
        cat2.setdefault(r.get("cat1"), []).append(r)
    rows = []
    for r in d.get("cat1") or []:
        v, pv = _num(r.get("revenue")), _num(r.get("prev_revenue"))
        rows.append({
            "name": r.get("name"), "revenue": v, "prev_revenue": pv,
            "share": _share(v, total), "yoy": _yoy(v, pv, complete),
            "children": [{"name": c.get("cat2"), "revenue": _num(c.get("revenue")),
                          "prev_revenue": _num(c.get("prev_revenue")),
                          "share": _share(_num(c.get("revenue")), total),
                          "yoy": _yoy(_num(c.get("revenue")), _num(c.get("prev_revenue")), complete)}
                         for c in cat2.get(r.get("name"), [])],
        })
    unclassified = (round(total - classified, 2)
                    if total is not None and classified is not None else None)
    return {"rows": rows, "total": total, "classified": classified,
            "unclassified": unclassified, "unclassified_share": _share(unclassified, total),
            "classified_share": _share(classified, total)}


def overview(site: str = "all", month: str | None = None) -> dict:
    if site not in SITE_KEYS:
        raise ValueError(f"知らないサイト {site!r}")
    ms = months()
    base = {"sites": [{"key": k, "label": lb} for k, lb, _ in SITES], "site": site,
            "site_label": next(lb for k, lb, _ in SITES if k == site), "months": ms,
            "tax": "税込", "basis": "商品代（受注日・seisan の「確定」「仮確定」）"}
    if not ms:
        return {**base, "month": None, "why": (
            f"出どころ（{export_dir()}）に月次の集計がありません。"
            "Auto GROWTH の月次集計（毎月2日）を読めるか確かめてください")}
    month = month if month in ms else ms[-1]
    d = _load(month)
    if d is None:
        return {**base, "month": month, "why": f"{month} の集計が読めません"}
    complete = bool(d.get("complete"))
    cur, prev = _site_total(d, site)
    trend = []
    for m in ms:
        dm = _load(m)
        if dm is None:
            continue
        c, p = _site_total(dm, site)
        trend.append({"month": m, "revenue": c, "prev_revenue": p,
                      "complete": bool(dm.get("complete"))})
    out = {**base, "month": month, "complete": complete,
           "generated_at": d.get("generated_at"), "source": d.get("source"),
           "caveats": d.get("caveats") or [], "note": d.get("note"),
           "prev_year_month": d.get("prev_year_month"),
           "total": {"revenue": cur, "prev_revenue": prev, "yoy": _yoy(cur, prev, complete),
                     "why": (WHY_FBA if site == "fba" else
                             None if cur is not None else "未計測: 出どころにこのサイトの合計がありません")},
           "trend": trend,
           "products": {"rows": None, "why": WHY_FBA if site == "fba" else WHY_PRODUCTS}}
    if site == "all":
        out["composition"] = {**_composition(d), "why": None}
        out["stores"] = [{"store": s.get("store"), "revenue": _num(s.get("revenue")),
                          "prev_revenue": _num(s.get("prev_revenue")),
                          "share": _share(_num(s.get("revenue")), cur),
                          "yoy": _yoy(_num(s.get("revenue")), _num(s.get("prev_revenue")), complete)}
                         for s in d.get("stores") or []]
    else:
        out["composition"] = {"rows": None, "why": WHY_FBA if site == "fba" else WHY_SITE_MIX}
    return out
