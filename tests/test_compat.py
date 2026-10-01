#!/usr/bin/env python3
"""対応確認（FR-102）。**項目の中身は入れない**・写した記録はひな形を直しても変わらない・不可には理由。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Compat(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, compat
        seed.run()
        self.m = compat
        store.ex("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('devdept','dev',?)",
                 (store.now_s(),))
        store.conn().commit()
        self.pid = project.create("u", expand=False, internal_name="検査", flow_type="meire",
                                  launch_date="2026-12-01")["id"]

    def tearDown(self):
        from app import store
        store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_no_items_are_invented(self):
        self.assertEqual(self.m.templates(), [], "種データに項目を入れない（商品開発部が決める）")

    def test_only_devdept_admin_can_edit_templates(self):
        with self.assertRaises(PermissionError):
            self.m.save_template({"name": "スマホケース", "items": "iPhone"}, "u")
        r = self.m.save_template({"name": "スマホケース", "items": "iPhone\nAndroid\n\niPhone"}, "dev")
        self.assertEqual(r["items"], ["iPhone", "Android"])

    def test_applied_items_survive_template_change_and_no_overwrite(self):
        self.m.save_template({"name": "スマホケース", "items": "iPhone\nAndroid"}, "dev")
        tid = self.m.templates()[0]["id"]
        self.assertEqual(self.m.apply(self.pid, tid, "u")["added"], 2)
        rid = self.m.overview(self.pid, "u")["rows"][0]["id"]
        self.m.set_result(self.pid, rid, "確認済", "", "u")
        self.m.save_template({"name": "スマホケース", "items": "Pixel"}, "dev")
        rows = self.m.overview(self.pid, "u")["rows"]
        self.assertEqual([r["item"] for r in rows], ["iPhone", "Android"])
        self.assertEqual(self.m.apply(self.pid, tid, "u")["added"], 1)   # Pixel だけ増える
        self.assertEqual(self.m.overview(self.pid, "u")["rows"][0]["result"], "確認済", "上書きしない")

    def test_ng_needs_reason(self):
        self.m.save_template({"name": "PC", "items": "Windows"}, "dev")
        self.m.apply(self.pid, self.m.templates()[0]["id"], "u")
        rid = self.m.overview(self.pid, "u")["rows"][0]["id"]
        with self.assertRaises(ValueError):
            self.m.set_result(self.pid, rid, "不可", "", "u")
        self.m.set_result(self.pid, rid, "不可", "Windows 11 で表示が崩れる", "u")
        self.assertEqual(self.m.overview(self.pid, "u")["summary"]["不可"], 1)


if __name__ == "__main__":
    unittest.main()
