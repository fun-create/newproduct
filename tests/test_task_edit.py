#!/usr/bin/env python3
"""
タスクを足す・直す（ADR-107）の検査。

見ているのは:
  - 足す: いちばん後ろ（予備時間の 998/999 より前）か、指定した行の次（後ろの番号をずらす）
  - 直す: 送られた欄だけ直し、履歴に前後の値を残す。開始が期限より後なら断る。日付・数字の形を検査する
  - 案件外の仕事も直せる
  - **直した値は同期（ADR-105）で上書きされない**（衝突になる）
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class TaskEdit(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, task
        seed.run()
        self.store, self.m = store, task
        self.pid = project.create("u", expand=False, internal_name="直す検査", stage="開発中")["id"]
        with store.tx() as c:
            for seq, title in ((1, "競合調査"), (2, "試作"), (3, "販売開始"), (998, "予備時間")):
                c.execute("INSERT INTO task (project_id,seq,title,role,status,created_at) VALUES (?,?,?,?,?,?)",
                          (self.pid, seq, title, "admin", "未着手", store.now_s()))
            c.execute("INSERT INTO work_item (kind,title,role,status,created_at) VALUES ('案件外','FBA','admin','未着手',?)", (store.now_s(),))

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def seqs(self):
        return [(r["seq"], r["title"]) for r in self.store.q("SELECT seq, title FROM task WHERE project_id=? ORDER BY seq", (self.pid,))]

    def test_add_at_end_and_after(self):
        r = self.m.add_task(self.pid, {"title": "出品", "role": "webmkt", "due_on": "2026-11-20", "hours": "0.5"}, "u")
        self.assertEqual(r["seq"], 4, "予備時間（998）より前のいちばん後ろ")
        self.m.add_task(self.pid, {"title": "サンプル発注", "after_seq": "1"}, "u")
        self.assertEqual(self.seqs(), [(1, "競合調査"), (2, "サンプル発注"), (3, "試作"), (4, "販売開始"), (5, "出品"), (998, "予備時間")])
        with self.assertRaises(ValueError):
            self.m.add_task(self.pid, {"title": " "}, "u")
        with self.assertRaises(ValueError):
            self.m.add_task(self.pid, {"title": "x", "role": "nope"}, "u")
        rev = self.store.val("SELECT what FROM project_revision WHERE project_id=? ORDER BY id DESC LIMIT 1", (self.pid,))
        self.assertIn("タスクを足した", rev)

    def test_edit_only_sent_fields_and_validate(self):
        tid = self.store.val("SELECT id FROM task WHERE project_id=? AND seq=2", (self.pid,))
        r = self.m.update_task("task", tid, {"due_on": "2026-11-05", "hours": "2"}, "u")
        self.assertEqual(sorted(r["changed"]), ["due_on", "hours"])
        t = dict(self.store.one("SELECT * FROM task WHERE id=?", (tid,)))
        self.assertEqual((t["title"], t["due_on"], t["hours"], t["role"]), ("試作", "2026-11-05", 2.0, "admin"))
        self.assertEqual(self.m.update_task("task", tid, {"due_on": "2026-11-05"}, "u")["changed"], [], "同じ値なら何もしない")
        with self.assertRaises(ValueError):
            self.m.update_task("task", tid, {"start_on": "2026-11-10"}, "u")       # 期限 11/5 より後
        with self.assertRaises(ValueError):
            self.m.update_task("task", tid, {"due_on": "11月5日"}, "u")
        with self.assertRaises(ValueError):
            self.m.update_task("task", tid, {"hours": "-1"}, "u")
        rev = self.store.val("SELECT what FROM project_revision WHERE project_id=? ORDER BY id DESC LIMIT 1", (self.pid,))
        self.assertIn("due_on 空→2026-11-05", rev)
        wid = self.store.val("SELECT id FROM work_item")
        self.assertEqual(self.m.update_task("work", wid, {"title": "FBA納品"}, "u")["changed"], ["title"])

    def test_app_edit_survives_sheet_sync(self):
        import tools.sync_sheet as S
        self.store.ex("UPDATE project SET source_sheet=?, source_key='直す検査' WHERE id=?", (S.SHEET, self.pid))
        self.store.conn().commit()
        rows = [["", "2026年度（5～4月）"], ["", "11月発売"], ["", "直す検査"],
                ["直す検査", "競合調査", "管理者", "未着手", "", "46330"]]
        S.apply(S.parse_ledger(rows), [], {}, dt.date(2026, 10, 18), "2026-10-18", "sync")
        tid = self.store.val("SELECT id FROM task WHERE project_id=? AND seq=1", (self.pid,))
        self.assertEqual(self.store.val("SELECT due_on FROM task WHERE id=?", (tid,)), "2026-11-04")
        self.m.update_task("task", tid, {"due_on": "2026-11-12"}, "u")            # アプリで直した
        rows[3][5] = "46335"                                                     # シートでも別の日に動いた
        n = S.apply(S.parse_ledger(rows), [], {}, dt.date(2026, 10, 18), "2026-10-18", "sync")
        self.assertEqual(n["conflicts"], 1)
        self.assertEqual(self.store.val("SELECT due_on FROM task WHERE id=?", (tid,)), "2026-11-12", "アプリで直した値が残る")


if __name__ == "__main__":
    unittest.main()
