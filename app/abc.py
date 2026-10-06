#!/usr/bin/env python3
"""
商品ABC分析と比較ABC分析（2026-10-02 十文字さん依頼・決定 ADR-050）。

決めたこと（選択肢への回答）:
- 区切り: **A 70%・B 90%・C 残り**（売上の累計の割合）
- 商品の単位: **サイトごとに店の商品番号**（グッズ本店・うちわ本店・楽天）。3店合計は作らない
- 比較の区分: **増加・減少・消滅・新規**（＋金額が同じなら「同額」）

守ること:
- 出どころは売上フィード（税込の商品代・取消と返金を除く）。2025-05 から
- **比べる期間にデータが無ければ比較しない**（無いのを 0 円と扱うと全部「新規」に見える）
- **今月（途中）を含むときは、前年も同じ日付までにそろえる**（途中の今月と丸1か月の前年を比べると
  必ず減少に見える）
- ABC の境目は**その商品の手前までの累計**で決める（1商品で70%を超えても、その商品は A）
"""
from __future__ import annotations

import datetime as _dt
import re

from app import salesfeed

A_LIMIT, B_LIMIT = 70.0, 90.0
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CLASSES = ("増加", "減少", "同額", "消滅", "新規")


def _d(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s)


def _month_range(from_m: str, to_m: str) -> tuple[_dt.date, _dt.date]:
    """[1日, 翌月1日)。"""
    lo = _d(from_m + "-01")
    hi = _d(salesfeed._month_add(to_m, 1) + "-01")
    if hi <= lo:
        raise ValueError("期間の終わりが始まりより前です")
    return lo, hi


def _minus_year(d: _dt.date) -> _dt.date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:                   # 2/29
        return d.replace(year=d.year - 1, day=28)


def abc(rows: dict[str, dict]) -> tuple[list[dict], dict]:
    """[{key, revenue, qty, share, cum, rank}] と区分ごとの集計。"""
    total = sum(v["revenue"] for v in rows.values())
    out, cum = [], 0.0
    summary = {c: {"count": 0, "revenue": 0.0} for c in "ABC"}
    for i, (k, v) in enumerate(sorted(rows.items(), key=lambda x: (-x[1]["revenue"], x[0])), 1):
        before = cum / total * 100 if total else 0.0
        cls = "A" if before < A_LIMIT else ("B" if before < B_LIMIT else "C")
        cum += v["revenue"]
        out.append({"key": k, "revenue": v["revenue"], "qty": v["qty"], "rank": i, "class": cls,
                    "share": round(v["revenue"] / total * 100, 2) if total else None,
                    "cum": round(cum / total * 100, 2) if total else None})
        summary[cls]["count"] += 1
        summary[cls]["revenue"] += v["revenue"]
    for c in summary.values():
        c["share"] = round(c["revenue"] / total * 100, 1) if total else None
    return out, {"total": total, "count": len(out), "classes": summary}


