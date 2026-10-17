#!/usr/bin/env python3
"""
同型の検知（FR-139・ADR-104）の検査。

見ているのは:
  - 構成の印は商品の形の語の集合（表記ゆれを吸収・具体の語があれば一般の語を外す・セットの印）
  - 印が同じ案を数え、テーマが違うものを「別テーマ」として数える（使い回しの疑い）
  - 形の語が無ければ「判定できない」（0 件と区別）
  - 過去の新商品の名前とも比べる
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class SameType(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, sametype
        seed.run()
        self.store, self.m = store, sametype

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_signature(self):
        self.assertEqual(self.m.signature("推し色アクスタ", ""), ["アクスタ"])
        self.assertEqual(self.m.signature("アクリルスタンドと缶バッチのセット", ""), ["アクスタ", "缶バッジ", "セット"])
        self.assertEqual(self.m.signature("フォトカードケース", ""), ["フォトカード"], "具体の語があれば「カード」は外す")
        self.assertEqual(self.m.signature("新型iPhoneスキンシール", ""), ["スキンシール"])
        self.assertEqual(self.m.signature("推しの誕生日を祝う何か", ""), [])

    def test_counts_same_signature_and_other_theme(self):
        from tests.test_ideas import _idea
        a = _idea("推し活アクスタ＋缶バッジセット", summary="アクスタと缶バッジのセット", theme_id="oshikatsu")
        b = _idea("出産祝いアクスタ＋缶バッジセット", summary="アクスタと缶バッジのセット", theme_id="lifeevent")
        c = _idea("推し活うちわ", summary="うちわ", theme_id="oshikatsu")
        r = self.m.check("卒団アクスタ 缶バッジ セット", "", "lifeevent")
        self.assertEqual(r["state"], "同型あり")
        self.assertEqual(r["signature"], ["アクスタ", "缶バッジ", "セット"])
        self.assertEqual({x["id"] for x in r["ideas"]}, {a, b})
        self.assertEqual((r["other_theme"], r["same_theme"]), (1, 1))
        r2 = self.m.check("推し活アクスタ＋缶バッジセット", "", "oshikatsu", exclude_id=a)
        self.assertEqual({x["id"] for x in r2["ideas"]}, {b})
        self.assertEqual(self.m.check("うちわ", "", None)["ideas"][0]["id"], c)
        self.assertEqual(self.m.check("推しの誕生日", "", None)["state"], "判定できない")
        self.assertEqual(self.m.check("ポーチ", "", None)["state"], "同型なし")

    def test_past_products_are_compared(self):
        self.store.ex("INSERT INTO past_product (code, name, launch_date, first12_yen, months_seen, complete, source, imported_at) "
                      "VALUES ('P1', '推し色アクスタ', '2024-05-01', 100, 12, 1, '検査', '2026-10-18')")
        self.store.conn().commit()
        r = self.m.check("新しいアクスタ", "", None)
        self.assertEqual([p["code"] for p in r["past"]], ["P1"])

    def test_detail_and_similar_api_carry_sametype(self):
        from tests.test_ideas import _idea
        from app import idea
        a = _idea("推し活アクスタ", summary="アクスタ")
        d = idea.detail(a)
        self.assertEqual(d["sametype"]["signature"], ["アクスタ"])
        self.assertEqual(d["sametype"]["ideas"], [], "自分は数えない")


if __name__ == "__main__":
    unittest.main()
