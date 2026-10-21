"""
進捗管理シートとの同期の跡を、画面から見るための読み出し（点検 H-1 の4・ADR-119）。

同期（`tools/sync_sheet.py`）は、書いた値の指紋を `sheet_sync` に残す。**いまの値の指紋が違う行**は、
前回の同期の後にアプリ側で変わった行で、次の同期では上書きしない（衝突）。並行運用（11/8 まで）は
シートが正なので、こういう行は管理者が確かめてシートかアプリのどちらかに揃える。
指紋の作り方は同期と同じでなければ意味が無いので、**ここを正にして同期もここから読む。**
"""
from __future__ import annotations

import hashlib
import json

from app import store


def digest(*vals) -> str:
    return hashlib.sha256(json.dumps(vals, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def vals(r) -> tuple:
    """同期が見る5つ（進捗・開始・期限・ロール・時間）。"""
    return (r["status"], r["start_on"], r["due_on"], r["role"], r["hours"])


def diff_ids(kind: str) -> set[int]:
    """前回の同期の後にアプリで変わった行（kind は task / work_item）。"""
    table = "task" if kind == "task" else "work_item"
    out = set()
    for r in store.q(f"SELECT x.id, x.status, x.start_on, x.due_on, x.role, x.hours, s.digest FROM {table} x "
                     f"JOIN sheet_sync s ON s.kind=? AND s.local_id=CAST(x.id AS TEXT)", (kind,)):
        if r["digest"] != digest(*vals(r)):
            out.add(r["id"])
    return out


def summary() -> dict:
    """設定の「シートとの同期」。最後の同期と、シートと違う行の一覧。"""
    from app import cutover, project as _p
    last = store.one("SELECT sheet_date, applied_at FROM sheet_sync_run ORDER BY id DESC LIMIT 1")
    rows = []
    tids = diff_ids("task")
    if tids:
        marks = ",".join("?" * len(tids))
        for r in store.q(f"SELECT t.id, t.title, t.status, t.due_on, t.project_id, p.internal_name, p.cat1, p.cat2, p.cat3, p.size, "
                         f"p.launch_date FROM task t LEFT JOIN project p ON p.id=t.project_id WHERE t.id IN ({marks}) "
                         f"ORDER BY p.launch_date, t.seq", sorted(tids)):
            rows.append({"kind": "task", "id": r["id"], "title": r["title"], "status": r["status"], "due_on": r["due_on"],
                         "project_id": r["project_id"], "project": _p.display_name(dict(r))})
    wids = diff_ids("work_item")
    if wids:
        marks = ",".join("?" * len(wids))
        for r in store.q(f"SELECT id, title, status, due_on, category FROM work_item WHERE id IN ({marks}) ORDER BY due_on",
                         sorted(wids)):
            rows.append({"kind": "work_item", "id": r["id"], "title": r["title"], "status": r["status"], "due_on": r["due_on"],
                         "project_id": None, "project": "案件外（" + (r["category"] or "区分なし") + "）"})
    return {"phase": cutover.phase(), "sheet_until": cutover.last_sheet_day(),
            "last_sheet_date": last["sheet_date"] if last else None, "last_applied_at": last["applied_at"] if last else None,
            "rows": rows}
