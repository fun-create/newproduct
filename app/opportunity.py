#!/usr/bin/env python3
"""
機会カレンダー（F-2・FR-78〜81）。

- 年間イベント（暦）・ライフイベント（採点つき）は 2026-09-23 に取り込み済み（ADR-032）。画面はここ
- **発売の目安はイベントの2か月前**（F-2-3・FR-80）。そこから年間プランの枠を1操作で作る。
  **自動で枠を作らない**（候補を出し、人が押す）。日付の原文（「11月15日前後の土日」等）は直さない
- 今週のトレンド上位（FCTR）を重ねて出す（FR-81）。時限スコアなので暦とは混ぜない
"""
from __future__ import annotations

from app import plan, store

LEAD_MONTHS = 2


def _ym(y: int, m: int) -> str:
    y += (m - 1) // 12
    return f"{y:04d}-{(m - 1) % 12 + 1:02d}"


def _target_version():
    """枠を足す先＝**策定中の版の、いちばん新しい年度**。無ければ None。"""
    return store.one("SELECT * FROM plan_version WHERE state='策定中' ORDER BY fiscal_year DESC, id DESC LIMIT 1")


def calendar() -> dict:
    today = store.today()
    v = _target_version()
    have = set()
    if v is not None:
        have = {r["occasion"] for r in store.q(
            "SELECT occasion FROM plan_slot WHERE version_id=? AND occasion IS NOT NULL", (v["id"],))}
    annual = []
    for r in store.q("SELECT * FROM theme WHERE kind='年間イベント' AND active=1 ORDER BY month, sort"):
        m = r["month"]
        # 次に来る回（今月より前の月なら来年）
        y = today.year if m is None or m >= today.month else today.year + 1
        launch = _ym(y, m - LEAD_MONTHS) if m else None
        annual.append({"id": r["id"], "label": r["label"], "month": m, "day": r["day"],
                       "sellable": bool(r["sellable"]), "note": r["note"],
                       "event_month": _ym(y, m) if m else None, "launch_month": launch,
                       "in_plan": r["label"] in have})
    life = store.rows(store.q(
        "SELECT t.id, t.label, t.product_gap, t.product_ideas, s.gift_intent, s.photo_fit, s.frequency, "
        "s.total, s.priority FROM theme t LEFT JOIN theme_score s ON s.theme_id=t.id "
        "WHERE t.kind='ライフイベント' AND t.active=1 ORDER BY (s.total IS NULL), s.total DESC, s.priority, t.sort"))
    for x in life:
        x["in_plan"] = x["label"] in have
    try:
        from app import fctr
        b = fctr.board()
        trends = {"week": b["latest_week"], "why": b["why"],
                  "segments": [{"name": s["name"], "top": [t for t in s["themes"] if t["top"]]}
                               for s in b["segments"]]}
    except Exception as e:                          # 暦は FCTR が無くても見られる
        trends = {"week": None, "why": str(e), "segments": []}
    return {"lead_months": LEAD_MONTHS, "today": today.isoformat(),
            "version": dict(v) if v else None, "annual": annual, "life": life, "trends": trends}


def to_slot(theme_id: str, user_id: str) -> dict:
    """イベントの2か月前を発売の目安にして、策定中の年間プランへ枠を1つ足す。"""
    t = store.one("SELECT * FROM theme WHERE id=? AND kind IN ('年間イベント','ライフイベント') AND active=1", (theme_id,))
    if t is None:
        raise LookupError("その機会はありません")
    v = _target_version()
    if v is None:
        raise ValueError("策定中の年間プランがありません（プランの画面で版を作ってください）")
    if store.one("SELECT 1 FROM plan_slot WHERE version_id=? AND occasion=?", (v["id"], t["label"])):
        raise ValueError(f"「{t['label']}」の枠は、この版に既にあります")
    f = {"version_id": v["id"], "occasion": t["label"],
         "note": f"機会カレンダーから（{t['kind']}）"}
    if t["kind"] == "年間イベント" and t["month"]:
        today = store.today()
        y = today.year if t["month"] >= today.month else today.year + 1
        f["launch_month"] = _ym(y, t["month"] - LEAD_MONTHS)
        f["note"] += f"。発売の目安＝イベント {t['month']}月 の{LEAD_MONTHS}か月前"
    else:
        raise ValueError("ライフイベントは月が決まっていないので、プランの画面で発売月を決めて枠を作ってください")
    return plan.create_slot(user_id, **f)
