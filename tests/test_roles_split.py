#!/usr/bin/env python3
"""
担当区分と承認できる人を分けた（2026-10-23 石原さんの依頼書 §3・ADR-123）ことの検査。

見ているのは:
  - 増地さん（担当区分＝商品開発部メンバー・承認＝管理者）の「自分のやること」に、管理者の区分のタスクが出ない
  - それでも G5（管理者）は承認できる
  - 担当区分の名前（商品開発部管理者・商品開発部メンバー・SE部・他部署パートさん）
  - 承認できる人に付けられるのは 社長・管理者・生産部 だけ。社長を誰も持たなくなる外し方は断る
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class RolesSplit(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-23"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, task, gate, admin
        seed.run()
        self.store, self.project, self.task, self.gate, self.admin = store, project, task, gate, admin
        import auth
        self._pu, self._u = auth.public_users, auth.users
        auth.public_users = lambda: [{"user_id": "chieko", "active": True}, {"user_id": "yoko", "active": True},
                                     {"user_id": "masateru", "active": True}]
        auth.users = auth.public_users
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id) VALUES ('admin','chieko'),('member','yoko'),('president','masateru')")
            c.execute("INSERT INTO approval_member (role_code,user_id) VALUES ('admin','chieko'),('admin','yoko')")
        pid = project.create("u", expand=False, internal_name="x")["id"]
        with store.tx() as c:
            c.execute("INSERT INTO task (project_id,seq,title,role,status,due_on) VALUES (?,1,'石原さんの仕事','admin','未着手','2026-10-24')", (pid,))
            c.execute("INSERT INTO task (project_id,seq,title,role,status,due_on) VALUES (?,2,'メンバーの仕事','member','未着手','2026-10-24')", (pid,))

    def tearDown(self):
        import auth
        auth.public_users, auth.users = self._pu, self._u
        self.store.close()
        os.environ.pop("NEWPRODUCT_TODAY", None)
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_my_work_uses_task_roles_only(self):
        mine = [t["title"] for t in self.task.dashboard("yoko")["my"]["tasks"]]
        self.assertEqual(mine, ["メンバーの仕事"], "承認の資格（管理者）で管理者のタスクは増えない")
        self.assertEqual([t["title"] for t in self.task.dashboard("chieko")["my"]["tasks"]], ["石原さんの仕事"])
        self.assertIn("admin", self.gate.roles_of("yoko"), "権限は担当区分と承認の和")
        self.assertEqual(self.gate.task_roles_of("yoko"), ["member"])
        self.assertTrue(self.gate.can_approve("yoko", "G5"), "増地さんは G5 を承認できる")

    def test_labels(self):
        lab = {r["code"]: r["label"] for r in self.store.q("SELECT code,label FROM role")}
        self.assertEqual((lab["admin"], lab["member"], lab["se"], lab["part"]),
                         ("商品開発部管理者", "商品開発部メンバー", "SE部", "他部署パートさん"))

    def test_set_approval(self):
        with self.assertRaises(ValueError):
            self.admin.set_role("yoko", "member", True, "masateru", "admin", kind="approval")
        self.admin.set_role("yoko", "admin", False, "masateru", "admin", kind="approval")
        self.assertFalse(self.gate.can_approve("yoko", "G5"))
        with self.assertRaises(ValueError):
            self.admin.set_role("masateru", "president", False, "masateru", "admin", kind="task")   # 社長が居なくなる
        self.admin.set_role("masateru", "president", True, "masateru", "admin", kind="approval")
        self.admin.set_role("masateru", "president", False, "masateru", "admin", kind="task")      # 承認側に残るので外せる
        self.assertIn("president", self.gate.roles_of("masateru"))


if __name__ == "__main__":
    unittest.main()
