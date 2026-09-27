#!/usr/bin/env python3
"""
利用者を足す道具（`tools/add_user.py`）の検査。**外部通信をしない・本番の台帳を触らない。**

2026-09-27、`/opt/accounts/roles/newproduct.json` を直しても誰も入れない
（`auth.py` は `config/users.json` を読む）ことが Calendar セッションの指摘で判明した。
道具は **`auth.create()` を通す**（錠・世代・0600）。手で JSON を書かない。

  - 共通台帳に居ない人は足せない（足しても合言葉の照合ができない）
  - 足した人の `password` は**誰も知らないダイジェスト**で、平文はどこにも残らない
  - 役割の変更・停止・再開が `auth.update()` を通る
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


@unittest.skipUnless(hasattr(hashlib, "scrypt"),
                     "hashlib.scrypt が無い環境（手元の Mac）。本番の Linux では動く")
class TestAddUser(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="np-adduser-"))
        (self.tmp / "config").mkdir()
        (self.tmp / "accounts").mkdir()
        (self.tmp / "accounts" / "users.json").write_text(json.dumps({"users": [
            {"id": "yoko", "name": "増地 洋子", "active": True, "password": "x"},
            {"id": "masateru", "name": "十文字 眞輝", "active": True, "password": "x"}]}),
            encoding="utf-8")
        os.environ["ACCOUNTS_DIR"] = str(self.tmp / "accounts")
        os.environ["AUTH_APP"] = "newproduct"
        for m in [k for k in sys.modules if k in ("auth", "add_user")]:
            del sys.modules[m]
        import auth
        auth.USERS_PATH = self.tmp / "config" / "users.json"
        auth.ACCOUNTS_DIR = os.environ["ACCOUNTS_DIR"]
        self.auth = auth
        spec = importlib.util.spec_from_file_location("add_user", BASE / "tools" / "add_user.py")
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.tool.auth = auth
        self.tool.SHARED = self.tmp / "accounts" / "users.json"

    def tearDown(self):
        os.environ.pop("ACCOUNTS_DIR", None)
        os.environ.pop("AUTH_APP", None)

    def run_tool(self, *argv):
        sys.argv = ["add_user.py", *argv]
        return self.tool.main()

    def test_adds_a_person_from_the_shared_ledger_with_an_unknown_password(self):
        self.assertEqual(self.run_tool("--who", "masateru", "--role", "admin"), 0)
        self.assertEqual(self.run_tool("--who", "yoko", "--role", "user"), 0)
        u = {x["user_id"]: x for x in self.auth.users()}
        self.assertEqual(u["yoko"]["role"], "user")
        self.assertEqual(u["yoko"]["name"], "増地 洋子", "表示名は共通台帳から")
        raw = json.loads(self.auth.USERS_PATH.read_text(encoding="utf-8"))
        rec = next(r for r in raw["users"] if r["user_id"] == "yoko")
        self.assertTrue(rec["password"], "ダイジェストは入る")
        self.assertNotIn("Aa1!", rec["password"], "平文が残らない")
        self.assertEqual(oct(self.auth.USERS_PATH.stat().st_mode)[-3:], "600")

    def test_refuses_someone_not_in_the_shared_ledger(self):
        self.assertEqual(self.run_tool("--who", "nobody", "--role", "user"), 1)
        self.assertEqual([u["user_id"] for u in self.auth.users()], [])

    def test_role_change_and_stop_go_through_auth_update(self):
        self.run_tool("--who", "masateru", "--role", "admin")
        self.run_tool("--who", "yoko", "--role", "user")
        self.assertEqual(self.run_tool("--who", "yoko", "--role", "admin"), 0)
        self.assertEqual(self.auth.get("yoko")["role"], "admin")
        self.assertEqual(self.run_tool("--who", "yoko", "--off"), 0)
        self.assertFalse(self.auth.get("yoko")["active"], "停止は消さない")
        self.assertEqual(self.run_tool("--who", "yoko", "--on"), 0)
        self.assertTrue(self.auth.get("yoko")["active"])

    def test_adding_twice_does_not_duplicate(self):
        self.run_tool("--who", "masateru", "--role", "admin")
        self.run_tool("--who", "yoko", "--role", "user")
        self.run_tool("--who", "yoko", "--role", "user")
        self.assertEqual(sum(1 for u in self.auth.users() if u["user_id"] == "yoko"), 1)


if __name__ == "__main__":
    unittest.main()
