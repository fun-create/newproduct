#!/usr/bin/env python3
"""
発売予定日を変えたら、未完了タスクの開始日・期限を計算し直す（FR-225・ADR-124・2026-10-23 石原さんの依頼書 §2）。

依頼書の必須と希望:
- **完了済み（と対象外）のタスクの日程は、絶対に変えない**
- 営業日はカレンダーアプリの会社休業日（`schedule.business_days` と同じ。**登録の外の日がかかれば計算しない**）
- 発売前だけでなく発売後のタスクも
- 変わる前後を**一覧で見てから**反映する（`plan` は書かない・`apply` で書く）。**元に戻せる**（`undo`）
- 手で直した日程は「固定」（`task.date_locked`）にでき、固定は動かさない
- 計算できないタスクは理由を出す（ルールが無い・カレンダーに登録が無い・発売日が無い）
- ルールはタスクに持つ（案件を起こしたとき・初めて反映したとき）。テンプレートを改訂しても既存の案件のルールは変わらない
"""
from __future__ import annotations

import calendar
import datetime as dt
import json

from app import calfc, schedule_rules, store

SKIP = ("完了", "対象外")


def _edate(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


class Calendar:
    """営業日の並び（カレンダーアプリの会社休業日）。範囲の外・登録の外は NotConnected。"""

    def __init__(self, fr: dt.date, to: dt.date, doc: dict | None = None):
        doc = doc if doc is not None else calfc.fetch(fr.isoformat(), to.isoformat())
        self.closed = set(doc.get("closed") or [])
        self.covered = list(doc.get("covered") or [])
        self.fr, self.to = fr, to

    def is_work(self, d: dt.date) -> bool:
        s = d.isoformat()
        if not (self.fr <= d <= self.to) or not any(c.get("from", "") <= s <= c.get("to", "") for c in self.covered):
            raise calfc.NotConnected(f"{s} がカレンダーアプリに登録されていません（休みかどうか分からない日では計算しません）")
        return s not in self.closed

    def step(self, d: dt.date, n: int, sign: int) -> dt.date:
        """d より前（sign=-1）／後（+1）の n 番目の営業日。d 自身は数えない（シートの式と同じ）。"""
        k = 0
        while k < n:
            d += dt.timedelta(days=sign)
            if self.is_work(d):
                k += 1
        return d

    def first_on_or_after(self, d: dt.date) -> dt.date:
        while not self.is_work(d):
            d += dt.timedelta(days=1)
        return d


def resolve(rule: str, launch: dt.date, cal: Calendar) -> dt.date:
    d = launch
    for op, n in schedule_rules.parse(rule):
        if op == "b":
            d = cal.step(d, n, -1)
        elif op == "a":
            d = cal.step(d, n, +1)
        elif op == "m":
            d = cal.first_on_or_after(_edate(d, n))
        elif op == "d":
            d = cal.first_on_or_after(d + dt.timedelta(days=n))
    return d


def _rules_of(p: dict, tasks: list[dict]) -> tuple[str | None, bool]:
    """ルールを引く開発タイプ。空なら（移行分）タスク名から推す。(flow, 推したか)"""
    if p["flow_type"]:
        return p["flow_type"], False
    return schedule_rules.guess_flow([t["title"] for t in tasks]), True


def plan(pid: str, launch: str | None = None, doc: dict | None = None) -> dict:
    """計算し直した結果（**書かない**）。launch を渡すとその日で（発売予定日を変える前の見込みにも使える）。"""
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    launch = launch or p["launch_date"]
    if not launch:
        return {"ok": False, "why": "発売予定日が入っていないので、日程は計算しません", "rows": []}
    ld = dt.date.fromisoformat(launch)
    tasks = store.rows(store.q("SELECT * FROM task WHERE project_id=? ORDER BY seq, id", (pid,)))
    flow, guessed = _rules_of(p, [t for t in tasks if t["kind"] != "予備"])
    try:
        cal = Calendar(ld - dt.timedelta(days=150), ld + dt.timedelta(days=240), doc)
        cal_err = None
    except calfc.NotConnected as e:
        cal, cal_err = None, str(e)
    rows = []
    for t in tasks:
        r = {"id": t["id"], "seq": t["seq"], "title": t["title"], "status": t["status"],
             "old_start": t["start_on"], "old_due": t["due_on"], "new_start": None, "new_due": None,
             "rule_start": t["rule_start"], "rule_due": t["rule_due"], "rule_src": t["rule_src"],
             "rule_label": None, "change": False, "why": None}
        rows.append(r)
        if t["status"] in SKIP:
            r["why"] = f"{t['status']}のタスクは動かしません"
            continue
        if t["kind"] == "予備":
            r["why"] = "予備時間の行（日付を持たない）"
            continue
        if t["date_locked"]:
            r["why"] = "日付を固定しています"
            continue
        if not (t["rule_start"] and t["rule_due"]):
            found = schedule_rules.rule_for(flow, t["title"])
            if not found:
                r["why"] = ("計算ルールがありません（テンプレートに無いタスク。日付は手で決めます）" if flow
                            else "開発タイプが空で、どのテンプレートか決められません（頭の欄で開発タイプを入れると計算できます）")
                continue
            r["rule_start"], r["rule_due"], r["rule_src"] = found
        r["rule_label"] = (schedule_rules.label(r["rule_start"])
                           + ("" if r["rule_due"] == r["rule_start"] else " 〜 " + schedule_rules.label(r["rule_due"])))
        if cal is None:
            r["why"] = cal_err
            continue
        try:
            ns, nd = resolve(r["rule_start"], ld, cal).isoformat(), resolve(r["rule_due"], ld, cal).isoformat()
        except calfc.NotConnected as e:
            r["why"] = str(e)
            continue
        r["new_start"], r["new_due"] = ns, nd
        r["change"] = (ns, nd) != (t["start_on"], t["due_on"])
    n_change = sum(1 for r in rows if r["change"])
    return {"ok": True, "launch": launch, "flow": flow, "flow_guessed": guessed, "rows": rows,
            "n_change": n_change, "n_skip": sum(1 for r in rows if r["why"]),
            "proposal": any(r["rule_src"] == "proposal" and r["new_start"] for r in rows)}


def apply(pid: str, user_id: str, launch_from: str | None = None, only: list[int] | None = None,
          doc: dict | None = None) -> dict:
    """計算し直した日程を書く。ルールを決めたタスクにはルールも書く（以後この案件はそのルール）。控えを残す。"""
    pl = plan(pid, doc=doc)
    if not pl["ok"]:
        raise ValueError(pl["why"])
    chosen = [r for r in pl["rows"] if r["change"] and (only is None or r["id"] in only)]
    now = store.now_s()
    undo = []
    with store.tx() as c:
        for r in pl["rows"]:
            if r["new_start"] and r["rule_start"]:
                c.execute("UPDATE task SET rule_start=?, rule_due=?, rule_src=? WHERE id=? AND rule_start IS NULL",
                          (r["rule_start"], r["rule_due"], r["rule_src"], r["id"]))
        for r in chosen:
            c.execute("UPDATE task SET start_on=?, due_on=? WHERE id=? AND status NOT IN ('完了','対象外') AND date_locked=0",
                      (r["new_start"], r["new_due"], r["id"]))
            undo.append({"id": r["id"], "start_on": r["old_start"], "due_on": r["old_due"],
                         "set_start": r["new_start"], "set_due": r["new_due"]})
        uid = None
        if undo:
            uid = c.execute("INSERT INTO schedule_undo (project_id,launch_from,launch_to,rows,applied_by,applied_at) "
                            "VALUES (?,?,?,?,?,?)", (pid, launch_from, pl["launch"], json.dumps(undo), user_id, now)).lastrowid
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                      (pid, now, user_id, "タスクの日程を計算し直した",
                       f"発売予定日 {pl['launch']} に合わせて {len(undo)} 件の開始日・期限を変えた（完了・対象外・固定は動かしていない）"))
    return {"ok": True, "changed": len(undo), "undo_id": uid}


