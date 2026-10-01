#!/usr/bin/env python3
"""
売上実績（2026-10-01 決定）の検査。**外部に触らない**（仮の集計ファイルで見る）。

見ているのは、出どころの数字を**そのまま**見せるか:
  - 未計測を 0 にしない（サイトが出どころに無い／FBA／商品別）
  - **途中の月は前年比を出さない**
  - 分類が付かない分を「分類なし」として1行で出し、構成比の分母を全体にする
  - サイト別の構成は、段階 B まで「未計測」と理由を言う
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def doc(month, complete=True, prev="2025-08"):
    return {
        "contract_version": 1, "generated_at": "2026-09-20 20:28:01", "month": month,
        "prev_year_month": prev, "complete": complete, "note": "n", "source": "seisan order_lines",
        "caveats": ["amazon は自社発送のみ（FBAは含まない）"],
        "totals": {"revenue": 1000.0, "orders": 10, "qty": 20.0, "classified_revenue": 900.0},
        "prev_totals": {"revenue": 800.0},
        "stores": [{"store": "グッズ本店", "revenue": 600.0, "prev_revenue": 500.0},
                   {"store": "うちわ本店", "revenue": 300.0, "prev_revenue": 0.0},
                   {"store": "amazon", "revenue": 100.0, "prev_revenue": 120.0}],
        "cat1": [{"name": "アクリル製品", "revenue": 500.0, "prev_revenue": 400.0},
                 {"name": "布製品", "revenue": 400.0, "prev_revenue": 400.0}],
        "cat2": [{"cat1": "アクリル製品", "cat2": "アクリルスタンド", "revenue": 300.0, "prev_revenue": 250.0}],
    }


class Sales(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="np-sales-"))
        os.environ["NEWPRODUCT_AG_EXPORT"] = str(self.dir)
        from app import sales
        self.m = sales
        for m, c in (("2026-07", True), ("2026-08", True), ("2026-09", False)):
            (self.dir / f"insight_products_{m}.json").write_text(json.dumps(doc(m, c)), encoding="utf-8")
        # 月ではないファイルは拾わない
        (self.dir / "insight_products_latest.json").write_text(json.dumps(doc("2026-09")), encoding="utf-8")

    def tearDown(self):
        os.environ.pop("NEWPRODUCT_AG_EXPORT", None)

    def test_months_and_default_is_latest(self):
        self.assertEqual(self.m.months(), ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(self.m.overview()["month"], "2026-09")

    def test_incomplete_month_has_no_yoy(self):
        o = self.m.overview("all", "2026-09")
        self.assertFalse(o["complete"])
        self.assertIsNone(o["total"]["yoy"])
        self.assertTrue(all(r["yoy"] is None for r in o["composition"]["rows"]))
        o = self.m.overview("all", "2026-08")
        self.assertEqual(o["total"]["yoy"], 25.0)

    def test_unclassified_is_one_row_and_shares_use_the_whole(self):
        c = self.m.overview("all", "2026-08")["composition"]
        self.assertEqual(c["unclassified"], 100.0)
        self.assertEqual(c["unclassified_share"], 10.0)
        self.assertEqual(c["rows"][0]["share"], 50.0)            # 500 / 1000（900 ではない）
        self.assertEqual(c["rows"][0]["children"][0]["name"], "アクリルスタンド")
        total = sum(r["revenue"] for r in c["rows"]) + c["unclassified"]
        self.assertEqual(total, 1000.0)

    def test_site_total_and_unmeasured(self):
        g = self.m.overview("goods", "2026-08")
        self.assertEqual(g["total"]["revenue"], 600.0)
        self.assertEqual(g["total"]["yoy"], 20.0)
        self.assertIsNone(g["composition"]["rows"])
        self.assertIn("未計測", g["composition"]["why"])
        u = self.m.overview("uchiwa", "2026-08")
        self.assertIsNone(u["total"]["yoy"], "前年0円は前年比を出さない")
        r = self.m.overview("rakuten", "2026-08")
        self.assertIsNone(r["total"]["revenue"], "出どころに無い店は 0 ではなく未計測")
        self.assertIn("未計測", r["total"]["why"])
        f = self.m.overview("fba", "2026-08")
        self.assertIsNone(f["total"]["revenue"])
        self.assertIn("FBA", f["total"]["why"])
        self.assertIsNone(f["products"]["rows"])

    def test_trend_follows_site(self):
        t = self.m.overview("amazon", "2026-08")["trend"]
        self.assertEqual([x["month"] for x in t], ["2026-07", "2026-08", "2026-09"])
        self.assertTrue(all(x["revenue"] == 100.0 for x in t))

    def test_missing_source_says_why(self):
        os.environ["NEWPRODUCT_AG_EXPORT"] = str(self.dir / "nope")
        o = self.m.overview()
        self.assertIsNone(o["month"])
        self.assertIn("ありません", o["why"])

    def test_unknown_site_is_refused(self):
        with self.assertRaises(ValueError):
            self.m.overview("yahoo")


if __name__ == "__main__":
    unittest.main()
