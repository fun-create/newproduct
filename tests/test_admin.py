#!/usr/bin/env python3
"""
設定ページの「全員に効くもの」（ADR-059）の検査。

  - 変えられる人: 中身は業務ロール、接続先はアプリ権限 admin、発売目標本数は社長だけ
  - **根拠が必須**。空欄は未設定（NULL）に戻す。0 にしない
  - 記録に前の値・新しい値・根拠が残り、変更の記録に出る
  - 正本が他にあるもの（長期連休の月など）は変えられない
  - 業務ロールの付け外しは admin だけ。社長を0人にできない
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Admin(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, admin
        seed.run()
        self.m, self.store = admin, store
        with store.tx() as c:
            for role, uid in (("president", "boss"), ("devdept", "dev"), ("admin", "kanri")):
                c.execute("INSERT INTO role_member (role_code,user_id) VALUES (?,?)", (role, uid))

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def val(self, k):
        return self.store.val("SELECT value FROM setting WHERE key=?", (k,))

    def test_who_can_edit(self):
        with self.assertRaises(PermissionError):
            self.m.set_value("monthly_launch_target", "3", "年間36本", "kanri", "admin")   # 社長だけ
        self.m.set_value("monthly_launch_target", "3", "年間36本", "boss", "user")
        self.assertEqual(self.val("monthly_launch_target"), "3")
        with self.assertRaises(PermissionError):
            self.m.set_value("automation.chatwork_room_id", "123456", "部屋", "boss", "user")   # 接続先は admin
        self.m.set_value("automation.chatwork_room_id", "123456", "部屋", "nobody", "admin")
        with self.assertRaises(PermissionError):
            self.m.set_value("plan.holiday_months", "1,5,8", "推測", "boss", "admin")      # 正本は Calendar
        with self.assertRaises(PermissionError):
            self.m.set_value("cost_tax_rate", "8", "軽減", "dev", "user")                  # 商品開発部は不可

    def test_reason_required_blank_is_unset_and_logged(self):
        with self.assertRaises(ValueError):
            self.m.set_value("cost_tax_rate", "8", " ", "kanri", "user")
        self.m.set_value("cost_tax_rate", "8", "軽減税率の商品", "kanri", "user")
        self.m.set_value("cost_tax_rate", "", "いったん外す", "kanri", "user")
        self.assertIsNone(self.val("cost_tax_rate"), "空欄は未設定（0 にしない）")
        log = self.m.change_log()
        self.assertEqual([(x["before"], x["after"], x["reason"]) for x in log[:2]],
                         [("8", None, "いったん外す"), ("10", "8", "軽減税率の商品")])
        with self.assertRaises(ValueError):
            self.m.set_value("plan.ratio_original_to_uchiwa", "三対一", "x", "kanri", "user")
        self.m.set_value("plan.ratio_original_to_uchiwa", "4：1", "x", "kanri", "user")
        self.assertEqual(self.val("plan.ratio_original_to_uchiwa"), "4:1")

    def test_connection_values_hidden_in_log_for_non_admin(self):
        self.m.set_value("automation.chatwork_room_id", "123456", "部屋を作った", "x", "admin")
        mine = self.m.change_log(app_role="admin")[0]
        self.assertEqual((mine["before"], mine["after"], mine["reason"]), (None, "123456", "部屋を作った"))
        other = self.m.change_log(app_role="user")[0]
        self.assertNotIn("123456", str(other), "admin 以外には接続先の値を出さない")
        self.assertIsNone(other["reason"])
        self.assertEqual(other["label"], "自動化依頼を渡す ChatWork の部屋ID", "変えたことは見える")

    def test_roles_admin_only_and_keep_a_president(self):
        import auth
        orig = auth.public_users
        auth.public_users = lambda: [{"user_id": u, "active": True} for u in ("boss", "dev", "kanri", "x")]
        self.addCleanup(setattr, auth, "public_users", orig)
        with self.assertRaises(PermissionError):
            self.m.set_role("x", "devdept", True, "boss", "user")
        self.m.set_role("x", "devdept", True, "kanri", "admin")
        self.assertIn("devdept", self.m.roles_view("admin")["members"]["x"])
        with self.assertRaises(ValueError):
            self.m.set_role("boss", "president", False, "kanri", "admin")
        self.m.set_role("x", "president", True, "kanri", "admin")
        self.m.set_role("boss", "president", False, "kanri", "admin")
        self.assertEqual(self.m.change_log()[0]["action"], "role.revoke")


if __name__ == "__main__":
    unittest.main()
