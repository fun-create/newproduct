#!/usr/bin/env python3
"""商品ABC分析・比較ABC分析（ADR-050）。仮の売上フィードで見る。"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Abc(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="np-abc-"))
        self.db = self.dir / "sales_v1.sqlite"
        c = sqlite3.connect(self.db)
        c.executescript("""
          CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE orders (shop TEXT, order_no TEXT, order_date TEXT, status TEXT, PRIMARY KEY (shop, order_no));
          CREATE TABLE order_lines (shop TEXT, order_no TEXT, line_no INTEGER, sku_no TEXT, item_code TEXT,
                                    qty INTEGER, unit_price INTEGER, option_price INTEGER, product_url TEXT);
          INSERT INTO meta VALUES ('contract_version','1');
        """)
        n = [0]

        def sale(d, sku, amt, status="COMPLETED"):
            n[0] += 1
            c.execute("INSERT INTO orders VALUES ('funcreate',?,?,?)", (str(n[0]), d, status))
            c.execute("INSERT INTO order_lines VALUES ('funcreate',?,0,?,'',1,?,0,'')", (str(n[0]), sku, amt))
        # 2026-08: A1 600 / A2 250 / B1 100 / C1 50 → 合計1000
        for sku, amt in (("A1", 600), ("A2", 250), ("B1", 100), ("C1", 50)):
            sale("2026-08-10", sku, amt)
        sale("2026-08-11", "X", 999, "CANCELLED")                  # 取消は数えない
        # 2025-08（前年）: A1 500 / A2 300 / OLD 200 / B1 100
        for sku, amt in (("A1", 500), ("A2", 300), ("OLD", 200), ("B1", 100)):
            sale("2025-08-10", sku, amt)
        # 今月（途中）: 2026-10-01 と、前年の同じ日付まで・それ以降
        sale("2026-10-01", "A1", 100)
        sale("2025-10-01", "A1", 70)
        sale("2025-10-20", "A1", 9999)                             # 前年の「その日以降」は比べない
        c.commit(); c.close()
        os.environ["NEWPRODUCT_SALESFEED"] = str(self.db)
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-02"
        from app import salesfeed, abc, seisan
        self.m, self.f, self.seisan = abc, salesfeed, seisan
        salesfeed._MAP.update(at=0.0, map=None)
        self._orig = seisan.store_codes
        seisan.store_codes = lambda: {"as_of": "x", "fba": [], "items": [
            {"store": "グッズ本店", "store_code": "A1", "product_code": "P1", "cat1": "布製品", "cat2": "タオル", "cat3": ""}]}

    def tearDown(self):
        self.seisan.store_codes = self._orig
        for k in ("NEWPRODUCT_SALESFEED", "NEWPRODUCT_TODAY"):
            os.environ.pop(k, None)

    def test_abc_classes_use_cumulative_before_the_item(self):
        r = self.m.analyze("goods", "2026-08", "2026-08", "none")
        # 手前までの累計で決める: A1 手前0%→A ／ A2 手前60%→A ／ B1 手前85%→B ／ C1 手前95%→C
        self.assertEqual([(x["key"], x["class"]) for x in r["rows"]],
                         [("A1", "A"), ("A2", "A"), ("B1", "B"), ("C1", "C")])
        self.assertEqual(r["current"]["total"], 1000.0)
        cl = r["current"]["classes"]
        self.assertEqual((cl["A"]["count"], cl["A"]["revenue"], cl["A"]["share"]), (2, 850.0, 85.0))

    def test_comparison_classes(self):
        r = self.m.analyze("goods", "2026-08", "2026-08", "yoy")
        ch = r["changes"]
        self.assertEqual([x["key"] for x in ch["増加"]["rows"]], ["A1"])
        self.assertEqual([x["key"] for x in ch["減少"]["rows"]], ["A2"])
        self.assertEqual([x["key"] for x in ch["同額"]["rows"]], ["B1"])
        self.assertEqual([x["key"] for x in ch["消滅"]["rows"]], ["OLD"])
        self.assertEqual([x["key"] for x in ch["新規"]["rows"]], ["C1"])
        self.assertEqual(ch["消滅"]["previous"], 200.0)
        self.assertEqual(ch["新規"]["current"], 50.0)
        self.assertEqual(sum(v["diff"] for v in ch.values()),
                         r["current"]["total"] - r["compare"]["total"])

    def test_no_data_period_is_not_compared(self):
        r = self.m.analyze("goods", "2026-04", "2026-04", "yoy")    # 前年 2025-04 は売上フィードの外
        self.assertIsNone(r["compare"])
        self.assertIn("0円と扱うと", r["why_compare"])
        self.assertNotIn("changes", r)

    def test_partial_month_aligns_previous_year(self):
        r = self.m.analyze("goods", "2026-10", "2026-10", "yoy")
        self.assertEqual(r["period"]["to"], "2026-10-01")
        self.assertEqual(r["compare"]["period"]["to"], "2025-10-01")
        self.assertEqual(r["compare"]["total"], 70.0, "前年の 10/20 は比べない")
        self.assertTrue(any("途中" in n for n in r["notes"]))

    def test_custom_compare_and_validation(self):
        r = self.m.analyze("goods", "2026-08", "2026-08", "custom", "2025-08", "2025-08")
        self.assertEqual(r["compare"]["total"], 1100.0)
        with self.assertRaises(ValueError):
            self.m.analyze("goods", "2026-13", "2026-13", "none")
        with self.assertRaises(ValueError):
            self.m.analyze("goods", "2025-04", "2025-04", "none")
        with self.assertRaises(ValueError):
            self.m.analyze("yahoo", "2026-08", "2026-08", "none")
        with self.assertRaises(ValueError):
            self.m.analyze("goods", "2026-08", "2026-08", "custom")

    def test_names_and_paths(self):
        r = self.m.analyze("goods", "2026-08", "2026-08", "none")
        a1 = r["rows"][0]
        self.assertEqual(a1["path"], "布製品 / タオル")
        self.assertEqual(a1["name_label"], "未取得")               # products 表が無い


if __name__ == "__main__":
    unittest.main()
