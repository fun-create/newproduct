#!/usr/bin/env python3
"""月次レポート（FR-116）。**1つの節が作れなくても、ほかは作り、作れなかった節を理由つきで書く。**"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Report(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_SALESFEED"] = "/nonexistent.sqlite"
        os.environ["NEWPRODUCT_FCTR"] = "/nonexistent.json"
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-02"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, report
        seed.run()
        self.m, self.store = report, store

    def tearDown(self):
        self.store.close()
        for k in ("NEWPRODUCT_SALESFEED", "NEWPRODUCT_FCTR", "NEWPRODUCT_TODAY"):
            os.environ.pop(k, None)
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_prev_month(self):
        self.assertEqual(self.m.prev_month(), "2026-09")
        import datetime as dt
        self.assertEqual(self.m.prev_month(dt.date(2027, 1, 2)), "2026-12")

    def test_partial_failure_is_written_with_reason(self):
        r = self.m.save("2026-09", "timer")
        self.assertTrue(r["problems"], "売上フィードが無いので売上の節は作れない")
        body = self.m.get("2026-09")["body_md"]
        self.assertIn("## 4. 開発の進み", body)                 # ほかの節は作る
        self.assertIn("## 作れなかった節", body)
        self.assertIn("売上フィード", body)
        self.assertEqual([x["month"] for x in self.m.listing()], ["2026-09"])
        self.m.save("2026-09", "u")                            # 同じ月は上書き
        self.assertEqual(len(self.m.listing()), 1)
        self.assertEqual(self.m.get("2026-09")["generated_by"], "u")


if __name__ == "__main__":
    unittest.main()
