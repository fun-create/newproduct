#!/usr/bin/env python3
"""
原価・調達の AI 案（FR-202・ADR-117）の検査。**claude も CIP も呼ばない。**

見ているのは:
  - AI が返した code のうち、CIP の一覧にあるものだけを案にする（無いものは捨てて未確認に書く）
  - 単価・仕入先・リードタイムは CIP の値を使う（AI の答えに数字があっても使わない）
  - 「候補に足す」で相見積の候補になる。使用量は入れない。全部決めたら案を閉じる
  - カルテの資材・作り方の文が空なら走らせない。未ログインなら失敗で案を作らない
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

MATS = [{"code": "M-101", "kind": "原材料", "category": "アクリル", "name": "透明アクリル板 3mm", "unit_price": 820, "supplier": "A商事",
         "lead_days": 7, "safety_days": 3, "lot_size": 10, "unit": "枚"},
        {"code": "M-202", "kind": "梱包材", "category": "袋", "name": "OPP袋 小", "unit_price": 3.5, "supplier": None,
         "lead_days": 5, "safety_days": 2, "lot_size": 100, "unit": "枚"}]


def _proc(payload):
    return types.SimpleNamespace(stdout=json.dumps(payload, ensure_ascii=False), stderr="", returncode=0)


class AiCost(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, ai_cost
        seed.run()
        self.store, self.m, self.project = store, ai_cost, project
        self.pid = project.create("u", expand=False, internal_name="推し色アクスタ", flow_type="freecut",
                                  launch_date="2026-12-01")["id"]

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def runner(self, answer, seen=None):
        def run(cmd, **kw):
            if seen is not None:
                seen["prompt"] = cmd[cmd.index("-p") + 1]
            return _proc({"result": json.dumps(answer, ensure_ascii=False), "is_error": False, "total_cost_usd": 0.04,
                          "modelUsage": {"claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"}}})
        return run

    def test_needs_material_text(self):
        r = self.m.start_run(self.pid, "u", runner=self.runner({}), materials_fn=lambda: MATS, sync=True, force=True)
        self.assertFalse(r["started"])
        self.assertIn("空です", r["reason"])

    def test_pick_from_cip_list_and_add_candidate(self):
        self.project.save_section(self.pid, "D.material", "本体は透明アクリル 3mm。個包装は OPP 袋", "u")
        seen = {}
        ans = {"items": [{"code": "M-101", "part": "本体", "why": "本体が透明アクリル 3mm", "unit_price": 1},
                         {"code": "M-999", "part": "本体", "why": "作った code"},
                         {"code": "M-202", "part": "包装", "why": "個包装の袋"}],
               "unverified": ["台座の材料は一覧に無い"]}
        r = self.m.start_run(self.pid, "u", runner=self.runner(ans, seen), materials_fn=lambda: MATS, sync=True, force=True)
        self.assertEqual(self.store.val("SELECT stage FROM ai_run WHERE id=?", (r["run_id"],)), "完了")
        self.assertIn("M-101\tアクリル\t透明アクリル板 3mm\t枚", seen["prompt"], "一覧（code・分類・名前・単位）を渡す")
        self.assertNotIn("820", seen["prompt"], "単価は渡さない（AI に数字を扱わせない）")
        p = self.m.latest(self.pid)
        self.assertEqual([x["code"] for x in p["items"]], ["M-101", "M-202"], "一覧に無い code は捨てる")
        self.assertEqual(p["items"][0]["unit_price"], 820, "単価は CIP の値（AI の 1 は使わない）")
        self.assertEqual(p["items"][1]["part"], "その他", "決めた区分の外は「その他」")
        self.assertTrue(any("M-999" in u for u in p["unverified"]))
        self.assertTrue(any("使用量" in u for u in p["unverified"]))
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM sourcing_candidate"), 0, "案は候補ではない")
        self.m.add_item(p["id"], "M-101", "u")
        c = self.store.one("SELECT * FROM sourcing_candidate")
        self.assertEqual((c["kind"], c["part"], c["supplier"], c["unit_price"], c["lead_days"], c["min_lot"]),
                         ("資材", "本体", "A商事", 820, 7, 10))
        self.assertIn("M-101", c["note"])
        with self.assertRaises(ValueError):
            self.m.add_item(p["id"], "M-101", "u")                     # 二度は足さない
        self.m.add_item(p["id"], "M-202", "u")
        self.assertEqual(self.store.one("SELECT supplier FROM sourcing_candidate WHERE id=(SELECT MAX(id) FROM sourcing_candidate)")["supplier"],
                         "（CIP に仕入先の登録なし）")
        self.assertIsNone(self.m.latest(self.pid), "全部決めたら案を閉じる")

    def test_not_logged_in_makes_no_proposal(self):
        self.project.save_section(self.pid, "D.material", "アクリル", "u")
        runner = lambda cmd, **kw: _proc({"result": "Not logged in · Please run /login", "is_error": True})  # noqa: E731
        r = self.m.start_run(self.pid, "u", runner=runner, materials_fn=lambda: MATS, sync=True, force=True)
        self.assertEqual(self.store.val("SELECT stage FROM ai_run WHERE id=?", (r["run_id"],)), "失敗")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM cost_ai_proposal"), 0)


if __name__ == "__main__":
    unittest.main()
