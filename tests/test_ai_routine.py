#!/usr/bin/env python3
"""
繰り返しの仕事の自動化の AI 案（FR-201・ADR-116）の検査。**claude は呼ばない。**

見ているのは:
  - 案は依頼ではない。「自動化依頼にする」で初めて自動化依頼の下書きができる（送らない）。二度はできない
  - 決めた語（できる／一部できる／難しい・大／中／小）の外は案にしない。how が空なら案にしない
  - 時間（分）を作らせない指示がある。渡すのは仕事の情報だけ
  - 同じ作業名の依頼があれば名前を変えて作る。案のある仕事は既定の対象にしない
  - 未ログインなら失敗で、案を作らない。権限の無い人は走らせられない
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


def _proc(payload):
    return types.SimpleNamespace(stdout=json.dumps(payload, ensure_ascii=False), stderr="", returncode=0)


class AiRoutine(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-19"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, routine, ai_routine
        seed.run()
        self.store, self.m = store, ai_routine
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('devdept','chieko',?)", (store.now_s(),))
        self.r1 = routine.save({"title": "新商品の前週売上確認", "cadence": "weekly", "weekdays": "0", "reason": "PDCA", "hours": "0.5", "active": "0"}, "chieko")["id"]
        self.r2 = routine.save({"title": "全店の注文メールチェック", "cadence": "daily", "timing": "出勤時", "active": "0"}, "chieko")["id"]

    def tearDown(self):
        self.store.close()
        os.environ.pop("NEWPRODUCT_TODAY", None)
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

    def test_proposal_then_request_draft(self):
        seen = {}
        ans = [{"id": self.r1, "verdict": "できる", "how": "売上の集計を毎週月曜に自動で出し、ChatWork に流す",
                "keep_human": "数字を見て次の手を決める", "impact": "中", "reason": "毎週の集計が要らなくなる", "unverified": ["売上の出どころ"]},
               {"id": self.r2, "verdict": "たぶん", "how": "x", "impact": "大"}]                        # 決めた語の外
        r = self.m.start_run(None, "chieko", runner=self.runner(ans, seen), sync=True, force=True)
        run = self.store.one("SELECT * FROM ai_run WHERE id=?", (r["run_id"],))
        self.assertEqual((run["stage"], run["n_ok"], run["n_ng"]), ("完了", 1, 1))
        self.assertIn("数字を作らない", self.m.SYSTEM)
        self.assertIn("新商品の前週売上確認", seen["prompt"])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM automation_request"), 0, "案は依頼ではない")
        self.assertEqual(self.m.candidates(), [self.r2], "案のある仕事は既定の対象にしない")
        p = self.m.latest_by_routine()[self.r1]
        with self.store.tx() as c:                                   # 同じ作業名の依頼が既にある
            c.execute("INSERT INTO automation_request (id,title,stage,created_at,created_by,updated_at,updated_by) "
                      "VALUES ('x1','新商品の前週売上確認','起票',?,?,?,?)", (self.store.now_s(), "u", self.store.now_s(), "u"))
        res = self.m.to_request(p["id"], "chieko")
        self.assertEqual(res["title"], "新商品の前週売上確認（繰り返しの仕事から）")
        req = self.store.one("SELECT * FROM automation_request WHERE id=?", (res["request_id"],))
        self.assertIn("AI の案", req["raw_request"])
        self.assertIn(f"#{self.r1}", req["note"])
        self.assertEqual(self.m.latest_by_routine()[self.r1]["state"], "依頼にした")
        with self.assertRaises(ValueError):
            self.m.to_request(p["id"], "chieko")

    def test_not_logged_in_and_permissions(self):
        runner = lambda cmd, **kw: _proc({"result": "Not logged in · Please run /login", "is_error": True})  # noqa: E731
        r = self.m.start_run(None, "chieko", runner=runner, sync=True, force=True)
        self.assertEqual(self.store.val("SELECT stage FROM ai_run WHERE id=?", (r["run_id"],)), "失敗")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM routine_ai_proposal"), 0)
        with self.assertRaises(PermissionError):
            self.m.start_run(None, "nobody")


if __name__ == "__main__":
    unittest.main()
