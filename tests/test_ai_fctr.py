#!/usr/bin/env python3
"""
自社側70点の AI 案（FR-200・ADR-115）の検査。**claude は呼ばない。**

見ているのは:
  - 案は点にしない（fctr_self_score は変わらない）。人が採ったときだけ点になり、直したかどうかが残る
  - 上限の外・数でない点は断る。根拠の無い軸は案にしない（未確認に回す）
  - 渡すのはテーマの情報と、同社が作れるもの（マスタの名前）だけ。数字を作らせない指示がある
  - 点の付いたテーマは既定の対象にしない。採るときに空の軸があれば断る
  - 未ログイン（is_error）なら失敗で、案を作らない
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
sys.path.insert(0, str(BASE / "tests"))

from test_fctr import weekly  # noqa: E402


def _proc(payload):
    return types.SimpleNamespace(stdout=json.dumps(payload, ensure_ascii=False), stderr="", returncode=0)


class AiFctr(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        self.json = Path(tempfile.mkdtemp()) / "fctr_weekly.json"
        os.environ["NEWPRODUCT_FCTR"] = str(self.json)
        os.environ["NEWPRODUCT_TODAY"] = "2026-09-28"
        self.json.write_text(json.dumps(weekly("2026-W40", [("結婚式記念", 17.9), ("七五三", 15.2)]), ensure_ascii=False), encoding="utf-8")
        from app import store
        store.close()
        from app import seed, fctr, ai_fctr
        seed.run()
        self.store, self.fctr, self.m = store, fctr, ai_fctr
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('devdept','chieko',?)", (store.now_s(),))
        fctr.ingest()
        self.ids = {t["label"]: t["theme_id"] for t in self.m.themes_now()}

    def tearDown(self):
        self.store.close()
        for k in ("NEWPRODUCT_FCTR", "NEWPRODUCT_TODAY"):
            os.environ.pop(k, None)
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def runner(self, answer, seen=None):
        def run(cmd, **kw):
            if seen is not None:
                seen["prompt"] = cmd[cmd.index("-p") + 1]
            return _proc({"result": json.dumps(answer, ensure_ascii=False), "is_error": False, "total_cost_usd": 0.02,
                          "modelUsage": {"claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"}}})
        return run

    def test_proposal_is_not_a_score_until_adopted(self):
        a, b = self.ids["結婚式記念"], self.ids["七五三"]
        seen = {}
        ans = [{"id": a, "fit": 20, "ops": 14, "speed": 10, "profit": 7,
                "reasons": {"fit": "フリーカットで出せる", "ops": "いまの流れで作れる", "speed": "既存の形を流用できる", "profit": "名入れで単価を上げやすい"},
                "unverified": ["式場向けの需要は未確認"]},
               {"id": b, "fit": 30, "ops": 10, "speed": 5, "profit": 5, "reasons": {"fit": "x"}}]      # fit が上限 25 の外
        r = self.m.start_run(None, "chieko", runner=self.runner(ans, seen), sync=True, force=True)
        self.assertTrue(r["started"])
        run = self.store.one("SELECT * FROM ai_run WHERE id=?", (r["run_id"],))
        self.assertEqual((run["stage"], run["n_ok"], run["n_ng"]), ("完了", 1, 1), "上限の外の案は作らない")
        self.assertIn("数字を作らない", self.m.SYSTEM)
        self.assertIn("フリーカット商品", seen["prompt"], "同社が作れるもの（開発タイプの名前）を渡す")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM fctr_self_score"), 0, "案は点にしない")
        p = self.m.pending_by_theme()[a]
        self.assertEqual(p["axes"]["fit"], 20)
        self.assertEqual(self.m.candidates(), [b], "案の出ているテーマは既定の対象にしない（七五三は案にならなかったので残る）")
        res = self.m.adopt(p["id"], "chieko", {"fit": "22"})
        self.assertTrue(res["changed"])
        ss = self.store.one("SELECT * FROM fctr_self_score WHERE theme_id=?", (a,))
        self.assertEqual((ss["fit"], ss["ops"], ss["speed"], ss["profit"]), (22, 14, 10, 7))
        self.assertIn("直して採用", ss["note"])
        with self.assertRaises(ValueError):
            self.m.adopt(p["id"], "chieko")                                  # 二度は採れない

    def test_missing_axis_must_be_filled_before_adopt(self):
        a = self.ids["結婚式記念"]
        ans = [{"id": a, "fit": 18, "ops": 12, "speed": None, "profit": 6,
                "reasons": {"fit": "a", "ops": "b", "profit": "c"}}]
        self.m.start_run([a], "chieko", runner=self.runner(ans), sync=True, force=True)
        p = self.m.pending_by_theme()[a]
        self.assertNotIn("speed", p["axes"])
        self.assertTrue(any("発売速度" in u for u in p["unverified"]), "根拠の無い軸は未確認に回す")
        with self.assertRaises(ValueError):
            self.m.adopt(p["id"], "chieko")
        self.m.adopt(p["id"], "chieko", {"speed": "9"})
        self.assertEqual(self.store.val("SELECT speed FROM fctr_self_score WHERE theme_id=?", (a,)), 9)

    def test_not_logged_in_makes_no_proposal_and_permissions(self):
        runner = lambda cmd, **kw: _proc({"result": "Not logged in · Please run /login", "is_error": True})  # noqa: E731
        r = self.m.start_run(None, "chieko", runner=runner, sync=True, force=True)
        self.assertEqual(self.store.val("SELECT stage FROM ai_run WHERE id=?", (r["run_id"],)), "失敗")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM fctr_ai_proposal"), 0)
        with self.assertRaises(PermissionError):
            self.m.start_run(None, "nobody")


if __name__ == "__main__":
    unittest.main()
