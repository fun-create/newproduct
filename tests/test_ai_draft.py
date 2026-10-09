#!/usr/bin/env python3
"""
LP依頼書・競合調査の下書き（FR-149・ADR-087）の検査。**claude は呼ばない。**

  - 案を出しただけではカルテを書き換えない。人が直した文面を、足す先の欄の末尾に足す（書いてある文は消さない）
  - 競合調査には、人が調べた競合の表を渡す。AI には店名・価格・URL を作らせない（指示に書いてある）
  - 未ログイン（終了コード0でも is_error）なら失敗で、案を作らない
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


class AiDraft(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, ai_draft
        seed.run()
        self.m, self.store = ai_draft, store
        self.pid = project.create("u", expand=False, internal_name="推し色マグ", flow_type="meire",
                                  launch_date="2026-12-01")["id"]
        project.save_section(self.pid, "F.lp", "既にある依頼メモ", "u")
        project.save_section(self.pid, "C.concept", "推しの色で毎日を過ごす", "u")
        with store.tx() as c:
            c.execute("INSERT INTO competitor_item (project_id,shop,item,channel,url,checked_on,price_yen,created_at,created_by) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (self.pid, "A店", "名入れマグ", "楽天", "https://example.com/a", "2026-10-01", 2980, store.now_s(), "u"))

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + suffix)
            except FileNotFoundError:
                pass

    def _runner(self, sections, seen=None):
        def run(cmd, **kw):
            if seen is not None:
                seen["prompt"] = cmd[cmd.index("-p") + 1]
            ans = {"sections": sections, "unverified": ["価格帯は未確認"]}
            return _proc({"result": json.dumps(ans, ensure_ascii=False), "is_error": False,
                          "total_cost_usd": 0.03, "modelUsage": {"claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"}}})
        return run

    def _sec(self, key):
        r = self.store.one("SELECT body FROM project_section WHERE project_id=? AND section_key=?", (self.pid, key))
        return r["body"] if r else None

    def test_lp_draft_then_adopt_edited(self):
        seen = {}
        secs = [{"title": "キャッチコピーの案", "body": "推しの色で、毎日を。"}, {"title": "空", "body": ""}]
        r = self.m.start_run(self.pid, "lp", "u", runner=self._runner(secs, seen), sync=True, force=True)
        self.assertTrue(r["started"])
        self.assertEqual(self._sec("F.lp"), "既にある依頼メモ", "案を出しただけで書き換えない")
        p = self.m.proposals_of(self.pid)["lp"][0]
        self.assertEqual([s["title"] for s in p["sections"]], ["キャッチコピーの案"], "中身の無い節は捨てる")
        self.assertIn("推しの色で毎日を過ごす", seen["prompt"])
        self.assertNotIn("販売価格（試算）", seen["prompt"], "試算原価が無ければ価格は渡さない（作らせない）")
        out = self.m.adopt(p["id"], "boss", "【キャッチコピーの案】\n推しの色と、毎日を。")
        self.assertTrue(out["edited"])
        body = self._sec("F.lp")
        self.assertTrue(body.startswith("既にある依頼メモ\n\n── AI案（claude-sonnet-5・"), body)
        self.assertTrue(body.endswith("推しの色と、毎日を。"))
        with self.assertRaises(ValueError):
            self.m.adopt(p["id"], "boss", "もう一度")

    def test_competitor_gets_the_human_table_and_no_invented_shops(self):
        seen = {}
        self.m.start_run(self.pid, "competitor", "u", sync=True, force=True,
                         runner=self._runner([{"title": "比べる観点", "body": "名入れの文字数"}], seen))
        self.assertIn("A店", seen["prompt"], "人が調べた表を渡す")
        self.assertIn("店名・商品名・価格・URL・レビュー件数を作らない", seen["prompt"])
        p = self.m.proposals_of(self.pid)["competitor"][0]
        self.m.reject(p["id"], "u", "観点が足りない")
        self.assertEqual(self.m.proposals_of(self.pid)["competitor"][0]["state"], "見送り")
        self.assertIsNone(self._sec("C.competitor"))

    def test_diff_adds_lines_only_so_gate_count_stays_honest(self):
        """差別化は1行1点。**見出しを足さない**（ゲートの「3点以上」は行の数で数える）。重複と記号は落とす。"""
        from app import project
        project.save_section(self.pid, "C.diff", "色を選べる", "u")
        self.m.start_run(self.pid, "diff", "u", sync=True, force=True,
                         runner=self._runner([{"title": "差別化の案", "body": "・名入れ無料\n1. 色を選べる\n即日発送"}]))
        p = self.m.proposals_of(self.pid)["diff"][0]
        out = self.m.adopt(p["id"], "boss", p["text"])
        self.assertEqual(self._sec("C.diff"), "色を選べる\n名入れ無料\n即日発送")
        self.assertIn("2 行足した", out["note"])

    def test_share_is_copy_only(self):
        self.m.start_run(self.pid, "share", "u", sync=True, force=True,
                         runner=self._runner([{"title": "ひとことで", "body": "推しの色で毎日を"}]))
        p = self.m.proposals_of(self.pid)["share"][0]
        before = self.store.val("SELECT COUNT(*) FROM project_section WHERE project_id=?", (self.pid,))
        out = self.m.adopt(p["id"], "boss", p["text"])
        self.assertIsNone(out["target"])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM project_section WHERE project_id=?", (self.pid,)), before,
                         "共有文はカルテに書かない")
        self.assertEqual(self.m.proposals_of(self.pid)["share"][0]["state"], "採用")

    def test_quality_and_concept_append_with_heading(self):
        for kind, key in (("quality", "E.quality"), ("concept", "C.concept")):
            self.m.start_run(self.pid, kind, "u", sync=True, force=True,
                             runner=self._runner([{"title": "検品で見る点", "body": "印刷のずれ"}]))
            p = self.m.proposals_of(self.pid)[kind][0]
            self.m.adopt(p["id"], "boss", p["text"])
            self.assertIn("── AI案（claude-sonnet-5・", self._sec(key))

    def test_not_logged_in_fails(self):
        from app import ai_score
        runner = lambda cmd, **kw: _proc({"result": "Not logged in · Please run /login", "is_error": True})  # noqa: E731
        r = self.m.start_run(self.pid, "lp", "u", runner=runner, sync=True, force=True)
        self.assertEqual(ai_score.run_view(r["run_id"])["stage"], "失敗")
        self.assertEqual(self.m.proposals_of(self.pid)["lp"], [])

    def test_unknown_kind_and_roles(self):
        with self.assertRaises(ValueError):
            self.m.start_run(self.pid, "price", "u")
        with self.assertRaises(PermissionError):
            self.m.start_run(self.pid, "lp", "誰でもない人")

    def test_no_network_imports(self):
        src = (BASE / "app" / "ai_draft.py").read_text(encoding="utf-8")
        for bad in ("import urllib", "import http", "import socket", "from urllib", "from http"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
