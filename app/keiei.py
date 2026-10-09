#!/usr/bin/env python3
"""
経営管理（keiei）の承認済みの年度の計画を読む（FR-125・2026-10-09・ADR-076）。**正はあちら。複製しない。**

`GET http://127.0.0.1:8792/api/plan?fy=2026`・`Authorization: Bearer <config/keiei_token>`（この道だけ通る専用のもの）
→ {fy, months, version, approved_at, plan:[{fy,term,month,dept_key,metric,value,version}], kpi:[…], departments:[…], reason}
- **承認済みの版だけ**が返る。版が無い年度は version null・plan [] と理由（空と「まだ無い」を混ぜない）
- 計画は月ごと。年度の途中から立てた計画は、その月から（例 2026年度は 2026-09〜2027-04 の8か月）
- 開くたびに読む（5分だけ手元に置く）。DB に写さない。version が変われば、次に読んだときから新しい値
- `X-Actor` は ASCII だけ
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.environ.get("NEWPRODUCT_KEIEI_TOKEN") or os.path.join(BASE, "config", "keiei_token")
API = os.environ.get("NEWPRODUCT_KEIEI_API") or "http://127.0.0.1:8792/api/plan"
ACTOR = "newproduct/simulate"
_CACHE: dict[int, tuple[float, dict]] = {}
_TTL = 300


class NotConnected(RuntimeError):
    """読めない（鍵が無い・つながらない）。**「計画0円」ではない。**"""


def plan(fy: int, opener=None) -> dict:
    hit = _CACHE.get(fy)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            tok = f.read().strip()
    except OSError as e:
        raise NotConnected("経営管理とまだつながっていません（読むための鍵がありません）") from e
    req = urllib.request.Request(f"{API}?fy={int(fy)}", headers={"Authorization": f"Bearer {tok}", "X-Actor": ACTOR})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=5) as r:
            doc = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise NotConnected(f"経営管理が {e.code} を返しました") from e
    except OSError as e:
        raise NotConnected(f"経営管理につながりません: {e}") from e
    except ValueError as e:
        raise NotConnected(f"経営管理の返事を読めません: {e}") from e
    _CACHE[fy] = (time.time(), doc)
    return doc


def revenue_by_dept(fy: int, doc: dict | None = None) -> dict:
    """部門ごとの売上計画（年度の合計）と、計画のある月。"""
    doc = doc if doc is not None else plan(fy)
    labels = {d.get("key"): d.get("label") for d in doc.get("departments") or []}
    tot, months = {}, set()
    for x in doc.get("plan") or []:
        if x.get("metric") != "revenue":
            continue
        tot[x["dept_key"]] = tot.get(x["dept_key"], 0) + (x.get("value") or 0)
        months.add(x["month"])
    return {"fy": fy, "version": doc.get("version"), "approved_at": doc.get("approved_at"), "reason": doc.get("reason"),
            "months": sorted(months), "depts": [{"key": k, "label": labels.get(k, k), "revenue": round(v)}
                                                for k, v in sorted(tot.items(), key=lambda kv: -kv[1])]}