def analyze(site: str, from_m: str, to_m: str, compare: str = "yoy",
            cfrom: str = "", cto: str = "") -> dict:
    if site not in salesfeed.SHOPS:
        raise ValueError("サイトは グッズ本店・うちわ本店・楽天 から選んでください")
    since = _d(salesfeed.SINCE)
    today = salesfeed._today()
    last_full = salesfeed._month_add(today.strftime("%Y-%m"), -1)
    from_m = from_m or to_m or last_full           # 既定は直近の丸1か月
    to_m = to_m or from_m
    for m in (from_m, to_m, cfrom, cto):
        if m and not _MONTH.match(m):
            raise ValueError(f"月は YYYY-MM の形で選んでください（{m!r}）")
    lo, hi = _month_range(from_m, to_m)
    if lo < since:
        raise ValueError(f"売上フィードは {salesfeed.SINCE[:7]} からです。それより前は選べません")
    notes = []
    partial = hi > today
    if partial:
        hi = today                       # 前日まで
        notes.append(f"{to_m} は途中の月です。{(hi - _dt.timedelta(days=1)).isoformat()} までの注文で数えています")
    if hi <= lo:
        raise ValueError("この期間にはまだ注文がありません")

    cur = salesfeed.by_code_range(site, lo.isoformat(), hi.isoformat())
    cur_rows, cur_sum = abc(cur)
    res = {"site": site, "site_label": salesfeed.SHOPS[site][1],
           "period": {"from": lo.isoformat(), "to": (hi - _dt.timedelta(days=1)).isoformat()},
           "limits": {"A": A_LIMIT, "B": B_LIMIT}, "tax": "税込", "notes": notes,
           "current": cur_sum, "compare": None, "why_compare": None,
           "months": salesfeed.months(), "compare_mode": compare,
           "sites": [{"key": k, "label": v[1]} for k, v in salesfeed.SHOPS.items()]}

    # 比べる期間
    if compare == "none":
        clo = chi = None
    elif compare == "yoy":
        clo, chi = _minus_year(lo), _minus_year(hi)
        if partial:
            notes.append("前年も同じ日付までにそろえています")
    else:
        if not cfrom or not cto:
            raise ValueError("比べる期間（はじめの月・おわりの月）を選んでください")
        clo, chi = _month_range(cfrom, cto)
        if chi > today:
            chi = today
        if partial or chi == today:
            notes.append("比べる期間か選んだ期間に途中の月が含まれます。日数が違うので、増減は割り引いて見てください")

    prev = {}
    if clo is not None:
        if clo < since:
            res["why_compare"] = (f"比べる期間（{clo.isoformat()}〜）に売上フィードのデータがありません"
                                  f"（{salesfeed.SINCE[:7]} から）。比較はしません（無いのを0円と扱うと全部「新規」に見えるため）")
            clo = None
        else:
            prev = salesfeed.by_code_range(site, clo.isoformat(), chi.isoformat())
            prev_rows, prev_sum = abc(prev)
            res["compare"] = {**prev_sum, "period": {"from": clo.isoformat(),
                                                     "to": (chi - _dt.timedelta(days=1)).isoformat()}}

    # 名前と分類（出すのは**商品マスタの名前だけ**。お客さま個別の商品は名前を出さない）
    keys = sorted(set(cur) | set(prev))
    names = salesfeed.names(site, keys)
    cmap, _why = salesfeed.code_map()
    st = salesfeed.SHOPS[site][1]

    def label(k):
        n = names.get(k) or {}
        if n.get("name"):
            return n["name"], None
        return None, ("複数の商品（同じ商品番号）" if n.get("multi") else
                      "個別の商品" if n.get("withheld") else "未取得")

    def path(k):
        ent = (cmap or {}).get((st, k))
        if not ent:
            return "CIP の紐付けに無い"
        pairs = {(a, b) for a, b, _c in ent["cats"]}
        if len(pairs) != 1:
            return "複数の分類に分かれる番号"
        cats = sorted(ent["cats"])
        return " / ".join(x for x in cats[0] if x) if len(cats) == 1 else " / ".join(next(iter(pairs)))

    prev_class = {r["key"]: r["class"] for r in (abc(prev)[0] if prev else [])}
    cur_class = {r["key"]: r["class"] for r in cur_rows}
    for r in cur_rows:
        r["name"], r["name_label"] = label(r["key"])
        r["path"] = path(r["key"])
        r["prev_class"] = prev_class.get(r["key"]) if clo is not None else None
    res["rows"] = cur_rows

    if clo is not None:
        groups = {c: [] for c in CLASSES}
        for k in keys:
            a = cur.get(k, {}).get("revenue", 0.0)
            b = prev.get(k, {}).get("revenue", 0.0)
            if b == 0 and a == 0:
                continue
            cls = ("新規" if b == 0 else "消滅" if a == 0 else
                   "増加" if a > b else "減少" if a < b else "同額")
            nm, nl = label(k)
            groups[cls].append({"key": k, "name": nm, "name_label": nl, "path": path(k),
                                "current": a, "previous": b, "diff": a - b,
                                "class_now": cur_class.get(k),
                                "class_before": prev_class.get(k)})
        res["changes"] = {c: {"count": len(v), "current": sum(x["current"] for x in v),
                              "previous": sum(x["previous"] for x in v),
                              "diff": sum(x["diff"] for x in v),
                              "rows": sorted(v, key=lambda x: -abs(x["diff"]))}
                          for c, v in groups.items()}
    return res
