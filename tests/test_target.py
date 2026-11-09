#!/usr/bin/env python3
"""年間目標（FR-109/110）。"""
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
    # G5 通過の発売後の仕事は tests/test_followup.py（案件外に作る・ADR-128）


if __name__ == "__main__":
    unittest.main()
