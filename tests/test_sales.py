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



class Feed(unittest.TestCase):
    """売上フィード（段階B）。仮の DB と、seisan の紐付け表の偽物で見る。"""

    def setUp(self):
        import sqlite3
        self.dir = Path(tempfile.mkdtemp(prefix="np-feed-"))
        self.db = self.dir / "sales_v1.sqlite"
        c = sqlite3.connect(self.db)
        c.executescript("""
          CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE orders (shop TEXT, order_no TEXT, order_date TEXT, status TEXT,
                               PRIMARY KEY (shop, order_no));
          CREATE TABLE order_lines (shop TEXT, order_no TEXT, line_no INTEGER, sku_no TEXT,
                               item_code TEXT, qty INTEGER, unit_price INTEGER, option_price INTEGER,
                               product_url TEXT);
          INSERT INTO meta VALUES ('contract_version','1'), ('generated_at','2026-10-01T02:33');
        """)
        def o(shop, no, d, st, lines):
            c.execute("INSERT INTO orders VALUES (?,?,?,?)", (shop, no, d, st))
            for i, (sku, item, q, up, op) in enumerate(lines):
                c.execute("INSERT INTO order_lines VALUES (?,?,?,?,?,?,?,?,?)",
                          (shop, no, i, sku, item, q, up, op, "/kan-" + sku + "/" if shop == "rakuten" else ""))
        o("funcreate", "1", "2026-08-03", "COMPLETED", [("gd1", "", 2, 1000, 100), ("gd9", "", 1, 500, 0)])
        o("funcreate", "2", "2026-08-10", "CANCELLED", [("gd1", "", 5, 1000, 0)])     # 除く
        o("funcreate", "3", "2025-08-10", "COMPLETED", [("gd1", "", 1, 1000, 0)])
        o("funcreate", "4", "2026-09-30", None, [("gd2", "", 1, 300, 0)])
        o("rakuten", "R1", "2026-08-05", "", [("v1", "10001", 1, 800, 0)])
        c.commit(); c.close()
        os.environ["NEWPRODUCT_SALESFEED"] = str(self.db)
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-01"
        os.environ["NEWPRODUCT_AG_EXPORT"] = str(self.dir / "none")
        from app import salesfeed, sales, seisan
        self.f, self.m, self.seisan = salesfeed, sales, seisan
        salesfeed._MAP.update(at=0.0, map=None)
        self._orig = seisan.store_codes
        seisan.store_codes = lambda: {"as_of": "x", "items": [
            {"store": "グッズ本店", "store_code": "gd1", "product_code": "P1", "cat1": "布製品", "cat2": "タオル", "cat3": "", "pack_qty": 1},
            {"store": "グッズ本店", "store_code": "gd2", "product_code": "P2", "cat1": "布製品", "cat2": "タオル", "cat3": "", "pack_qty": 1},
            {"store": "楽天", "store_code": "10001", "product_code": "P3", "cat1": "アクリル製品", "cat2": "アクスタ", "cat3": "", "pack_qty": 1}],
            "fba": []}

    def tearDown(self):
        self.seisan.store_codes = self._orig
        for k in ("NEWPRODUCT_SALESFEED", "NEWPRODUCT_TODAY", "NEWPRODUCT_AG_EXPORT"):
            os.environ.pop(k, None)

    def test_default_is_last_complete_month_and_cancelled_is_excluded(self):
        o = self.m.overview("goods")
        self.assertEqual(o["month"], "2026-09", "今月（10月）は途中なので直近の丸1か月")
        o = self.m.overview("goods", "2026-08")
        self.assertEqual(o["total"]["revenue"], 2700.0)            # (1000+100)*2 + 500。取消は除く
        self.assertEqual(o["total"]["prev_revenue"], 1000.0)
        self.assertEqual(o["total"]["yoy"], 170.0)
        self.assertIn("売上フィード", o["source"])

    def test_composition_uses_seisan_map_and_unmatched_is_one_row(self):
        c = self.m.overview("goods", "2026-08")["composition"]
        self.assertEqual(c["rows"][0]["name"], "布製品")
        self.assertEqual(c["rows"][0]["revenue"], 2200.0)
        self.assertEqual(c["unclassified"], 500.0)                  # gd9 は紐付けに無い
        self.assertEqual(c["rows"][0]["children"][0]["name"], "タオル")

    def test_products_have_codes_not_names(self):
        p = self.m.overview("goods", "2026-08")["products"]
        self.assertEqual(p["rows"][0]["store_code"], "gd1")
        self.assertIsNone(p["rows"][0]["name"])
        self.assertIn("表示名", p["name_note"])
        self.assertEqual(p["rows"][0]["name_label"], "未取得")
        self.assertEqual(p["rows"][0]["product_codes"], ["P1"])
        self.assertEqual(p["rows"][1]["path"], "CIP の紐付けに無い")

    def test_names_come_from_master_table_when_it_exists(self):
        import sqlite3
        c = sqlite3.connect(self.db)
        c.executescript("""CREATE TABLE products (shop TEXT, sku_no TEXT, product_no TEXT, item_code TEXT,
                              name TEXT, name_withheld INTEGER, variation_h TEXT, variation_v TEXT,
                              visible INTEGER, source_updated_at TEXT, fetched_at TEXT);
          INSERT INTO products VALUES ('funcreate','gd1','gd1','','タオル（マスタ名）',0,'','',1,'x','x');
          INSERT INTO products VALUES ('funcreate','gd9','gd9','',NULL,1,'','',1,'x','x');
          INSERT INTO products VALUES ('rakuten','kan-v1','kan-v1','10001','アクスタ（マスタ名）',0,'','',1,'x','x');
          INSERT INTO products VALUES ('rakuten','kan-zz','kan-zz','10001','別の商品（同じ商品番号）',0,'','',1,'x','x');""")
        c.commit(); c.close()
        p = self.m.overview("goods", "2026-08")["products"]
        self.assertEqual(p["rows"][0]["name"], "タオル（マスタ名）")
        self.assertIsNone(p["rows"][1]["name"], "個別の商品は名前を出さない")
        self.assertEqual(p["rows"][1]["name_label"], "個別の商品")
        self.assertIsNone(p["name_note"])
        r = self.m.overview("rakuten", "2026-08")["products"]
        self.assertEqual(r["rows"][0]["name"], "アクスタ（マスタ名）",
                         "楽天は商品管理番号で引く（同じ item_code の別商品の名前を拾わない）")
        # 注文に別の商品管理番号が混ざると、名前を決めつけない
        import sqlite3
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO orders VALUES ('rakuten','R2','2026-08-06','')")
        c.execute("INSERT INTO order_lines VALUES ('rakuten','R2',0,'zz','10001',1,100,0,'/kan-zz/')")
        c.commit(); c.close()
        r = self.m.overview("rakuten", "2026-08")["products"]
        self.assertIsNone(r["rows"][0]["name"])
        self.assertEqual(r["rows"][0]["name_label"], "複数の商品（同じ商品番号）")

    def test_rakuten_uses_item_code(self):
        c = self.m.overview("rakuten", "2026-08")["composition"]
        self.assertEqual(c["rows"][0]["name"], "アクリル製品")
        self.assertEqual(c["unclassified"], 0.0)

    def test_other_contract_version_stops(self):
        import sqlite3
        c = sqlite3.connect(self.db); c.execute("UPDATE meta SET value='2' WHERE key='contract_version'")
        c.commit(); c.close()
        with self.assertRaises(self.f.Unavailable):
            self.f.monthly("goods")
        o = self.m.overview("goods", "2026-08")
        self.assertIn("版", o["feed_why"])                         # 黙って読まない。理由を出す

    def test_project_sales_uses_registered_codes_and_skips_shared(self):
        os.environ["NEWPRODUCT_DB"] = str(self.dir / "np.db")
        from app import store, seed, project
        store.close()
        seed.run()
        pid = project.create("u", expand=False, internal_name="新商品", flow_type="meire",
                             launch_date="2026-08-01")["id"]
        o = self.m.project_sales(pid)
        self.assertIn("CIP への登録", o["why"])
        with store.tx() as c:
            c.execute("INSERT INTO seisan_registration (project_id,state,product_code) VALUES (?,?,?)",
                      (pid, "登録済", "P1"))
        o = self.m.project_sales(pid)
        g = next(x for x in o["sites"] if x["site"] == "goods")
        self.assertEqual(g["store_codes"], ["gd1"])
        self.assertEqual(g["total"], 2200.0)                 # 8/3 の gd1（取消は除く）
        self.assertEqual(g["checkpoints"][0]["revenue"], 2200.0)   # 8/1〜8/14
        self.assertTrue(g["checkpoints"][1]["reached"])
        r = next(x for x in o["sites"] if x["site"] == "rakuten")
        self.assertIsNone(r["total"], "紐付けの無いサイトは 0 ではなく —")
        # 共有の商品番号は数えない
        self.seisan.store_codes = lambda: {"as_of": "x", "fba": [], "items": [
            {"store": "グッズ本店", "store_code": "gd1", "product_code": "P1", "cat1": "a", "cat2": "b", "cat3": ""},
            {"store": "グッズ本店", "store_code": "gd1", "product_code": "P9", "cat1": "a", "cat2": "b", "cat3": ""}]}
        self.f._MAP.update(at=0.0, map=None)
        o = self.m.project_sales(pid)
        self.assertEqual(o["shared"], ["グッズ本店 gd1"])
        store.close()

    def test_new_product_summary_full_incremental_and_unmeasured(self):
        os.environ["NEWPRODUCT_DB"] = str(self.dir / "np2.db")
        from app import store, seed, project
        store.close()
        seed.run()
        pid = project.create("u", expand=False, internal_name="新商品", flow_type="meire",
                             launch_date="2026-08-01")["id"]
        old = project.create("u", expand=False, internal_name="古い", flow_type="meire",
                             launch_date="2025-10-15")["id"]
        with store.tx() as c:
            c.execute("INSERT INTO seisan_registration (project_id,state,product_code) VALUES (?,?,?)", (pid, "登録済", "P1"))
            c.execute("INSERT INTO seisan_registration (project_id,state,product_code) VALUES (?,?,?)", (old, "登録済", "P2"))
        n = self.m.new_product_summary()
        self.assertEqual(n["count"], 2)
        self.assertEqual(n["counted"], 0, "既定は売上に含めない")
        with self.assertRaises(ValueError):
            project.set_revenue(pid, True, "", "u")                       # 方式が要る
        project.set_revenue(pid, True, "全額", "u")
        n = self.m.new_product_summary()
        self.assertEqual(n["totals"]["全額"], 2200.0)
        project.set_revenue(pid, True, "増分", "u")
        r = self.m.new_product_summary()["rows"][0]
        self.assertEqual((r["full"], r["baseline"], r["amount"]), (2200.0, 1000.0, 1200.0))
        project.set_revenue(old, True, "増分", "u")                      # 前年 2024-10 は売上フィードの外
        n = self.m.new_product_summary()
        o = next(x for x in n["rows"] if x["id"] == old)
        self.assertIsNone(o["amount"])
        self.assertIn("未計測", o["why"])
        project.set_revenue(pid, False, "", "u")
        self.assertEqual(self.m.new_product_summary()["counted"], 1)
        store.close()

    def test_months_start_2025_05(self):
        self.assertEqual(self.f.months()[0], "2025-05")
        self.assertNotIn("2025-04", self.m.overview("goods")["months"])


if __name__ == "__main__":
    unittest.main()
