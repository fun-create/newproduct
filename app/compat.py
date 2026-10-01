#!/usr/bin/env python3
"""
対応確認の定型チェック（FR-102・F-8-7）。2026-10-01 十文字さん「商品開発部に決めてもらう」。

- **項目の中身は入れない**（発明しない）。ひな形は商品開発部・管理者が画面で作る
- 案件ではひな形を選ぶと項目が写る。**写した後にひな形を直しても、案件の記録は変わらない**
- 結果は 未確認／確認済／不可。**不可には理由（備考）が要る**
"""
from __future__ import annotations

import json

from app import gate, store

RESULTS = ("未確認", "確認済", "不可")
EDITOR_ROLES = ("devdept", "admin", "president")


def can_edit_templates(user_id: str) -> bool:
    return bool(set(gate.roles_of(user_id)) & set(EDITOR_ROLES))


def templates(active_only=True) -> list[dict]:
    rs = store.rows(store.q("SELECT * FROM compat_template " +
                            ("WHERE active=1 " if active_only else "") + "ORDER BY name"))
    for r in rs:
        r["items"] = json.loads(r.pop("items_json") or "[]")
    return rs


def save_template(f: dict, user_id: str) -> dict:
    if not can_edit_templates(user_id):
        raise PermissionError("ひな形を直せるのは、商品開発部・管理者・社長の業務ロールの人です")
    name = (f.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("商品の種類（ひな形の名前）を入れてください")
    items = []
    for ln in str(f.get("items") or "").splitlines():
        ln = ln.strip()[:80]
        if ln and ln not in items:
            items.append(ln)
    if not items:
        raise ValueError("確認する項目を1行に1つずつ入れてください")
    active = 0 if str(f.get("active", "1")) in ("0", "false") else 1
    now = store.now_s()
    with store.tx() as c:
        c.execute("INSERT INTO compat_template (name,items_json,active,updated_by,updated_at) "
                  "VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET items_json=excluded.items_json,"
                  "active=excluded.active,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                  (name, json.dumps(items, ensure_ascii=False), active, user_id, now))
    return {"ok": True, "name": name, "items": items}


def _editable(pid: str):
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません（R-2）")


def apply(pid: str, template_id, user_id: str) -> dict:
    """ひな形の項目を案件に写す。**既にある項目は上書きしない**（確認した記録を消さない）。"""
    _editable(pid)
    t = store.one("SELECT * FROM compat_template WHERE id=?", (int(template_id),))
    if t is None:
        raise LookupError("そのひな形はありません")
    n = 0
    with store.tx() as c:
        for it in json.loads(t["items_json"]):
            n += c.execute("INSERT OR IGNORE INTO compat_check (project_id,template_id,item,"
                           "updated_by,updated_at) VALUES (?,?,?,?,?)",
                           (pid, t["id"], it, user_id, store.now_s())).rowcount
    return {"ok": True, "added": n}


def set_result(pid: str, cid, result: str, note: str, user_id: str) -> dict:
    _editable(pid)
    if result not in RESULTS:
        raise ValueError("結果は " + "／".join(RESULTS) + " から選んでください")
    note = (note or "").strip()[:300] or None
    if result == "不可" and not note:
        raise ValueError("「不可」には理由を入れてください")
    with store.tx() as c:
        n = c.execute("UPDATE compat_check SET result=?,note=?,updated_by=?,updated_at=? "
                      "WHERE id=? AND project_id=?",
                      (result, note, user_id, store.now_s(), int(cid), pid)).rowcount
    if not n:
        raise LookupError("その項目はこの案件にありません")
    return {"ok": True}


def overview(pid: str, user_id: str) -> dict:
    rows = store.rows(store.q("SELECT * FROM compat_check WHERE project_id=? ORDER BY id", (pid,)))
    return {"templates": templates(), "rows": rows, "results": RESULTS,
            "can_edit_templates": can_edit_templates(user_id),
            "summary": {r: sum(1 for x in rows if x["result"] == r) for r in RESULTS}}
