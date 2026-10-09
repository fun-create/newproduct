#!/usr/bin/env python3
"""
4モールの出品依頼（FR-119・ADR-075）の検査。

  - カルテにあることだけを並べ、無いものは「（未記入）」・冒頭に埋まっていない項目
  - 4モールぶん作る。店の商品番号は CIP の紐付け表に登録済みのものだけ（モールの店名で引く）
  - 文字数の上限を書き写さない（数えて出すだけ）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class MallReq(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, mallreq, seisan
        seed.run()
        self.m, self.store, self.seisan = mallreq, store, seisan
        self.pid = project.create("u", expand=False, internal_name="推し色マグ", flow_type="meire",
                                  launch_date="2026-12-01")["id"]
        project.save_section(self.pid, "F.name", "推し色マグカップ 名入れ", "u")
        project.save_section(self.pid, "C.diff", "色を選べる\n名入れ無料\n即日発送", "u")
        with store.tx() as c:
            c.execute("INSERT INTO seisan_registration (project_id,state,product_code) VALUES (?,?,?)",
                      (self.pid, "登録済", "PC-001"))
        self.orig = seisan.store_codes
        seisan.store_codes = lambda: {"items": [
            {"store": "楽天", "store_code": "mug-001", "product_code": "PC-001"},
            {"store": "amazon", "store_code": "ASIN-X", "product_code": "PC-001"},
            {"store": "楽天", "store_code": "other", "product_code": "PC-999"}]}

    def tearDown(self):
        self.seisan.store_codes = self.orig
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_four_malls_from_karte(self):
        r = self.m.build(self.pid)
        self.assertEqual([x["mall"] for x in r["malls"]], ["rakuten", "amazon", "yahoo", "giftmall"])
        rk = r["malls"][0]
        self.assertEqual(rk["store_codes"], ["mug-001"], "他の商品の番号は混ぜない")
        self.assertEqual(r["malls"][1]["store_codes"], ["ASIN-X"])
        self.assertEqual(r["malls"][2]["store_codes"], [])
        self.assertIn("推し色マグカップ 名入れ", rk["text"])
        self.assertIn("・色を選べる", rk["text"])
        self.assertIn("ターゲット・使用シーン", r["missing"])
        self.assertTrue(rk["text"].count("（未記入）") >= 3)
        self.assertIn("12字", rk["text"], "商品名（案）の文字数を数えて出す")
        self.assertIn("埋まっていない項目", rk["text"])


if __name__ == "__main__":
    unittest.main()
