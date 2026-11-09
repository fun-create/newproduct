#!/usr/bin/env python3
"""
発売後の改善要望（2026-11-09 十文字さん・ADR-132）。

受け付けて、リストにし、優先順位・担当部署（担当区分）・担当者・期限を付け、タスクにする。
**タスク化は案件外の仕事（work_item）を1件作る。**担当者と期限が入っていれば、今の仕組み（FR-121）でカレンダーに出る。
要望を直すと、まだ終わっていないタスクにも題・担当・期限を写す。タスクを完了にすると、要望も「完了」にする。
**消さない。**要らなくなったら「見送り」。直した中身は監査ログ（improve.edit）に残る。
"""
from __future__ import annotations

import datetime as dt

from app import store

PRIORITIES = ("高", "中", "低", "未設定")
STATUSES = ("受付", "検討中", "対応中", "完了", "見送り")
OPEN = ("受付", "検討中", "対応中")
FIELDS = ("title", "detail", "source", "project_id", "priority", "status", "role", "assignee", "due_on", "received_on")
LABEL = {"title": "要望", "detail": "くわしく", "source": "受付元", "project_id": "商品", "priority": "優先度",
         "status": "状態", "role": "担当部署", "assignee": "担当者", "due_on": "期限", "received_on": "受付日"}
_ORDER = "CASE priority WHEN '高' THEN 0 WHEN '中' THEN 1 WHEN '低' THEN 2 ELSE 3 END"


def _clean(f: dict, partial: bool) -> dict:
    out = {}
    for k in FIELDS:
        if k not in f and partial:
            continue
        v = (str(f.get(k)) if f.get(k) is not None else "").strip()
        if k == "title":
            if not v:
                raise ValueError("要望（何を直すか）を入れてください")
            v = v[:200]
        elif k == "detail":
            v = v[:4000]
        elif k == "source":
            v = v[:100]
        elif k == "priority":
            v = v or "未設定"
            if v not in PRIORITIES:
                raise ValueError("優先度は 高・中・低・未設定 から選んでください")
        elif k == "status":
            v = v or "受付"
            if v not in STATUSES:
                raise ValueError("状態は " + "・".join(STATUSES) + " から選んでください")
        elif k == "project_id" and v:
            if store.one("SELECT 1 FROM project WHERE id=?", (v,)) is None:
                raise ValueError("その商品（案件）はありません")
        elif k == "role" and v:
            if store.one("SELECT 1 FROM role WHERE code=?", (v,)) is None:
                raise ValueError("その担当部署はありません")
        elif k in ("due_on", "received_on") and v:
            try:
                v = dt.date.fromisoformat(v).isoformat()
            except ValueError:
                raise ValueError(f"{LABEL[k]}は 2026-12-01 の形で入れてください") from None
        out[k] = v or None
    if not partial:
        out["priority"] = out.get("priority") or "未設定"
        out["status"] = out.get("status") or "受付"
        out["received_on"] = out.get("received_on") or store.today_s()
    return out


def create(f: dict, user_id: str) -> dict:
    v = _clean(f, partial=False)
    with store.tx() as c:
        rid = c.execute(
            "INSERT INTO improve_request (title,detail,source,project_id,priority,status,role,assignee,due_on,"
            "received_on,received_by,updated_at,updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (v["title"], v["detail"], v["source"], v["project_id"], v["priority"], v["status"], v["role"],
             v["assignee"], v["due_on"], v["received_on"], user_id, store.now_s(), user_id)).lastrowid
    store.audit(user_id, "improve.create", str(rid), {k: v[k] for k in ("title", "priority", "project_id")})
    return {"ok": True, "id": rid}


def _sync_task(c, r: dict) -> None:
    """タスクにしてあれば、まだ終わっていないタスクへ題・担当・期限を写す。要望を完了・見送りにしたらタスクも閉じる"""
    if not r["work_item_id"]:
        return
    w = c.execute("SELECT status FROM work_item WHERE id=?", (r["work_item_id"],)).fetchone()
    if w is None or w["status"] in ("完了", "対象外"):
        return
    st = "完了" if r["status"] == "完了" else "対象外" if r["status"] == "見送り" else w["status"]
    c.execute("UPDATE work_item SET title=?, role=?, assignee=?, due_on=?, status=?, project_id=?, "
              "done_at=CASE WHEN ?='完了' THEN ? ELSE done_at END WHERE id=?",
              (_task_title(r), r["role"], r["assignee"], r["due_on"], st, r["project_id"], st, store.now_s(),
               r["work_item_id"]))


