#!/usr/bin/env python3
"""
新商品の年度累計（5月始まり）— 石原さん 11/16 追加修正依頼書 R01・FR-329・ADR-164。

**数え方**（依頼書 R01 の必須条件 1〜9）
- 年度は 5/1〜翌 4/30。年度途中は 5/1 から「実績のある最後の日」まで
- ラベルごとに、案件の発売日〜1年後の同じ日の前日（`one_year_end` の前日）と年度が重なる日だけを足す。
  発売前の売上・1年を過ぎてからの売上は入れない。**年度の途中で1年を過ぎても、それまでの分は残る**（いま1年以内の案件だけを拾う方式にしない）
- 2/29 発売は、翌年の 3/1 から入れない（`window_since` と同じ境。翌年 2/28 までは入る）
- 出どころは2つ。**同じ期間を2回足さない**（週次の2週間の数字は使わない）
  1. ラベル×日の売上（`newprod_daily`・月曜 8:30 の自動送信・年度の始まりから毎回まるごと入れ替え）。年度の始まりから覆っていればこれを使う＝確定
  2. 無ければ最新の週次レポートの月別の表。**発売の月・1年の境の月は日で区切れないので合計に入れず「暫定」**と出す（推計しない）
- 案件に結ばれていないラベルは、発売日が分からないので合計に入れず、別に金額を出す（黙って落とさない）
- 全額／増分: 週次レポートは全額で数えている。増分の案件も週次レポートの全額のまま入れ、そう出す（ルールは変えない）
"""
from __future__ import annotations

import datetime as dt
import json

from app import store


def fy_of(d: dt.date) -> int:
    return d.year if d.month >= 5 else d.year - 1


def one_year_end(launch: dt.date) -> dt.date:
    """この日からは数えない（発売の1年後の同じ日。2/29 は翌年の 3/1）"""
    try:
        return launch.replace(year=launch.year + 1)
    except ValueError:
        return dt.date(launch.year + 1, 3, 1)


def _month_end(d: dt.date) -> dt.date:
    n = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return n - dt.timedelta(days=1)


def _linked() -> dict:
    """{ラベル: {launch, projects, basis}}（登録済みの案件と結んだラベル）。1つのラベルが複数の案件に結ばれていたら早い発売日"""
    out: dict = {}
    for r in store.q("SELECT g.project_id, g.labels, p.launch_date, p.revenue_basis, p.internal_name FROM newprod_reg g "
                     "JOIN project p ON p.id=g.project_id WHERE g.state='登録済み'"):
        for lab in json.loads(r["labels"] or "[]"):
            x = out.setdefault(lab, {"launch": None, "projects": [], "basis": set()})
            x["projects"].append({"id": r["project_id"], "name": r["internal_name"]})
            if r["revenue_basis"]:
                x["basis"].add(r["revenue_basis"])
            if r["launch_date"] and (x["launch"] is None or r["launch_date"] < x["launch"]):
                x["launch"] = r["launch_date"]
    return out


