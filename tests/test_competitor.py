#!/usr/bin/env python3
"""
競合調査（FR-141〜143）の検査。

見ているのは:
  - **出典 URL と確認日が無ければ断る**（F-9-8）。先の日付・javascript: も断る
  - 分からない値は未確認のまま（0 にしない）
  - **レビュー率が未設定なら推計を出さない**（何が欠けたかを言う）。決めたら「件数 ÷ 率 × 価格」
  - レビュー率を決められるのは 商品開発部・管理者・社長 だけ
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

OK = {"shop": "A工房", "item": "アクリル名札", "channel": "楽天", "url": "https://item.rakuten.co.jp/a/1/",
      "checked_on": "2026-10-01", "price_yen": "1980", "review_count": "120"}


class Competitor(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-05"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, competitor
        seed.run()
        self.m, self.store = competitor, store
        self.pid = project.create("u", expand=False, internal_name="競合検査", flow_type="meire",
                                  launch_date="2026-12-01")["id"]
        with store.tx() as c:
            c.execute("INSERT OR IGNORE INTO role_member (role_code,user_id) VALUES ('devdept','dev')")

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_source_and_date_are_required(self):
        for k, v in (("url", ""), ("url", "javascript:alert(1)"), ("checked_on", ""),
                     ("checked_on", "2026-10-06"), ("shop", "")):
            with self.subTest(k=k, v=v), self.assertRaises(ValueError):
                self.m.save(self.pid, {**OK, k: v}, "u")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM competitor_item"), 0)

    def test_unknown_stays_unknown_and_no_estimate_without_rate(self):
        self.m.save(self.pid, {**OK, "price_yen": "", "review_count": ""}, "u")
        r = self.m.overview(self.pid, "u")["rows"][0]
        self.assertIsNone(r["price_yen"])
        self.assertIsNone(r["review_count"])
        self.assertIsNone(r["estimate"]["revenue"])
        self.assertEqual(r["estimate"]["missing"], ["レビュー率（未設定）", "レビュー件数", "販売価格"])

    def test_estimate_after_rate_is_decided(self):
        self.m.save(self.pid, OK, "u")
        self.assertIsNone(self.m.overview(self.pid, "u")["review_rate"], "既定は未設定（仮の率で埋めない）")
        with self.assertRaises(PermissionError):
            self.m.set_rate("2", "u")
        self.m.set_rate("2", "dev")
        o = self.m.overview(self.pid, "dev")
        self.assertTrue(o["can_set_rate"])
        self.assertEqual(o["rows"][0]["estimate"], {"qty": 6000, "revenue": 11880000, "missing": []})
        self.m.set_rate("", "dev")
        self.assertIsNone(self.m.overview(self.pid, "u")["rows"][0]["estimate"]["revenue"], "空欄で未設定に戻せる")
        with self.assertRaises(ValueError):
            self.m.set_rate("150", "dev")

    def test_edit_delete_and_price_range(self):
        a = self.m.save(self.pid, OK, "u")["id"]
        self.m.save(self.pid, {**OK, "shop": "B社", "price_yen": "2480"}, "u")
        self.m.save(self.pid, {**OK, "id": a, "price_yen": "1680"}, "u")
        o = self.m.overview(self.pid, "u")
        self.assertEqual(o["price_range"], [1680.0, 2480.0])
        self.m.delete(self.pid, a, "u")
        self.assertEqual(len(self.m.overview(self.pid, "u")["rows"]), 1)
        with self.assertRaises(LookupError):
            self.m.delete(self.pid, a, "u")


if __name__ == "__main__":
    unittest.main()
