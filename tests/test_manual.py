#!/usr/bin/env python3
"""
操作マニュアル（docs/操作マニュアル.md・画面の「設定」→「操作マニュアル」）の検査。

見ているのは:
  - 移行の手順の節がある（FR-194・ADR-110。移行プランのページは非公開で、商品開発部の人が開けないため）
  - 画面の描き方（ui/app.js の mdRender）が読めない書き方を使っていない
    （番号付きの箇条書き・[文字](URL) のリンク・3段以上の字下げ。読めないと記号がそのまま出る）
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MD = (BASE / "docs" / "操作マニュアル.md").read_text(encoding="utf-8")


class Manual(unittest.TestCase):
    def section(self, head):
        m = re.search(rf"^## {re.escape(head)}.*?$(.*?)(?=^## )", MD, re.M | re.S)
        self.assertIsNotNone(m, f"「{head}」の節が無い")
        return m.group(1)

    def test_migration_section(self):
        s = self.section("移行の手順")
        for want in ("10/19", "10/26", "11/6", "11/9", "担当をまとめて割り振る", "進捗はシートだけに書く",
                     "カレンダーのタスクで「完了」を押さない", "繰り返しの仕事", "シートでやっていたこと"):
            self.assertIn(want, s)

    def test_only_markup_the_screen_can_draw(self):
        for i, ln in enumerate(MD.split("\n"), 1):
            self.assertNotRegex(ln, r"^\s*\d+\. ", f"{i} 行目: 番号付きの箇条書きは画面で描けない")
            self.assertNotRegex(ln, r"\[[^\]]+\]\([^)]+\)", f"{i} 行目: [文字](URL) は画面で描けない")
            self.assertNotRegex(ln, r"^ {4,}- ", f"{i} 行目: 字下げは1段まで")
            self.assertEqual(ln.count("**") % 2, 0, f"{i} 行目: 太字の ** が閉じていない")


if __name__ == "__main__":
    unittest.main()