def last_undo(pid: str) -> dict | None:
    r = store.one("SELECT * FROM schedule_undo WHERE project_id=? AND undone_at IS NULL ORDER BY id DESC LIMIT 1", (pid,))
    if r is None:
        return None
    return {"id": r["id"], "applied_at": r["applied_at"], "applied_by": r["applied_by"], "n": len(json.loads(r["rows"])),
            "launch_from": r["launch_from"], "launch_to": r["launch_to"]}


def undo(pid: str, user_id: str) -> dict:
    """直前の反映を元に戻す。**反映のあと人が直した行は戻さない**（その人の変更を消さない）。"""
    r = store.one("SELECT * FROM schedule_undo WHERE project_id=? AND undone_at IS NULL ORDER BY id DESC LIMIT 1", (pid,))
    if r is None:
        raise ValueError("元に戻せる反映がありません")
    back, kept = 0, 0
    now = store.now_s()
    with store.tx() as c:
        for x in json.loads(r["rows"]):
            cur = c.execute("SELECT start_on, due_on FROM task WHERE id=?", (x["id"],)).fetchone()
            if cur is None or (cur["start_on"], cur["due_on"]) != (x["set_start"], x["set_due"]):
                kept += 1
                continue
            c.execute("UPDATE task SET start_on=?, due_on=? WHERE id=?", (x["start_on"], x["due_on"], x["id"]))
            back += 1
        c.execute("UPDATE schedule_undo SET undone_at=? WHERE id=?", (now, r["id"]))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                  (pid, now, user_id, "タスクの日程の反映を元に戻した",
                   f"{back} 件を元の日付に戻した" + (f"（反映のあと直された {kept} 件はそのまま）" if kept else "")))
    return {"ok": True, "restored": back, "kept": kept}


def set_lock(task_id: int, locked: bool, user_id: str) -> dict:
    t = store.one("SELECT project_id, title, date_locked FROM task WHERE id=?", (task_id,))
    if t is None:
        raise LookupError("そのタスクがありません")
    if bool(t["date_locked"]) == locked:
        return {"ok": True, "changed": False}
    with store.tx() as c:
        c.execute("UPDATE task SET date_locked=? WHERE id=?", (1 if locked else 0, task_id))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                  (t["project_id"], store.now_s(), user_id, "日付の固定", f"{t['title']}: {'固定した' if locked else '固定を外した'}"))
    return {"ok": True, "changed": True, "locked": locked}
