#!/usr/bin/env python3
"""
新機種調査（FR-140・ADR-102）の検査。**claude は呼ばない**（stream-json の返事を差し替える）。

見ているのは:
  - 展開の表の取り込み: 列の揃っていない10シートを読め、画面で直した行は取り込みで上書きしない
  - AI に渡すのは対象シリーズと既知の機種。既知の機種（表記ゆれ）は候補に入らない
  - 出典が AI の見たものかを行ごとに出す（見ていない）
  - 候補の決定: 案件にする→④ニューモデル追加の案件ができ表に「検討中」で入る／展開済み→表に「○」／見送り。二度決められない。業務ロールだけ
  - 同じ機種が次の回でまた出たら、前の「提案」は置き換わる。決めたものは触らない
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.test_ai_web import _stream  # noqa: E402


class Device(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, device
        seed.run()
        self.store, self.m = store, device
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('devdept','dev',?)", (store.now_s(),))
        self.m.import_models()

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def _answer(self, rows, none=("glo",)):
        return {"rows": rows, "none": list(none), "unverified": ["発売日は報道ベース"]}

    def test_import_reads_uneven_sheets_and_keeps_edited_rows(self):
        n = self.store.val("SELECT COUNT(*) FROM device_model")
        self.assertGreater(n, 150)
        self.assertEqual(self.store.val("SELECT COUNT(DISTINCT sheet) FROM device_model"), 10)
        ip = self.store.one("SELECT * FROM device_model WHERE sheet='アイパッド' AND model_no IS NOT NULL LIMIT 1")
        self.assertIsNotNone(ip, "iPad の表はモデル番号の列がある")
        iq = self.store.one("SELECT * FROM device_model WHERE sheet='アイコス' AND model='IQOS'")
        self.assertEqual(iq["deploy"], "○")
        self.assertIn("iqos.com", iq["url"] or "")
        # 画面で直した行は取り込みで戻らない
        self.m.save_model({"id": iq["id"], "sheet": "アイコス", "model": "IQOS", "deploy": "掲載終了"}, "dev")
        r = self.m.import_models()
        self.assertGreaterEqual(r["kept_edited"], 1)
        self.assertEqual(self.store.val("SELECT deploy FROM device_model WHERE id=?", (iq["id"],)), "掲載終了")
        with self.assertRaises(PermissionError):
            self.m.save_model({"sheet": "アイコス", "model": "x"}, "nobody")

    def test_known_models_are_passed_and_filtered_out(self):
        inp = self.m.inputs()
        s = {x["code"]: x for x in inp["対象シリーズ"]}
        self.assertEqual(len(s), 12)
        self.assertIn("IQOS", s["iqos"]["既知の機種"])
        self.assertEqual(s["lil"]["既知の機種"], s["iqos"]["既知の機種"], "リルはアイコスのシートを使う")
        seen = {}
        def runner(cmd, **kw):
            seen["prompt"] = cmd[cmd.index("-p") + 1]
            return _stream(self._answer([
                {"series": "iqos", "model": "IQOS ILUMA i PRIME NEO", "release_date": "2026-09", "jp_released": "発売済み",
                 "confidence": "公式", "summary": "新色", "url": "https://item.rakuten.co.jp/a/1/"},
                {"series": "iqos", "model": "I Q O S", "release_date": "2026-01", "jp_released": "発売済み", "confidence": "公式",
                 "summary": "既知の表記ゆれ", "url": "https://item.rakuten.co.jp/a/1/"},
                {"series": "switch", "model": "Switch 2 Lite", "release_date": "不明", "jp_released": "発売予定",
                 "confidence": "噂", "summary": "うわさ", "url": "https://example.com/not-opened"},
                {"series": "zzz", "model": "知らないシリーズ", "url": "https://item.rakuten.co.jp/a/1/"},
            ]), opened=["https://item.rakuten.co.jp/a/1/"])
        r = self.m.start_run("dev", runner=runner, sync=True, force=True)
        self.assertTrue(r["started"])
        self.assertIn("既知の機種", seen["prompt"])
        self.assertIn("直近3か月以内", seen["prompt"])
        cs = self.store.rows(self.store.q("SELECT * FROM device_candidate ORDER BY id"))
        self.assertEqual([c["model"] for c in cs], ["IQOS ILUMA i PRIME NEO", "Switch 2 Lite"], "既知の表記ゆれと知らないシリーズは落とす")
        self.assertEqual(cs[0]["seen"], "開いた")
        self.assertEqual(cs[1]["seen"], "見ていない")
        self.assertEqual(cs[1]["confidence"], "噂")
        v = self.m.view("dev")
        self.assertEqual(v["counts"]["提案"], 2)
        self.assertEqual(v["last"]["body"]["none"], ["glo"])
        self.assertEqual(v["last"]["body"]["dropped"], 2)
        self.assertEqual(self.store.val("SELECT stage FROM ai_run WHERE id=?", (r["run_id"],)), "完了")

    def test_decide_creates_project_or_model_and_only_once(self):
        def runner(cmd, **kw):
            return _stream(self._answer([{"series": "iphone", "model": "iPhone 18 Fold", "release_date": "2026-09-19",
                                          "jp_released": "発売済み", "confidence": "公式", "summary": "折りたたみ",
                                          "url": "https://item.rakuten.co.jp/a/1/"},
                                         {"series": "macbook", "model": "MacBook Zeta", "jp_released": "発売予定",
                                          "confidence": "報道", "url": "https://item.rakuten.co.jp/a/1/"}]),
                           opened=["https://item.rakuten.co.jp/a/1/"])
        self.m.start_run("dev", runner=runner, sync=True, force=True)
        cs = self.store.rows(self.store.q("SELECT * FROM device_candidate ORDER BY id"))
        with self.assertRaises(PermissionError):
            self.m.decide(cs[0]["id"], "案件にした", "nobody")
        r = self.m.decide(cs[0]["id"], "案件にした", "dev")
        p = self.store.one("SELECT * FROM project WHERE id=?", (r["project_id"],))
        self.assertEqual((p["flow_type"], p["internal_name"]), ("newmodel", "iPhone 18 Fold スキンシール"))
        self.assertEqual(self.store.val("SELECT deploy FROM device_model WHERE sheet='アイフォン' AND model='iPhone 18 Fold'"), "検討中")
        with self.assertRaises(ValueError):
            self.m.decide(cs[0]["id"], "見送り", "dev")
        self.m.decide(cs[1]["id"], "展開済み", "dev")
        self.assertEqual(self.store.val("SELECT deploy FROM device_model WHERE sheet='マックブック' AND model='MacBook Zeta'"), "○")
        # 次の回に同じ機種が出ても、決めたものは触らず、候補にも入らない（表に入ったので既知）
        self.m.start_run("dev", runner=runner, sync=True, force=True)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM device_candidate"), 2)

    def test_pending_proposal_is_replaced_by_the_next_run(self):
        def runner(cmd, **kw):
            return _stream(self._answer([{"series": "ipad", "model": "iPad Ultra", "jp_released": "不明", "confidence": "噂",
                                          "url": "https://item.rakuten.co.jp/a/1/"}]), opened=["https://item.rakuten.co.jp/a/1/"])
        self.m.start_run("dev", runner=runner, sync=True, force=True)
        self.m.start_run("dev", runner=runner, sync=True, force=True)
        cs = self.store.rows(self.store.q("SELECT state FROM device_candidate ORDER BY id"))
        self.assertEqual([c["state"] for c in cs], ["見送り", "提案"])


if __name__ == "__main__":
    unittest.main()