def ytd(today: dt.date | None = None) -> dict:
    from app import newprod, newprod_daily
    today = today or store.today()
    fy = fy_of(today)
    fs, fe = dt.date(fy, 5, 1), dt.date(fy + 1, 4, 30)
    cov = newprod_daily.coverage()
    snap = newprod.latest()
    notes: list[str] = []
    if cov and cov["f"] and cov["f"] <= fs.isoformat():
        src, end = "daily", min(dt.date.fromisoformat(cov["t"]), fe, today)
        last = newprod_daily.status()
        received = last["received_at"] if last else None
        universe = {r["label"] for r in store.q("SELECT DISTINCT label FROM newprod_daily WHERE day BETWEEN ? AND ?",
                                                (fs.isoformat(), end.isoformat()))}
    elif snap:
        src, end, received = "monthly", min(dt.date.fromisoformat(snap["period_to"]), fe, today), snap["received_at"]
        universe = set(snap["monthly"])
        notes.append("ラベル×日の売上がまだ年度の始まりから届いていないため、週次レポートの月別の表で数えています")
        months_have = {m for b in snap["monthly"].values() for m in b.get("months", [])}
        if snap["monthly"] and f"{fy:04d}-05" not in months_have and end >= fs:
            notes.append(f"週次レポートの月別の表に {fy}年5月がありません（{fy}年5月〜の分が未取込）")
    else:
        return {"fy": fy, "from": fs.isoformat(), "to": None, "source": None, "total": None, "provisional": True,
                "notes": ["まだ週次レポートもラベル×日の売上も届いていません"], "labels": [], "unlinked": [], "increment": []}
    if end < fs:
        end = fs - dt.timedelta(days=1)
    linked = _linked()
    rows, unlinked, total, provisional = [], [], 0.0, src != "daily"
    for lab in sorted(set(linked) | universe):
        L = linked.get(lab)
        if L is None:                                            # 発売日が分からない（案件と結んでいない）
            amt = _amount(src, snap, lab, fs, end, [])
            if amt:
                unlinked.append({"label": lab, "amount": amt})
            continue
        r = {"label": lab, "launch": L["launch"], "projects": L["projects"], "basis": sorted(L["basis"]),
             "amount": None, "from": None, "to": None, "why": None, "skipped": []}
        if not L["launch"]:
            r["why"] = "案件に発売日がありません"
            provisional = True
            rows.append(r)
            continue
        ld = dt.date.fromisoformat(L["launch"])
        a, b = max(fs, ld), min(one_year_end(ld) - dt.timedelta(days=1), end)
        if a > b:
            r["amount"] = 0.0
            r["why"] = ("年度より前に1年を過ぎました" if one_year_end(ld) <= fs else
                        "まだ発売前です" if ld > end else "この年度の実績がまだありません")
            rows.append(r)
            continue
        r["from"], r["to"] = a.isoformat(), b.isoformat()
        r["amount"] = _amount(src, snap, lab, a, b, r["skipped"], end)
        if r["amount"] is None:
            r["why"] = "週次レポートの月別の表にこのラベルがありません"
            provisional = True
        else:
            total += r["amount"]
        if r["skipped"]:
            provisional = True
        rows.append(r)
    inc = [r["label"] for r in rows if "増分" in r["basis"]]
    if inc:
        notes.append(f"「増分」の案件のラベル {len(inc)} 件も、週次レポートと同じ全額で入っています")
    if any(r["skipped"] for r in rows):
        notes.append("発売の月・発売から1年の境の月は、月の数字を日で区切れないので合計に入れていません（ラベル×日の売上が届くと入ります）")
    if src == "daily":
        dbl = newprod_daily.health()["double"]
        if dbl:
            notes.append(f"同じ案件の古い名前と新しい名前で、同じ日の売上を二重に数えているおそれが {len(dbl)} 組あります"
                         "（設定の「自動送信」で確かめてください）")
    if unlinked:
        notes.append(f"案件と結んでいないラベル {len(unlinked)} 件（{sum(u['amount'] for u in unlinked):,.0f}円）は、発売日が分からないので合計に入れていません")
    return {"fy": fy, "from": fs.isoformat(), "to": end.isoformat() if end >= fs else None, "source": src,
            "received_at": received, "total": total, "provisional": provisional, "notes": notes,
            "labels": rows, "unlinked": unlinked, "increment": inc}


def _amount(src, snap, lab, a: dt.date, b: dt.date, skipped: list, end: dt.date | None = None) -> float | None:
    """a〜b（両端を含む）の金額。月別の表では、まるごと入る月だけ足し、区切れない月は skipped に入れる。"""
    from app import newprod_daily
    if src == "daily":
        return newprod_daily.sum_between([lab], a.isoformat(), b.isoformat()) or 0.0
    blk = (snap or {}).get("monthly", {}).get(lab)
    if not blk or "sales" not in blk:
        return None
    end = end or b
    s, m = 0.0, dt.date(a.year, a.month, 1)
    while m <= b:
        me = _month_end(m)
        ym = m.strftime("%Y-%m")
        whole = a <= m and (me <= b or (b == end and m <= end <= me))     # 実績の最後の月は、最後の日までで「まるごと」
        if ym in blk["months"]:
            i = blk["months"].index(ym)
            v = blk["sales"][i] if i < len(blk["sales"]) else None
            if whole:
                s += v or 0
            elif v:
                skipped.append({"month": ym, "amount": v})
        elif whole:
            skipped.append({"month": ym, "amount": None})
        m = me + dt.timedelta(days=1)
    return s
