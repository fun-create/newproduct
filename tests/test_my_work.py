#!/usr/bin/env python3
"""
ダッシュボードの「自分のやること」（2026-10-06）。

タスクは人ではなく業務ロールに付いている。「自分の」＝ 担当者が自分 ＋ 自分の業務ロール。
期限切れ（未完）と7日先まで。完了・対象外・8日以上先・他のロールは出さない。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class MyWork(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-06"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, task
        seed.run()
        self.store, self.task = store, task
        self.pid = project.create("u", expand=False, internal_name="自分の検査", flow_type="meire",
                                  launch_date="2026-12-01")["id"]
        rows = [  # seq, title, role, assignee, due, status
            (1, "開発・期限切れ", "devdept", None, "2026-10-01", "未着手"),
            (2, "開発・7日後", "devdept", None, "2026-10-13", "着手"),
            (3, "開発・8日後", "devdept", None, "2026-10-14", "未着手"),
            (4, "開発・完了", "devdept", None, "2026-10-02", "完了"),
            (5, "生産・明日", "prod", None, "2026-10-07", "未着手"),
            (6, "生産だが自分が担当", "prod", "dev", "2026-10-08", "未着手"),
        ]
        with store.tx() as c:
            for seq, title, role, who, due, st in rows:
                c.execute("INSERT INTO task (project_id,seq,title,role,assignee,due_on,status,created_at) "
                          "VALUES (?,?,?,?,?,?,?,?)", (self.pid, seq, title, role, who, due, st, "2026-10-01"))
            c.execute("INSERT OR IGNORE INTO role_member (role_code,user_id) VALUES ('devdept','dev')")

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_my_roles_and_assigned_within_7_days(self):
        my = self.task.dashboard("dev")["my"]
        self.assertEqual([t["title"] for t in my["tasks"]], ["開発・期限切れ", "生産だが自分が担当", "開発・7日後"],
                         "期限の順。8日後・完了・他のロールは出さない")
        self.assertEqual(my["tasks_n"], 3)
        self.assertTrue(my["tasks"][0]["overdue"])
        self.assertEqual([t["by"] for t in my["tasks"]], ["ロール", "担当", "ロール"])
        self.assertEqual(my["roles"], ["商品開発部"])

    def test_task_list_mine_next7_matches_dashboard(self):
        """「ほか N 件」の先（タスク一覧・7日先まで・自分の分だけ）が、ダッシュボードと同じ範囲を出す。"""
        dash = {t["title"] for t in self.task.dashboard("dev")["my"]["tasks"]}
        lst = self.task.listing("next7", "project", mine="dev")
        self.assertEqual({r["title"] for r in lst["rows"]}, dash)
        self.assertTrue(lst["mine"])
        everyone = {r["title"] for r in self.task.listing("next7", "project")["rows"]}
        self.assertIn("生産・明日", everyone, "全員の分には他のロールも出る")

    def test_no_roles_sees_only_assigned(self):
        my = self.task.dashboard("nobody")["my"]
        self.assertEqual(my["tasks"], [])
        self.assertEqual(my["roles"], [])


if __name__ == "__main__":
    unittest.main()
