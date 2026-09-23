#!/usr/bin/env python3
"""
機会カレンダーの取り込み（F-2 ／ FR-78・FR-79）の検査。**外部通信をしない。**

    python3 -m unittest discover -s tests -v

2026-09-21 に「列位置がずれていて機械では読めない」として 0件のままにした表
（ADR-020）。読み方を**式で裏取りする形**に変えて取り込んだ（ADR-032）。
**恐れていたことは実在した**ので、それが再発しないことを見る。

  - 名前が **8列目と10列目**に分かれていること（12件 / 7件）を数で固定する
  - **10列目には別のもの（新商品が作れていない13件）も入っている。**
    式の検算で分かれること。**列位置だけで読むと混ざる**
  - 総合＝購買意欲×2＋写真親和性＋発生頻度×2 が **19件すべて誤差0**
  - **名前の揺れを推測で寄せない。**一致しない6件に印を付けないこと
  - 日付を `MM-DD` に直さず**原文のまま**持つこと
  - **2回流しても増えない**
  - 元表の**ブロック2（年齢別124行）を入れない**こと
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def _tool():
    spec = importlib.util.spec_from_file_location("import_events",
                                                  BASE / "tools" / "import_events.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-09-23"
        cls.dbfile = p
        from app import store
        store.close()
        from app import seed
        seed.run()
        cls.m = _tool()
        cls.d = cls.m.parse(cls.m.read_rows())

    @classmethod
    def tearDownClass(cls):
        from app import store
        store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(cls.dbfile + s)
            except OSError:
                pass


class TestParse(Base):
    def test_counts(self):
        self.assertEqual(len(self.d["annual"]), 53)
        self.assertEqual(len(self.d["scored"]), 19)
        self.assertEqual(len(self.d["gaps"]), 13)

    def test_the_name_column_really_is_split_between_8_and_10(self):
        """**09-21 に恐れたずれは実在する。**数で固定して、読み落としに気づけるように。"""
        cols = {}
        for e in self.d["scored"]:
            cols[e["col"] + 1] = cols.get(e["col"] + 1, 0) + 1
        self.assertEqual(cols, {8: 12, 10: 7})

    def test_every_score_satisfies_the_formula(self):
        """総合 ＝ 購買意欲×2 ＋ 写真親和性 ＋ 発生頻度×2（満点40）。**19件すべて誤差0。**"""
        for e in self.d["scored"]:
            self.assertEqual(e["gift_intent"] * 2 + e["photo_fit"] + e["frequency"] * 2,
                             e["total"], e["name"])
            self.assertLessEqual(e["total"], 40)

    def test_priority_is_monotonic_across_the_column_shift(self):
        """**ずれた7件は同じ並びの続き。**総合が下がり続けることが2つ目の裏取り。"""
        totals = [e["total"] for e in self.d["scored"]]
        self.assertEqual(totals, sorted(totals, reverse=True))
        self.assertEqual(totals[0], 38)
        self.assertEqual(totals[-1], 25)

    def test_the_other_list_in_column_10_is_not_mistaken_for_scores(self):
        """**10列目には「新商品が作れていない13件」も入っている。**混ざらないこと。"""
        scored = {e["name"] for e in self.d["scored"]}
        gaps = {e["name"] for e in self.d["gaps"]}
        self.assertIn("家族旅行", scored)            # 10列目・採点あり
        self.assertIn("入園・入学", gaps)             # 10列目・採点なし
        self.assertNotIn("入園・入学", scored)
        # 商品例つきのものが採点側へ紛れ込んでいないこと
        self.assertTrue(any(e["ideas"] for e in self.d["gaps"]))

    def test_dates_are_kept_verbatim(self):
        """**`MM-DD` に直さない。**直せない行が多く、直すと正確に見えてしまう。"""
        days = {e["day"] for e in self.d["annual"] if e["day"]}
        self.assertIn("11月15日前後の土日", days)
        self.assertIn("1月の第２月曜日", days)
        self.assertIn("7〜8月", days)

    def test_months_carry_down_from_merged_cells(self):
        by = {e["name"]: e for e in self.d["annual"]}
        self.assertEqual(by["お花見"]["month"], 4, "結合セルの月が掛かっている")
        self.assertEqual(by["大晦日"]["month"], 12)
        self.assertTrue(all(e["month"] for e in self.d["annual"]), "月が空の行が無いこと")

    def test_name_variants_are_not_guessed(self):
        """**推測で寄せない。**「入園入学」と「入学式」を機械で結ばない。"""
        un = self.d["sellable_unmatched"]
        self.assertEqual(len(un), 6)
        self.assertIn("入園入学", un)
        self.assertIn("七五三/犬の日", un, "2つのイベントなので、なおさら機械で寄せない")


class TestApply(Base):
    def test_import_is_idempotent(self):
        from app import store
        self.m.apply(self.d)
        first = self.m.report()
        self.m.apply(self.d)
        self.assertEqual(first, self.m.report(), "2回流しても増えないこと")
        self.assertEqual(store.val("SELECT COUNT(*) FROM theme WHERE kind='年間イベント'"),
                         53)

    def test_scores_land_with_their_source_row_and_column(self):
        """**どの行をどちらとして読んだかを残す。**人が元表と突き合わせられるように。"""
        from app import store
        self.m.apply(self.d)
        r = store.one("SELECT t.source_row, t.source_col, s.total, s.source "
                      "FROM theme t JOIN theme_score s ON s.theme_id=t.id "
                      "WHERE t.label='家族旅行'")
        self.assertEqual(r["source_col"], 10)
        self.assertEqual(r["total"], 27)
        self.assertIn("10列目", r["source"])

    def test_the_gap_list_marks_the_same_row_not_a_new_one(self):
        """「還暦とか」は採点側にも欠け側にもある。**2件にしない。**"""
        from app import store
        self.m.apply(self.d)
        self.assertEqual(store.val(
            "SELECT COUNT(*) FROM theme WHERE label='還暦とか'"), 1)
        r = store.one("SELECT product_gap, id FROM theme WHERE label='還暦とか'")
        self.assertEqual(r["product_gap"], 1)
        self.assertIsNotNone(store.one("SELECT 1 FROM theme_score WHERE theme_id=?",
                                       (r["id"],)), "採点も残っていること")

    def test_unmatched_names_get_no_mark(self):
        from app import store
        self.m.apply(self.d)
        # 「入学式」は 6列目の「入園入学」と揺れているだけだが、**印は付けない**
        self.assertEqual(store.val(
            "SELECT sellable FROM theme WHERE label='入学式'"), 0)
        self.assertEqual(store.val(
            "SELECT sellable FROM theme WHERE label='母の日'"), 1, "一致する分は付く")

    def test_block2_is_not_imported(self):
        """元表59〜185行目（年齢別 × 購入者・124行）は**入れない。**"""
        from app import store
        self.m.apply(self.d)
        self.assertEqual(store.val(
            "SELECT COUNT(*) FROM theme WHERE source_row >= 58"), 0,
            "ブロック2の行を1件も入れていないこと")
        self.assertEqual(store.val("SELECT COUNT(*) FROM theme"), 53 + 31 + 4)

    def test_evaluation_themes_are_untouched(self):
        """採点に使う「評価テーマ」4件（kind='評価テーマ'）を壊さないこと。"""
        from app import store
        self.m.apply(self.d)
        self.assertEqual(store.val(
            "SELECT COUNT(*) FROM theme WHERE kind='評価テーマ'"), 4)


if __name__ == "__main__":
    unittest.main()
