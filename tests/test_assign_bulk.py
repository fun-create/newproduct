#!/usr/bin/env python3
"""
担当のまとめ割り振り（ADR-106）の検査。

見ているのは:
  - 案件 × ロールで、担当のいない未完了タスクを数える（中止・保留の案件は外す）
  - まとめて入れるのは担当が空の行だけ。入っている担当・完了・対象外は変えない
  - 割り振れるのは業務ロール（商品開発部・管理者・社長）の人だけ。利用者台帳に無い人は入れない
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class AssignBulk(unittest.TestCase):
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
        import auth
        self._users = auth.users
        auth.users = lambda: [{"user_id": "chieko"}, {"user_id": "mayumi"}]
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('devdept','chieko',?)", (store.now_s(),))
        self.pid = project.create("u", expand=False, internal_name="割り振り検査", stage="開発中")["id"]
        held = project.create("u", expand=False, internal_name="保留中", stage="保留")["id"]
        with store.tx() as c:
            for seq, role, st, who in ((1, "admin", "未着手", None), (2, "admin", "着手", None), (3, "admin", "完了", None),
                                      (4, "admin", "未着手", "mayumi"), (5, "member", "未着手", None), (6, "admin", "対象外", None)):
                c.execute("INSERT INTO task (project_id,seq,title,role,status,assignee,created_at) VALUES (?,?,?,?,?,?,?)",
                          (self.pid, seq, f"t{seq}", role, st, who, store.now_s()))
            c.execute("INSERT INTO task (project_id,seq,title,role,status,created_at) VALUES (?,?,?,?,?,?)",
                      (held, 1, "保留の案件", "admin", "未着手", store.now_s()))
            c.execute("INSERT INTO work_item (kind,title,role,status,created_at) VALUES ('案件外','FBA','admin','未着手',?)", (store.now_s(),))

    def tearDown(self):
        import auth
        auth.users = self._users
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_summary_counts_open_unassigned_only(self):
        s = self.m.unassigned_summary()
        got = {(r["project_id"], r["role"]): r["n"] for r in s["rows"]}
        self.assertEqual(got, {(self.pid, "admin"): 2, (self.pid, "member"): 1}, "完了・対象外・担当あり・保留の案件は数えない")
        self.assertEqual([(w["role"], w["n"]) for w in s["work"]], [("admin", 1)])
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["members"]["devdept"], ["chieko"])

    def test_assign_fills_empty_only(self):
        r = self.m.assign_bulk("task", self.pid, "admin", "chieko", "chieko")
        self.assertEqual(r["assigned"], 2)
        rows = {t["seq"]: dict(t) for t in self.store.q("SELECT * FROM task WHERE project_id=?", (self.pid,))}
        self.assertEqual((rows[1]["assignee"], rows[2]["assignee"]), ("chieko", "chieko"))
        self.assertEqual(rows[4]["assignee"], "mayumi", "入っている担当は変えない")
        self.assertIsNone(rows[3]["assignee"], "完了は触らない")
        self.assertIsNone(rows[6]["assignee"], "対象外は触らない")
        self.assertIsNone(rows[5]["assignee"], "違うロールは触らない")
        self.assertEqual(self.m.assign_bulk("work", None, "admin", "mayumi", "chieko")["assigned"], 1)
        self.assertEqual(self.m.unassigned_summary()["total"], 1)

    def test_permission_and_unknown_user(self):
        with self.assertRaises(PermissionError):
            self.m.assign_bulk("task", self.pid, "admin", "chieko", "mayumi")
        with self.assertRaises(ValueError):
            self.m.assign_bulk("task", self.pid, "admin", "nobody", "chieko")
        with self.assertRaises(LookupError):
            self.m.assign_bulk("task", "nope", "admin", "chieko", "chieko")


if __name__ == "__main__":
    unittest.main()
