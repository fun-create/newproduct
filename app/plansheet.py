#!/usr/bin/env python3
"""
企画スプシの URL（FR-332 段階1・ADR-163）。

- 案件の頭の欄に「企画スプシ」を持つ。Google スプレッドシートの URL だけを受け、`…/spreadsheets/d/<id>` の形にそろえる
- **G0 を通したら**、URL が空なら「企画スプシの URL を入れる」タスクを1行足す（担当は案件の担当・期限は3営業日後）
- URL を入れたら、そのタスクを完了にする。同じ案件に2行は作らない（task.origin と一意の索引）
- 発売済・追跡中・中止の案件には足さない（企画を書く時期を過ぎている）

内容の毎日の写し（段階2）はここに足す。読み取りは NP 専用のアカウントで、Auto GROWTH の GA4 用は使わない。
"""
from __future__ import annotations

import re

from app import store

ORIGIN = "plan_sheet"
TITLE = "企画スプシの URL を入れる（カルテの頭の欄）"
ROLE = "devdept"
DUE_RULE = "L+3"            # G0 を通した日より後の3営業日目（会社の営業日・つながらなければ暦の日）
NO_TASK_STAGES = ("発売済", "追跡中", "中止")

_URL = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})(?:[/?#].*)?$")


def normalize(url: str | None) -> str | None:
    """空なら None。Google スプレッドシートの URL でなければ ValueError。"""
    u = (url or "").strip()
    if not u:
        return None
    m = _URL.match(u)
    if not m:
        raise ValueError("企画スプシは Google スプレッドシートの URL（https://docs.google.com/spreadsheets/d/…）で入れてください")
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}"


def ensure_task(pid: str, user_id: str, today: str | None = None) -> dict:
    """URL が空なら「URL を入れる」タスクを足す。足さなかったときは理由を返す。"""
    p = store.one("SELECT id, stage, owner, plan_sheet_url, source_of_truth FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    if p["source_of_truth"] != "app":
        return {"added": False, "why": "Drive 側が正本"}
    if p["plan_sheet_url"]:
        return {"added": False, "why": "URL は入っている"}
    if p["stage"] in NO_TASK_STAGES:
        return {"added": False, "why": f"{p['stage']}の案件"}
    if store.one("SELECT 1 FROM task WHERE project_id=? AND origin=?", (pid, ORIGIN)):
        return {"added": False, "why": "もうある"}
    from app import assign, followup
    base = today or store.today_s()
    due, note = followup.due_for(DUE_RULE, base)
    with store.tx() as c:
        seq = c.execute("SELECT COALESCE(MAX(seq),0)+1 FROM task WHERE project_id=? AND seq<900", (pid,)).fetchone()[0]
        tid = c.execute(
            "INSERT INTO task (project_id,seq,title,role,assignee,due_on,status,created_at,kind,origin) "
            "VALUES (?,?,?,?,?,?,'未着手',?,'実作業',?)",
            (pid, seq, TITLE, ROLE, assign.for_role(ROLE, p["owner"]), due, store.now_s(), ORIGIN)).lastrowid
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                  (pid, store.now_s(), user_id, f"タスクを足した: {seq} {TITLE}", f"G0 通過のあと・期限 {due}（{note}）"))
    return {"added": True, "id": tid, "seq": seq, "due_on": due}


def on_url_set(c, pid: str, url: str | None, user_id: str) -> int:
    """URL を入れたら「URL を入れる」タスクを完了にする（update_header のトランザクションの中で呼ぶ）。完了にした件数。"""
    if not url:
        return 0
    n = c.execute("UPDATE task SET status='完了', done_at=? WHERE project_id=? AND origin=? AND status NOT IN ('完了','対象外')",
                  (store.now_s(), pid, ORIGIN)).rowcount
    if n:
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what) VALUES (?,?,?,?)",
                  (pid, store.now_s(), user_id, f"タスクを完了: {TITLE}（URL を入れたため）"))
    return n


def backfill(user_id: str, apply: bool = False) -> dict:
    """いま G0 を通っていて、発売前で URL が空の案件に、タスクを足す（段階1を入れた日に1回）。既定は数えるだけ。

    保留中の案件には足さない（期限3営業日の行が止まったまま遅れに数えられる）。"""
    rows = store.q(
        "SELECT p.id FROM project p WHERE p.source_of_truth='app' AND p.plan_sheet_url IS NULL "
        "AND p.stage NOT IN ('発売済','追跡中','中止','保留') "
        "AND (SELECT result FROM gate_review g WHERE g.project_id=p.id AND g.gate='G0' ORDER BY g.id DESC LIMIT 1)='通過' "
        "AND NOT EXISTS (SELECT 1 FROM task t WHERE t.project_id=p.id AND t.origin=?) ORDER BY p.id", (ORIGIN,))
    ids = [r["id"] for r in rows]
    out = {"targets": ids, "added": []}
    if apply:
        for pid in ids:
            r = ensure_task(pid, user_id)
            if r.get("added"):
                out["added"].append({"project_id": pid, "seq": r["seq"], "due_on": r["due_on"]})
    return out


if __name__ == "__main__":          # python3 -m app.plansheet [--apply --by <user>]
    import argparse
    import json
    ap = argparse.ArgumentParser(description="G0 を通った発売前の案件に「企画スプシの URL を入れる」を足す")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--by", default="")
    a = ap.parse_args()
    if a.apply and not a.by:
        ap.error("--apply には --by（記録に残す人）が要ります")
    print(json.dumps(backfill(a.by or "dry-run", a.apply), ensure_ascii=False, indent=1))
