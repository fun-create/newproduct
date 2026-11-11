#!/usr/bin/env python3
"""週次レポートの自動受け取り（2026-11-10 依頼書 2-5・ADR-138）。
同じファイルは2度取り込まない・同じ期間の訂正版は入れ替え（足さない）・明細の Excel は断る・失敗は記録して次で送り直せる・鍵。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests"))
from test_newprod import SHEET, xlsx  # noqa: E402

N1 = "週次売上分析_20261023-20261105.xlsx"


class Feed(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, weeklyfeed
        seed.run()
        self.store, self.m = store, weeklyfeed

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_same_file_twice_is_not_counted_twice(self):
        data = xlsx({"新商品": SHEET})
        self.assertEqual(self.m.receive(N1, data, "windows", "feed")["result"], "取り込み")
        self.assertEqual(self.m.receive(N1, data, "windows", "feed")["result"], "取り込み済み（同じ内容）")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM newprod_weekly", (), 0), 1)
        self.assertEqual(len(self.m.known()), 1)

    def test_correction_replaces_and_does_not_add(self):
        from app import newprod
        self.m.receive(N1, xlsx({"新商品": SHEET}), "windows", "feed")
        fixed = [r[:] for r in SHEET]
        fixed[1][3] = 99999                                  # 1行目の売上を直した訂正版
        r = self.m.receive(N1, xlsx({"新商品": fixed}), "windows", "feed")
        self.assertEqual(r["result"], "訂正版で入れ替え")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM newprod_weekly", (), 0), 1)
        self.assertEqual(newprod.latest()["rows"][0]["sales"], 99999)

    def test_older_period_does_not_become_latest(self):
        from app import newprod
        self.m.receive(N1, xlsx({"新商品": SHEET}), "windows", "feed")
        self.m.receive("週次売上分析_20261009-20261022.xlsx", xlsx({"新商品": SHEET}), "windows", "feed")
        self.assertEqual(newprod.latest()["period_to"], "2026-11-05")     # 過去分を後から送っても最新は変わらない

    def test_detail_file_and_broken_file_are_refused_and_logged(self):
        with self.assertRaises(ValueError):
            self.m.receive("週次売上明細_20261023-20261105.xlsx", xlsx({"新商品": SHEET}), "windows", "feed")
        with self.assertRaises(ValueError):
            self.m.receive(N1, b"not an excel", "windows", "feed")
        st = self.m.status()
        self.assertEqual(len(st["open_errors"]), 2)
        self.assertEqual(self.m.known(), [])                               # 失敗は取り込み済みにしない → 次で送り直す
        self.m.receive(N1, xlsx({"新商品": SHEET}), "windows", "feed")
        self.assertEqual(len(self.m.status()["open_errors"]), 1)           # 送り直して直ったものは消える
        self.assertIsNotNone(self.m.status()["last_success"])

    def test_windows_must_send_summary_only_and_dedupes_by_source_hash(self):
        full = xlsx({"新商品": SHEET, "明細_全件": [["店名", "メッセージ"], ["x", "お客さまの名前入り"]]})
        with self.assertRaises(ValueError):
            self.m.receive(N1, full, "windows", "feed")                         # 明細のシートがあれば断る
        summary = xlsx({"新商品": SHEET[:5]})
        src = "a" * 64
        self.assertEqual(self.m.receive(N1, summary, "windows", "feed", source_sha=src)["result"], "取り込み")
        self.assertIn(src, self.m.known())                                     # 元のファイルの指紋で覚える
        again = xlsx({"新商品": SHEET[:5]}) + b""                              # 写しは作るたびに変わってよい
        self.assertEqual(self.m.receive(N1, again, "windows", "feed", source_sha=src)["result"], "取り込み済み（同じ内容）")
        self.assertIn(self.m.receive(N1, full, "画面:u", "u")["result"], ("取り込み", "訂正版で入れ替え"))  # 画面から手で入れるのは今までどおり

    def test_token(self):
        tf = Path(tempfile.mkdtemp()) / "weekly_feed_token"
        with mock.patch.object(self.m, "token_path", return_value=tf):
            self.assertFalse(self.m.token_ok("x" * 32))                    # 置かれていなければ誰も通さない
            tf.write_text("A" * 40 + "\n")
            self.assertTrue(self.m.token_ok("A" * 40))
            self.assertFalse(self.m.token_ok("A" * 39 + "B"))
            self.assertFalse(self.m.token_ok(""))


if __name__ == "__main__":
    unittest.main()
