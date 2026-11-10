#!/usr/bin/env python3
"""担当区分ごとの既定の担当者（2026-11-10 追加修正依頼書 §2-2・ADR-136）。
作るときに空なら入れる・人が選んだ担当は上書きしない・他部署パートさんは空・一度だけの穴埋めは空のものだけ。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

USERS = [{"user_id": u} for u in ("yoko", "chieko", "satomi", "risa", "kazuya", "mayumi", "masateru")]


class Assign(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, assign
        seed.run()
        self.store, self.m = store, assign
        import auth
        self.p_users = mock.patch.object(auth, "users", return_value=USERS)
        self.p_users.start()

    def tearDown(self):
        self.p_users.stop()
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_table_from_the_request(self):
        m = self.m.mapping()
        self.assertEqual(m, {"member": "yoko", "admin": "chieko", "devdept": "chieko", "webmkt": "satomi",
                             "prod": "risa", "se": "kazuya", "cs": "mayumi"})
        self.assertIsNone(self.m.for_role("part"))                    # 他部署パートさんは空
        self.assertIsNone(self.m.for_role("president"))               # 表に無い区分は推測しない
        self.assertEqual(self.m.for_role("admin", "masateru"), "masateru")   # 人が選んだ担当はそのまま

    def test_new_tasks_get_the_default(self):
        from app import project, task, routine
        pid = project.create("u", internal_name="x", flow_type="meire")["id"]
        rows = self.store.q("SELECT role, assignee, kind FROM task WHERE project_id=?", (pid,))
        self.assertTrue(rows)
        for r in rows:
            if r["kind"] == "予備":
                continue
            self.assertEqual(r["assignee"], self.m.for_role(r["role"]), r["role"])
        t = task.add_task(pid, {"title": "足した", "role": "prod"}, "u")
        self.assertEqual(self.store.val("SELECT assignee FROM task WHERE id=?", (t["id"],)), "risa")
        t = task.add_task(pid, {"title": "人を選んだ", "role": "prod", "assignee": "masateru"}, "u")
        self.assertEqual(self.store.val("SELECT assignee FROM task WHERE id=?", (t["id"],)), "masateru")
        w = task.create_work_item({"title": "案件外", "role": "webmkt"}, "u")
        self.assertEqual(self.store.val("SELECT assignee FROM work_item WHERE id=?", (w["id"],)), "satomi")
        w = task.create_work_item({"title": "パート", "role": "part"}, "u")
        self.assertIsNone(self.store.val("SELECT assignee FROM work_item WHERE id=?", (w["id"],)))

    def test_fill_only_empty_open_tasks_and_keeps_manual(self):
        from app import project, task
        pid = project.create("u", expand=False, internal_name="x", flow_type="meire")["id"]
        ex = self.store.ex
        a = ex("INSERT INTO task (project_id,seq,title,role,status,created_at) VALUES (?,1,'a','admin','未着手','x')", (pid,)).lastrowid
        b = ex("INSERT INTO task (project_id,seq,title,role,assignee,status,created_at) VALUES (?,2,'b','admin','yoko','未着手','x')", (pid,)).lastrowid
        c = ex("INSERT INTO task (project_id,seq,title,role,status,created_at) VALUES (?,3,'c','admin','完了','x')", (pid,)).lastrowid
        d = ex("INSERT INTO task (project_id,seq,title,role,status,created_at) VALUES (?,4,'d','part','未着手','x')", (pid,)).lastrowid
        w = ex("INSERT INTO work_item (kind,title,role,status,created_at) VALUES ('案件外','w','cs','着手','x')").lastrowid
        self.store.conn().commit()
        dry = self.m.fill_unassigned("u")
        self.assertEqual((dry["task"], dry["work_item"]), (1, 1))
        self.assertIsNone(self.store.val("SELECT assignee FROM task WHERE id=?", (a,)))   # 数えるだけでは書かない
        self.m.fill_unassigned("u", apply=True)
        got = {i: self.store.val("SELECT assignee FROM task WHERE id=?", (i,)) for i in (a, b, c, d)}
        self.assertEqual(got, {a: "chieko", b: "yoko", c: None, d: None})    # 既にいる人・完了・パートは触らない
        self.assertEqual(self.store.val("SELECT assignee FROM work_item WHERE id=?", (w,)), "mayumi")
        task.set_assignee("task", a, "", "u")                                  # 人が外したら
        task.set_assignee("task", a, "masateru", "u")
        self.assertEqual(self.store.val("SELECT assignee FROM task WHERE id=?", (a,)), "masateru")

    def test_setting_refuses_part_unknown_role_and_user(self):
        from app import admin
        f = admin.EDITABLE["task.default_assignees"][1]
        self.assertEqual(f("admin=chieko、cs=mayumi", "既定"), "admin=chieko、cs=mayumi")
        for bad in ("part=yoko", "nosuch=yoko", "admin=nobody", "admin"):
            with self.assertRaises(ValueError):
                f(bad, "既定")


if __name__ == "__main__":
    unittest.main()
