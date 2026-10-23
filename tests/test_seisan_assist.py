#!/usr/bin/env python3
"""
CIP 登録のアシスト（FR-227 採番の案・FR-228 登録票の AI 入力アシスト・ADR-125）の検査。CIP も claude も呼ばない。

見ているのは:
  - 採番の案: 同じ分類で多い頭の英字の「いまの最大の次」。CIP に同じコードがあれば次へ。分類が無ければ案を出さない
  - AI の案: CIP の選択肢・商品の構成に無い値は落として未確認へ。レシピの無い商品は複製元にしない
  - AI に商品名を渡さない・出させない（構成に名前の列が無い）
  - 案は登録ではない（seisan_registration は変わらない）。未ログインなら失敗で案を作らない
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

CODES = [{"product_code": "AG1004", "cat1": "アクリル製品", "cat2": "アクリルスタンド"},
         {"product_code": "AG1115", "cat1": "アクリル製品", "cat2": "アクリルスタンド"},
         {"product_code": "AI1200", "cat1": "アクリル製品", "cat2": "キーホルダー"},
         {"product_code": "AG1116-2", "cat1": "アクリル製品", "cat2": "アクリルスタンド"},
         {"product_code": "BD1712", "cat1": "シール・ステッカー", "cat2": "スキンシール"}]
VOC = {"source": "seisan", "sales_types": ["Webdeco", "フリーカット", "名入れ"], "cat1": ["アクリル製品", "シール・ステッカー"],
       "cat2": ["アクリルスタンド", "スキンシール"], "cat3": ["透明", "なし"]}
PRODS = [{"code": "AG1004", "sales_type": "フリーカット", "cat1": "アクリル製品", "cat2": "アクリルスタンド", "cat3": "透明",
          "shape": "自由形", "size": "10cm", "color": "", "has_recipe": True},
         {"code": "AG1115", "sales_type": "フリーカット", "cat1": "アクリル製品", "cat2": "アクリルスタンド", "cat3": "透明",
          "shape": "自由形", "size": "15cm", "color": "", "has_recipe": False}]


def _proc(payload):
    return types.SimpleNamespace(stdout=json.dumps(payload, ensure_ascii=False), stderr="", returncode=0)


class SeisanAssist(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, seisan, ai_seisan
        seed.run()
        self.store, self.seisan, self.m = store, seisan, ai_seisan
        self.pid = project.create("u", expand=False, internal_name="推し色アクスタ", flow_type="freecut")["id"]
        project.save_section(self.pid, "D.material", "透明アクリル 3mm の自由形スタンド", "u")

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_code_suggest(self):
        taken = {"AG1116"}
        r = self.seisan.code_suggest("アクリル製品", "アクリルスタンド", exists_fn=lambda c: c in taken, codes_fn=lambda: CODES)
        self.assertEqual((r["prefix"], r["max_code"], r["proposal"]), ("AG", "AG1115", "AG1117"),
                         "同じ分類で多い頭の英字の最大の次。CIP にあれば次へ。AG1116-2 は形が違うので数えない")
        self.assertIn("2 件のうち 2 件", r["basis"])
        r2 = self.seisan.code_suggest("無い分類", exists_fn=lambda c: False, codes_fn=lambda: CODES)
        self.assertIsNone(r2["proposal"])
        self.assertIsNone(self.seisan.code_suggest("", exists_fn=lambda c: False, codes_fn=lambda: CODES)["proposal"])

    def runner(self, answer, seen=None):
        def run(cmd, **kw):
            if seen is not None:
                seen["prompt"] = cmd[cmd.index("-p") + 1]
            return _proc({"result": json.dumps(answer, ensure_ascii=False), "is_error": False, "total_cost_usd": 0.03,
                          "modelUsage": {"claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"}}})
        return run

    def test_ai_proposal_checked_against_cip(self):
        seen = {}
        ans = {"fields": {"sales_type": "フリーカット", "cat1": "アクリル製品", "cat2": "アクリルスタンド", "cat3": "透明",
                          "shape": "自由形", "size": "10cm", "color": "", "copy_recipe_from": "AG1115"},
               "reasons": {"sales_type": "フリーカット商品として作る"}, "unverified": []}
        r = self.m.start_run(self.pid, "u", runner=self.runner(ans, seen), products_fn=lambda: (VOC, PRODS), sync=True, force=True)
        self.assertEqual(self.store.val("SELECT stage FROM ai_run WHERE id=?", (r["run_id"],)), "完了")
        p = self.m.latest(self.pid)
        self.assertEqual(p["fields"]["cat2"], "アクリルスタンド")
        self.assertEqual(p["fields"]["copy_recipe_from"], "", "レシピの無い商品は複製元にしない")
        self.assertTrue(any("AG1115" in u for u in p["unverified"]))
        self.assertIn("AG1004\tフリーカット\tアクリル製品", seen["prompt"], "構成（コード・販売タイプ・分類…）を渡す")
        self.assertNotIn("name", json.dumps(PRODS[0]))
        self.assertIn("商品名", self.m.SYSTEM)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM seisan_registration"), 0, "案は登録でも下書きでもない")
        bad = {"fields": {"sales_type": "知らない", "cat1": "アクリル製品", "cat2": "スキンシール", "cat3": "", "shape": "", "size": "",
                          "color": "", "copy_recipe_from": ""}}
        self.m.start_run(self.pid, "u", runner=self.runner(bad), products_fn=lambda: (VOC, PRODS), sync=True, force=True)
        p2 = self.m.latest(self.pid)
        self.assertEqual((p2["fields"]["sales_type"], p2["fields"]["cat2"]), ("", ""), "CIP に無い値・無い組み合わせは落とす")
        self.assertEqual(p2["fields"]["cat1"], "アクリル製品")

    def test_not_logged_in_and_empty_karte(self):
        runner = lambda cmd, **kw: _proc({"result": "Not logged in · Please run /login", "is_error": True})  # noqa: E731
        r = self.m.start_run(self.pid, "u", runner=runner, products_fn=lambda: (VOC, PRODS), sync=True, force=True)
        self.assertEqual(self.store.val("SELECT stage FROM ai_run WHERE id=?", (r["run_id"],)), "失敗")
        self.assertIsNone(self.m.latest(self.pid))
        from app import project
        empty = project.create("u", expand=False, internal_name="空")["id"]
        self.assertFalse(self.m.start_run(empty, "u", runner=runner, products_fn=lambda: (VOC, PRODS), sync=True, force=True)["started"])


if __name__ == "__main__":
    unittest.main()
