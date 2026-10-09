#!/usr/bin/env python3
"""
タスクの目安の日付（FR-41・F-5-2 後半・2026-10-09 十文字さんの選択・ADR-084）。

十文字さんの選択:
- **目安として出し、担当が確定する。**計算した日付は `plan_start / plan_due` に置き、期限（`start_on / due_on`）には入れない。
  人が「目安を期限にする」を押したときだけ期限になる（ADR-012 の「誰も決めていない期限を入れない」を守る）
- **発売日までを工数の比で割る。**今日（または次の営業日）から発売日の前の営業日までの営業日を、
  タスクの順に標準工数（h）の比で割り当てる。新しい仮定（1日の作業時間など）を足さない

守ること:
- 営業日は**カレンダーアプリの会社休業日だけ**で決める（`app/calfc.py`）。土日を自分で足さない。
  **カレンダーに登録の無い日がかかるなら計算しない**（分からない日を営業日と見なさない）
- 発売日の無い案件・営業日が0日の案件・標準工数が全部空の案件は、目安を出さず理由を返す（推測しない）
- 完了・対象外のタスクは割り当てない。標準工数の空いたタスクは 0h として、前のタスクと同じ日に置く
- 期限を人が入れてあるタスクは、目安を期限にしても**上書きしない**
"""
from __future__ import annotations

import datetime as dt
import math

from . import calfc
from . import store

SKIP = ("完了", "対象外")


def business_days(fr: dt.date, to: dt.date, doc: dict | None = None) -> list[str]:
    """[fr, to] の営業日。**登録の外の日が1日でもあれば NotConnected**（分からない日を数えない）。"""
    if to < fr:
        return []
    doc = doc if doc is not None else calfc.fetch(fr.isoformat(), to.isoformat())
    closed = set(doc.get("closed") or [])
    covered = list(doc.get("covered") or [])
    out, d = [], fr
    while d <= to:
        s = d.isoformat()
        if not any(c.get("from", "") <= s <= c.get("to", "") for c in covered):
            raise calfc.NotConnected(f"{s} がカレンダーアプリに登録されていません（休みかどうか分からない日は数えません）")
        if s not in closed:
            out.append(s)
        d += dt.timedelta(days=1)
    return out


def compute(project_id: str, doc: dict | None = None) -> dict:
    """目安を計算する（保存はしない）。{"ok", "why", "days", "rows": [{id, plan_start, plan_due}]}"""
    p = store.one("SELECT launch_date FROM project WHERE id=?", (project_id,))
    if p is None:
        raise LookupError("案件がありません")
    if not p["launch_date"]:
        return {"ok": False, "why": "発売予定日が決まっていないので、目安は出しません（仮の日付を置かない）", "rows": []}
    launch = dt.date.fromisoformat(p["launch_date"])
    start = store.today()
    tasks = store.rows(store.q("SELECT id, seq, hours, status FROM task WHERE project_id=? ORDER BY seq, id",
                               (project_id,)))
    tasks = [t for t in tasks if t["status"] not in SKIP]
    if not tasks:
        return {"ok": False, "why": "割り当てるタスクがありません（完了・対象外を除く）", "rows": []}
    try:
        days = business_days(start, launch - dt.timedelta(days=1), doc)
    except calfc.NotConnected as e:
        return {"ok": False, "why": str(e), "rows": []}
    if not days:
        return {"ok": False, "why": f"今日から発売日（{p['launch_date']}）の前日までに営業日がありません", "rows": []}
    hs = [max(float(t["hours"] or 0), 0.0) for t in tasks]
    total = sum(hs)
    if total <= 0:
        return {"ok": False, "why": "標準工数が全部空なので、比で割れません", "rows": []}
    n, cum, rows = len(days), 0.0, []
    for t, h in zip(tasks, hs):
        a = min(int(math.floor(cum / total * n)), n - 1)
        cum += h
        b = max(a, min(int(math.ceil(cum / total * n)) - 1, n - 1))
        rows.append({"id": t["id"], "seq": t["seq"], "plan_start": days[a], "plan_due": days[b]})
    return {"ok": True, "why": "", "rows": rows, "days": n, "from": days[0], "to": days[-1],
            "basis": f"今日から発売日（{p['launch_date']}）の前日までの営業日 {n} 日を、タスクの順に標準工数の比で割った目安"
                     "（営業日はカレンダーアプリの会社休業日から）"}


def refresh(project_id: str, user_id: str, doc: dict | None = None) -> dict:
    """目安を計算して保存する。**期限（due_on）は触らない。**"""
    r = compute(project_id, doc)
    now = store.now_s()
    with store.tx() as c:
        c.execute("UPDATE task SET plan_start=NULL, plan_due=NULL, plan_at=NULL WHERE project_id=?", (project_id,))
        for x in r["rows"]:
            c.execute("UPDATE task SET plan_start=?, plan_due=?, plan_at=? WHERE id=?",
                      (x["plan_start"], x["plan_due"], now, x["id"]))
        if r["ok"]:
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what) VALUES (?,?,?,?)",
                      (project_id, now, user_id, f"タスクの目安を計算（{len(r['rows'])}件・営業日 {r['days']} 日）"))
    return {k: r.get(k) for k in ("ok", "why", "days", "from", "to", "basis")} | {"n": len(r["rows"])}


def adopt(project_id: str, user_id: str) -> dict:
    """目安を期限にする。**期限が入っているタスクは上書きしない。**押した人を履歴に残す。"""
    now = store.now_s()
    with store.tx() as c:
        n = c.execute("UPDATE task SET start_on=COALESCE(start_on, plan_start), due_on=plan_due "
                      "WHERE project_id=? AND plan_due IS NOT NULL AND due_on IS NULL "
                      "AND status NOT IN ('完了','対象外')", (project_id,)).rowcount or 0
        kept = c.execute("SELECT COUNT(*) FROM task WHERE project_id=? AND plan_due IS NOT NULL "
                         "AND due_on IS NOT NULL AND due_on != plan_due", (project_id,)).fetchone()[0]
        if n:
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what) VALUES (?,?,?,?)",
                      (project_id, now, user_id, f"タスクの目安を期限にした（{n}件。期限が入っていた {kept} 件はそのまま）"))
    return {"adopted": n, "kept": kept}


def summary(project_id: str) -> dict:
    """画面に出す数。目安のあるタスク・まだ期限になっていないタスク。"""
    r = store.one("SELECT COUNT(*) AS n, SUM(plan_due IS NOT NULL) AS planned, "
                  "SUM(plan_due IS NOT NULL AND due_on IS NULL AND status NOT IN ('完了','対象外')) AS adoptable, "
                  "MAX(plan_at) AS plan_at FROM task WHERE project_id=?", (project_id,))
    return {"tasks": r["n"] or 0, "planned": r["planned"] or 0, "adoptable": r["adoptable"] or 0, "plan_at": r["plan_at"]}
