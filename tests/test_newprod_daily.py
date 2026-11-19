#!/usr/bin/env python3
"""ラベル×日の売上（ADR-161）。期間ごと入れ替える・週次レポートの月別と月ごとに比べる・形の違うものは断る・
覆っていない期間は None（推測しない）。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Daily(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store, seed, newprod_daily
        store.close()
        seed.run()
        self.store, self.m = store, newprod_daily
        monthly = {"等身大パネル": {"months": ["2026-05", "2026-06", "2026-07"], "sales": [1000, 2000, 500]},
                   "ブロックメモ": {"months": ["2026-05", "2026-06", "2026-07"], "sales": [300, 0, 0]}}
        store.ex("INSERT INTO newprod_weekly (period_from,period_to,file_name,rows,monthly,received_at) VALUES "
                 "('2026-07-03','2026-07-16','x.xlsx',?,?,'2026-07-20')", (json.dumps({"rows": [], "total": None}),
                                                                          json.dumps(monthly, ensure_ascii=False)))
        store.conn().commit()

    def tearDown(self):
        self.store.close()
        os.unlink(self.dbfile)

    def send(self, labels, f="20260501", t="20260720"):
        return self.m.receive({"from": f, "to": t, "amount_col": "売上(税込)", "labels": labels})

    def test_matches_weekly_monthly(self):
        r = self.send({"等身大パネル": {"2026-05-03": 400, "2026-05-30": 600, "2026-06-10": 2000, "2026-07-02": 100},
                       "ブロックメモ": {"20260520": 300}})
        self.assertEqual(r["mismatch"], 0)
        self.assertEqual(r["compared"], 4)                   # 5月・6月 × 2ラベル（7月は週次の期間の終わりの月なので比べない）
        st = self.m.status()
        self.assertEqual(st["check"]["compared_months"], ["2026-05", "2026-06"])
        self.assertEqual(st["amount_col"], "売上(税込)")

    def test_mismatch_is_reported(self):
        r = self.send({"等身大パネル": {"2026-05-03": 990, "2026-06-10": 2000}, "ブロックメモ": {"2026-05-20": 300}})
        self.assertEqual(r["mismatch"], 1)
        mm = self.m.status()["check"]["mismatch"]
        self.assertEqual(mm, [{"label": "等身大パネル", "month": "2026-05", "weekly": 1000, "daily": 990}])

    def test_resend_replaces_not_adds(self):
        self.send({"等身大パネル": {"2026-05-03": 1000}, "古いラベル": {"2026-05-04": 50}})
        self.send({"等身大パネル": {"2026-05-03": 1000}})
        self.assertEqual(self.m.sum_between(["等身大パネル"], "2026-05-01", "2026-05-31"), 1000)
        self.assertEqual(self.m.sum_between(["古いラベル"], "2026-05-01", "2026-05-31"), 0)

    def test_sum_between_needs_coverage(self):
        self.send({"等身大パネル": {"2026-05-03": 1000, "2026-06-10": 2000}})
        self.assertEqual(self.m.sum_between(["等身大パネル"], "2026-05-01", "2026-06-05"), 1000)
        self.assertIsNone(self.m.sum_between(["等身大パネル"], "2026-04-01", "2026-05-31"))   # 覆っていない
        self.assertIsNone(self.m.sum_between([], "2026-05-01", "2026-05-31"))

    def test_bad_shapes_are_refused(self):
        for bad in ({"from": "2026-05-01", "to": "2026-04-01", "labels": {}},
                    {"from": "2026-05-01", "to": "2026-07-01", "labels": {"x": {"2026-08-01": 1}}},
                    {"from": "2026-05-01", "to": "2026-07-01", "labels": {"x": {"2026-05-01": "abc"}}},
                    {"from": "2026-05-01", "to": "2026-07-01", "labels": {"x\ty": {"2026-05-01": 1}}},
                    {"from": "2026-05-01", "to": "2026-07-01", "labels": {"x": {"2026-05-01": float("nan")}}},
                    {"from": "2026-05-01", "to": "2026-07-01"}):
            with self.assertRaises(ValueError):
                self.m.receive(bad)
        self.assertIsNone(self.m.status())
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM newprod_daily"), 0)


if __name__ == "__main__":
    unittest.main()
