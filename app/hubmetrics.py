#!/usr/bin/env python3
"""
HUB へ指標を書き出す（FR-123・2026-10-09 十文字さんの選択・ADR-091）。

指標の置き場は Auto GROWTH の metrics（HUB `kpi/README.md`「kpi/ は退役」）。画面は https://autogrowth.fun-create.co.jp/metrics
送り先は同じサーバの `POST http://127.0.0.1:8789/api/metrics`（Auto GROWTH の内部の口。loopback から・転送ヘッダ無しで呼ぶ）。

書き出すもの（source は `newproduct`・月ごとは captured_at を月末 `YYYY-MM-DD 23:59:00`・dims は `{"window": "month"}`）:
- `revenue_newproduct`: 新商品（発売から12か月以内）がその月に売った額（税込の商品代）。「新商品売上状況」の表から。
  **表に入っているのは 2022年度（2022-05〜）発売の商品から**なので、12か月以内の商品が全部そろう 2023-04 から、表の最後の月まで
- `launches`: その月に発売した新商品の数（同じ表から・2022-05〜表の最後の月）。発売の無かった月の 0 は数えたうえでの 0
- `ideas_created`: NEW PRODUCT で起票したアイデアの数（移行分は起票日が分からないので入れない）。本格的に使い始めた 2026-10 から、締まった月だけ
- `concept_stock_months`: コンセプト在庫の月数。**未計測のあいだは書かない**（0 と書かない・N-10）。出せたときに、その時点の値を dims `{"window": "snapshot"}` で

守ること:
- **送った点を覚え、同じ (metric, dims, captured_at) は二度送らない**（向こうは置き換えないので、送り直すと行が増える）
- 値が後から変わった点（表の差し替えなど）は送らずに「変わった」と返す（向こうで置き換える手段が要る）
- 送れなかった点は覚えない（次の回にもう一度送る）
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import os
import urllib.error
import urllib.request

from . import store

SOURCE = "newproduct"
ENDPOINT = os.environ.get("NEWPRODUCT_HUB_METRICS", "http://127.0.0.1:8789/api/metrics")
DIMS_MONTH = json.dumps({"window": "month"}, ensure_ascii=False)
DIMS_SNAPSHOT = json.dumps({"window": "snapshot"}, ensure_ascii=False)
REVENUE_FROM = "2023-04"
LAUNCH_FROM = "2022-05"
IDEAS_FROM = "2026-10"


def _month_end(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-{calendar.monthrange(y, m)[1]:02d} 23:59:00"


def _add(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def _months(a: str, b: str) -> list[str]:
    out, m = [], a
    while m <= b:
        out.append(m)
        m = _add(m, 1)
    return out


def build(today: dt.date | None = None) -> dict:
    """送る点を作る（送らない）。{"rows": [...], "notes": [...]}"""
    today = today or store.today()
    this_month = today.strftime("%Y-%m")
    rows, notes = [], []
    pp = store.rows(store.q("SELECT launch_date, months_json FROM past_product WHERE launch_date IS NOT NULL"))
    rev: dict[str, float] = {}
    for p in pp:
        for k, v in enumerate(json.loads(p["months_json"] or "[]")[:12]):
            ym = _add(p["launch_date"][:7], k)
            rev[ym] = rev.get(ym, 0.0) + float(v or 0)
    # 表の最後の月＝**全商品の合計が 0 でない最後の月**（取り込みの data_end と同じ定義）。
    # 表は数字の無い先の月まで 0 で埋めてあるので、商品ごとの月数から出すと先へ延びて 0 を送ってしまう（2026-10-09 に気づいた）
    filled = [ym for ym, v in rev.items() if v > 0]
    data_end = min(max(filled), _add(this_month, -1)) if filled else None
    if data_end:
        for ym in _months(REVENUE_FROM, data_end):
            rows.append({"metric": "revenue_newproduct", "dims": DIMS_MONTH, "captured_at": _month_end(ym),
                         "value": round(rev.get(ym, 0.0))})
        launches: dict[str, int] = {}
        for p in pp:
            launches[p["launch_date"][:7]] = launches.get(p["launch_date"][:7], 0) + 1
        for ym in _months(LAUNCH_FROM, data_end):
            rows.append({"metric": "launches", "dims": DIMS_MONTH, "captured_at": _month_end(ym),
                         "value": launches.get(ym, 0)})
        notes.append(f"新商品の売上と発売本数は「新商品売上状況」の表から（売上 {REVENUE_FROM}〜{data_end}・本数 {LAUNCH_FROM}〜{data_end}）")
    else:
        notes.append("「新商品売上状況」の表が取り込まれていないので、売上と発売本数は書きません")
    last_closed = _add(this_month, -1)
    if last_closed >= IDEAS_FROM:
        cnt = {r[0]: r[1] for r in store.q("SELECT substr(created_at,1,7), COUNT(*) FROM idea WHERE source_sheet IS NULL "
                                          "AND created_at IS NOT NULL GROUP BY 1")}
        for ym in _months(IDEAS_FROM, last_closed):
            rows.append({"metric": "ideas_created", "dims": DIMS_MONTH, "captured_at": _month_end(ym),
                         "value": cnt.get(ym, 0)})
    else:
        notes.append(f"起票数は {IDEAS_FROM} が締まってから書きます")
    from . import idea
    cs = idea.concept_stock()
    if cs.get("value") is not None:
        rows.append({"metric": "concept_stock_months", "dims": DIMS_SNAPSHOT,
                     "captured_at": today.isoformat() + " 23:59:00", "value": cs["value"]})
    else:
        notes.append(f"コンセプト在庫の月数は未計測なので書きません（{cs.get('why') or cs.get('state')}）")
    return {"rows": rows, "notes": notes}


def plan(today: dt.date | None = None) -> dict:
    """送る点のうち、**まだ送っていないもの**と、**送った値と違うもの**を分ける。"""
    b = build(today)
    sent = {(r["metric"], r["dims"], r["captured_at"]): r["value"] for r in store.q("SELECT * FROM hub_metric_sent")}
    new, changed = [], []
    for r in b["rows"]:
        k = (r["metric"], r["dims"], r["captured_at"])
        if k not in sent:
            new.append(r)
        elif abs(float(sent[k]) - float(r["value"])) > 1e-9:
            changed.append({**r, "sent_value": sent[k]})
    return {"new": new, "changed": changed, "notes": b["notes"], "total": len(b["rows"])}


def push(today: dt.date | None = None, opener=None, chunk: int = 200) -> dict:
    p = plan(today)
    done = 0
    for i in range(0, len(p["new"]), chunk):
        part = p["new"][i:i + chunk]
        body = json.dumps([{"source": SOURCE, "metric": r["metric"], "value": r["value"],
                            "dims": json.loads(r["dims"]), "captured_at": r["captured_at"]} for r in part],
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Actor": "newproduct/hubmetrics"})
        try:
            with (opener or urllib.request.urlopen)(req, timeout=15) as res:
                ans = json.loads(res.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {**p, "sent": done, "error": f"送れませんでした（{type(e).__name__}: {e}）。送れた {done} 点だけを覚えました"}
        if ans.get("inserted") != len(part):
            return {**p, "sent": done, "error": f"受け取った数が合いません（送った {len(part)}・返事 {ans}）"}
        now = store.now_s()
        with store.tx() as c:
            c.executemany("INSERT INTO hub_metric_sent (metric,dims,captured_at,value,sent_at) VALUES (?,?,?,?,?)",
                          [(r["metric"], r["dims"], r["captured_at"], r["value"], now) for r in part])
        done += len(part)
    return {**p, "sent": done, "error": None}
