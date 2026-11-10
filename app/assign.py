#!/usr/bin/env python3
"""
タスクの担当区分ごとの**既定の担当者**（石原さんの追加修正依頼書 2026-11-10 §2-2・ADR-136）。

- 担当区分（role）から個人の担当者を決める。**担当者が空のときだけ**入れる（人が選んだ担当は上書きしない）
- 入れるのは**タスクを作るとき**（テンプレートからの展開・1行足す・案件外の仕事・繰り返しの仕事・
  発売後の仕事・改善要望のタスク化）と、一度だけの穴埋め（`fill_unassigned`）。
  画面の再表示・同期・日程の計算し直しでは触らない → 人が変えた担当がそのまま残る
- 「他部署パートさん」（part）は空のまま（依頼書の表）。表に無い担当区分は推測で入れない
- これは**担当者の既定値だけ**。担当区分への所属・アプリ権限・ゲートの承認資格は変えない
- 期限は入れない（カレンダーへは今の条件＝担当者と期限がそろったものだけが出る）
"""
from __future__ import annotations

import re

from app import store

SETTING = "task.default_assignees"
# 依頼書 §2-2 の表（2026-11-10）。商品開発部管理者と商品開発部はどちらも石原さん
DEFAULT = "member=yoko、admin=chieko、devdept=chieko、webmkt=satomi、prod=risa、se=kazuya、cs=mayumi"
NEVER = ("part",)                       # 他部署パートさんは空欄のまま（自動の穴埋めの対象外）
OPEN = ("未着手", "着手", "保留")


def parse(v: str | None, *, roles: set | None = None) -> dict:
    """「担当区分=user_id、…」。壊れていたら ValueError（保存のときに止める）"""
    out = {}
    for part in re.split(r"[、,\n]", v or ""):
        part = part.strip()
        if not part:
            continue
        k, sep, u = part.partition("=")
        k, u = k.strip(), u.strip()
        if not sep or not k or not u:
            raise ValueError(f"「{part}」は 担当区分=利用者ID の形で入れてください")
        if k in NEVER:
            raise ValueError("他部署パートさん（part）は空欄のままにする決まりです（2026-11-10 依頼書 §2-2）")
        if roles is not None and k not in roles:
            raise ValueError(f"担当区分 {k!r} はありません")
        out[k] = u
    return out


def seed_settings() -> None:
    store.ex("INSERT INTO setting (key,value,label,kind,unit,why) VALUES (?,?,?,?,?,?) "
             "ON CONFLICT(key) DO UPDATE SET label=excluded.label,kind=excluded.kind,why=excluded.why",
             (SETTING, DEFAULT, "担当区分ごとの既定の担当者", "text", None,
              "2026-11-10 石原さんの依頼書 §2-2 の表。タスクを作るとき、担当者が空なら入れます"))


def mapping() -> dict:
    v = store.val("SELECT value FROM setting WHERE key=?", (SETTING,), None)
    try:
        return parse(v)
    except ValueError:
        return {}                       # 壊れた値では入れない（推測しない）


def for_role(role: str | None, assignee: str | None = None, m: dict | None = None) -> str | None:
    """担当者が入っていればそのまま。空なら担当区分の既定（無ければ空のまま）。"""
    a = (assignee or "").strip()
    if a:
        return a
    if not role or role in NEVER:
        return None
    return (m if m is not None else mapping()).get(role) or None


def fill_unassigned(user_id: str, apply: bool = False) -> dict:
    """**一度だけの穴埋め。**未完了で担当者が空のタスク・案件外の仕事に既定の担当者を入れる。
    中止の案件のタスクは入れない。既に担当がいるものは触らない。apply=False は数えるだけ。"""
    m = mapping()
    marks = ",".join("?" * len(OPEN))
    tasks = store.rows(store.q(
        f"SELECT t.id, t.role, t.due_on, t.project_id FROM task t JOIN project p ON p.id=t.project_id "
        f"WHERE t.status IN ({marks}) AND (t.assignee IS NULL OR t.assignee='') AND p.stage!='中止'", OPEN))
    works = store.rows(store.q(
        f"SELECT id, role, due_on FROM work_item WHERE status IN ({marks}) "
        f"AND (assignee IS NULL OR assignee='')", OPEN))
    plan = {"task": [], "work_item": []}
    skipped = {}
    for table, rows in (("task", tasks), ("work_item", works)):
        for r in rows:
            a = for_role(r["role"], None, m)
            if a:
                plan[table].append((r["id"], a, r["due_on"]))
            else:
                k = r["role"] or "担当区分なし"
                skipped[k] = skipped.get(k, 0) + 1
    if apply:
        with store.tx() as c:
            for table in plan:
                for i, a, _d in plan[table]:
                    # 条件を付け直す（数えた後に人が入れた担当は上書きしない）
                    c.execute(f"UPDATE {table} SET assignee=? WHERE id=? AND (assignee IS NULL OR assignee='')",
                              (a, i))
        store.audit(user_id, "assign.fill_default", "", {
            "task": len(plan["task"]), "work_item": len(plan["work_item"]), "skipped": skipped})
    by = {}
    for table in plan:
        for _i, a, _d in plan[table]:
            by[a] = by.get(a, 0) + 1
    return {"applied": apply, "task": len(plan["task"]), "work_item": len(plan["work_item"]),
            "with_due": sum(1 for t in plan for _i, _a, d in plan[t] if d),
            "by_user": by, "skipped": skipped,
            "ids": {t: [i for i, _a, _d in plan[t]] for t in plan}}
