#!/usr/bin/env python3
"""年間目標（FR-109/110）と発売後タスク（FR-104/105）。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Target(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, target
        seed.run()
        self.m = target
        self.pid = project.create("u", expand=False, internal_name="検査", flow_type="meire",
                                  launch_date="2026-08-01")["id"]

    def tearDown(self):
        from app import store
        store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def ok(self, **f):
        d = {"method": "類似商品法", "annual_yen": "365000", "basis": "似ている商品 A の発売1年の実績から"}
        d.update(f)
        return self.m.save(self.pid, d, "u")

    def test_unset_is_not_zero_and_blocks_gates(self):
        from app import gate
        self.assertEqual(self.m.progress(self.pid, "2026-08-01", 1000)["state"], "目標未設定")
        self.assertIn("goal", {m["key"] for m in gate.missing(self.pid, "G3")})
        self.ok()
        self.assertNotIn("goal", {m["key"] for m in gate.missing(self.pid, "G3")})
        self.assertNotIn("goal_entered", {m["key"] for m in gate.missing(self.pid, "G5")})

    def test_method_and_basis_required_and_no_zero(self):
        for bad in ({"method": "勘"}, {"basis": "なんとなく"}, {"annual_yen": ""}, {"annual_yen": "0"},
                    {"annual_yen": "abc"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.ok(**bad)
        self.assertIsNone(self.m.get(self.pid))

    def test_progress_and_pace(self):
        self.ok()
        p = self.m.progress(self.pid, "2026-08-01", 61000.0, today=dt.date(2026, 10, 1))
        self.assertEqual(p["days"], 61)
        self.assertEqual(p["rate"], 16.7)
        self.assertEqual(p["pace_rate"], 100.0)            # 365000×61/365 = 61000
        self.assertIsNone(self.m.progress(self.pid, "2026-08-01", None)["rate"])

    def test_g5_pass_creates_post_launch_tasks_once(self):
        from app import gate, store
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('admin','k',?)",
                      (store.now_s(),))
        gate._post_launch_tasks(dict(store.one("SELECT * FROM project WHERE id=?", (self.pid,))), "k")
        gate._post_launch_tasks(dict(store.one("SELECT * FROM project WHERE id=?", (self.pid,))), "k")
        rows = store.rows(store.q("SELECT title,role,due_on FROM task WHERE project_id=? ORDER BY seq",
                                  (self.pid,)))
        self.assertEqual([(r["title"], r["role"], r["due_on"]) for r in rows],
                         [("売上集計（発売+2週）", "devdept", "2026-08-15"),
                          ("生産問題点確認（発売+2か月）", "prod", "2026-09-30")])


if __name__ == "__main__":
    unittest.main()
