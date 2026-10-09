#!/usr/bin/env python3
"""
商品名の案出し（FR-149・ADR-082）の検査。**claude は呼ばない**（返事の形を差し替えて渡す）。

  - AI は案を出すだけ。カルテには書かない。選んだ名前だけを F.name の末尾に足し、書いてある行は消さない
  - 根拠（why）の無い名前は案にしない。商標の登録状況は必ず「未確認」に入る
  - 渡すのはカルテの文面だけ。渡した項目を残す
  - 未ログイン（終了コード0でも is_error）なら、その回は失敗で案を作らない
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


class AiName(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, ai_name
        seed.run()
        self.m, self.store, self.project = ai_name, store, project
        self.pid = project.create("u", expand=False, internal_name="推し色マグ", flow_type="meire",
                                  launch_date="2026-12-01")["id"]
        project.save_section(self.pid, "F.name", "推し色マグカップ", "u")
        project.save_section(self.pid, "C.concept", "推しの色で毎日を過ごす", "u")

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + suffix)
            except FileNotFoundError:
                pass

    def _runner(self, cands, unverified=None, seen=None):
        def run(cmd, **kw):
            if seen is not None:
                seen["prompt"] = cmd[cmd.index("-p") + 1]
            ans = {"candidates": cands, "unverified": unverified or []}
            return _proc({"result": json.dumps(ans, ensure_ascii=False), "is_error": False,
                          "total_cost_usd": 0.02, "modelUsage": {"claude-sonnet-5-5": {}}})
        return run

    def _fname(self):
        r = self.store.one("SELECT body FROM project_section WHERE project_id=? AND section_key='F.name'", (self.pid,))
        return r["body"]

    def test_proposal_does_not_touch_the_karte(self):
        seen = {}
        cands = [{"name": "推し色マグ いろどり", "why": "コンセプトの色を前に出した"},
                 {"name": "根拠なしの名前", "why": ""}]
        r = self.m.start_run(self.pid, "u", runner=self._runner(cands, seen=seen), sync=True, force=True)
        self.assertTrue(r["started"])
        self.assertEqual(self._fname(), "推し色マグカップ", "案を出しただけでカルテを書き換えない")
        p = self.m.proposals_of(self.pid)[0]
        self.assertEqual([c["name"] for c in p["candidates"]], ["推し色マグ いろどり"], "根拠の無い名前は案にしない")
        self.assertEqual(p["unverified"][0], self.m.TRADEMARK, "商標は必ず未確認")
        self.assertEqual(p["model"], "claude-sonnet-5-5")
        self.assertIn("コンセプト", p["inputs"]["項目"])
        self.assertIn("推しの色で毎日を過ごす", seen["prompt"])

    def test_adopt_appends_only_picked_names(self):
        cands = [{"name": "推し色マグ いろどり", "why": "a"}, {"name": "推しカラーマグ", "why": "b"},
                 {"name": "推し色マグカップ", "why": "既にある名前"}]
        self.m.start_run(self.pid, "u", runner=self._runner(cands), sync=True, force=True)
        pid = self.m.proposals_of(self.pid)[0]["id"]
        r = self.m.adopt(pid, "u", [1, 2])
        self.assertEqual(r["added"], ["推しカラーマグ"])
        self.assertEqual(r["already"], ["推し色マグカップ"])
        self.assertEqual(self._fname(), "推し色マグカップ\n推しカラーマグ", "書いてある行は消さず、選んだものだけ足す")
        self.assertEqual(self.m.proposals_of(self.pid)[0]["state"], "採用")
        with self.assertRaises(ValueError):
            self.m.adopt(pid, "u", [0])

    def test_reject_leaves_the_karte(self):
        self.m.start_run(self.pid, "u", runner=self._runner([{"name": "x名", "why": "y"}]), sync=True, force=True)
        pid = self.m.proposals_of(self.pid)[0]["id"]
        self.m.reject(pid, "u", "方向が違う")
        self.assertEqual(self.m.proposals_of(self.pid)[0]["state"], "見送り")
        self.assertEqual(self._fname(), "推し色マグカップ")

    def test_not_logged_in_fails_the_run(self):
        from app import ai_score
        runner = lambda cmd, **kw: _proc({"result": "Not logged in · Please run /login", "is_error": True})  # noqa: E731
        r = self.m.start_run(self.pid, "u", runner=runner, sync=True, force=True)
        run = ai_score.run_view(r["run_id"])
        self.assertEqual((run["stage"], run["error_kind"], run["kind"]), ("失敗", "env", "name"))
        self.assertEqual(self.m.proposals_of(self.pid), [])

    def test_only_business_roles_can_start(self):
        with self.assertRaises(PermissionError):
            self.m.start_run(self.pid, "誰でもない人")

    def test_no_network_imports(self):
        src = (BASE / "app" / "ai_name.py").read_text(encoding="utf-8")
        for bad in ("import urllib", "import http", "import socket", "from urllib", "from http"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