def _task_title(r: dict) -> str:
    return f"改善要望: {r['title']}"


def update(rid: int, f: dict, user_id: str) -> dict:
    old = store.one("SELECT * FROM improve_request WHERE id=?", (rid,))
    if old is None:
        raise LookupError("その改善要望はありません")
    v = _clean(f, partial=True)
    changed = {k: (old[k], x) for k, x in v.items() if (old[k] or None) != x}
    if not changed:
        return {"ok": True, "changed": 0}
    sets = ",".join(f"{k}=?" for k in changed) + ",updated_at=?,updated_by=?"
    with store.tx() as c:
        c.execute(f"UPDATE improve_request SET {sets} WHERE id=?",
                  [x for _o, x in changed.values()] + [store.now_s(), user_id, rid])
        _sync_task(c, dict(c.execute("SELECT * FROM improve_request WHERE id=?", (rid,)).fetchone()))
    store.audit(user_id, "improve.edit", str(rid), {LABEL[k]: [o, n] for k, (o, n) in changed.items()})
    return {"ok": True, "changed": len(changed)}


def to_task(rid: int, user_id: str) -> dict:
    """タスクにする（案件外の仕事を1件作る）。二度押しても1件。担当部署か担当者のどちらかが要る"""
    r = store.one("SELECT * FROM improve_request WHERE id=?", (rid,))
    if r is None:
        raise LookupError("その改善要望はありません")
    r = dict(r)
    if r["work_item_id"]:
        return {"ok": True, "id": r["work_item_id"], "created": False}
    if not (r["role"] or r["assignee"]):
        raise ValueError("タスクにするには、担当部署か担当者を入れてください")
    if r["status"] in ("完了", "見送り"):
        raise ValueError(f"{r['status']}の要望はタスクにしません")
    with store.tx() as c:
        wid = c.execute(
            "INSERT INTO work_item (kind,title,category,role,assignee,due_on,status,created_at,project_id,origin) "
            "VALUES ('案件外',?,?,?,?,?,'未着手',?,?,?)",
            (_task_title(r), "改善要望", r["role"], r["assignee"], r["due_on"], store.now_s(), r["project_id"],
             f"improve-{rid}")).lastrowid
        c.execute("UPDATE improve_request SET work_item_id=?, status=CASE WHEN status IN ('受付','検討中') THEN '対応中' "
                  "ELSE status END, updated_at=?, updated_by=? WHERE id=?", (wid, store.now_s(), user_id, rid))
    store.audit(user_id, "improve.to_task", str(rid), {"work_item": wid})
    return {"ok": True, "id": wid, "created": True,
            "calendar": "カレンダーに出ます" if (r["assignee"] and r["due_on"]) else
            "担当者と期限が入るとカレンダーに出ます（いまは" + ("担当者" if not r["assignee"] else "期限") + "が空）"}


def _close_done_tasks() -> None:
    """タスクを完了にした要望は「完了」に（画面で見るたびにそろえる）"""
    store.ex("UPDATE improve_request SET status='完了', updated_at=? WHERE status IN ('受付','検討中','対応中') "
             "AND work_item_id IN (SELECT id FROM work_item WHERE status='完了')", (store.now_s(),))


def listing(state: str = "open", project_id: str = "") -> dict:
    _close_done_tasks()
    where, p = ["1=1"], []
    if state == "open":
        where.append("i.status IN ('受付','検討中','対応中')")
    elif state in STATUSES:
        where.append("i.status=?"); p.append(state)
    if project_id:
        where.append("i.project_id=?"); p.append(project_id)
    rows = store.rows(store.q(
        "SELECT i.*, r.label AS role_label, pr.internal_name AS project_name, w.status AS task_status "
        "FROM improve_request i LEFT JOIN role r ON r.code=i.role LEFT JOIN project pr ON pr.id=i.project_id "
        f"LEFT JOIN work_item w ON w.id=i.work_item_id WHERE {' AND '.join(where)} "
        f"ORDER BY {_ORDER.replace('priority', 'i.priority')}, (i.due_on IS NULL), i.due_on, i.id", p))
    counts = {s: store.val("SELECT COUNT(*) FROM improve_request WHERE status=?", (s,), 0) for s in STATUSES}
    return {"rows": rows, "counts": counts, "priorities": PRIORITIES, "statuses": STATUSES,
            "today": store.today_s()}
