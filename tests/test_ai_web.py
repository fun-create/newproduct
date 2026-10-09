#!/usr/bin/env python3
"""
AI のウェブ調査（ADR-088）の検査。**claude は呼ばない**（stream-json の返事を差し替える）。

  - 渡す道具は検索とページの読み取りだけ。検索した言葉・開いた URL を全部残す
  - AI が見ていない URL を出典にした行は、競合の表に入れられない
  - 選んだ行だけを競合の表へ。確認日は調べた日、メモに「AI調べ」
  - 需要の調べは、採用したときだけ採点の案に渡る（見ていない出典の要点は渡さない）
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


def _stream(answer: dict, opened=(), searched=(("名入れ マグ", ["https://item.rakuten.co.jp/a/1/"]),)):
    lines = [{"type": "system", "subtype": "init"}]
    for q, urls in searched:
        lines.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch", "input": {"query": q}}]}})
        lines.append({"type": "user", "message": {"content": [{"type": "tool_result", "content":
                      "Links: " + json.dumps([{"title": "t", "url": u} for u in urls])}]}})
    for u in opened:
        lines.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebFetch", "input": {"url": u, "prompt": "価格"}}]}})
        lines.append({"type": "user", "message": {"content": [{"type": "tool_result", "content": "価格 2,980円"}]}})
    lines.append({"type": "result", "is_error": False, "result": json.dumps(answer, ensure_ascii=False),
                  "total_cost_usd": 0.12, "modelUsage": {"claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"}}})
    return types.SimpleNamespace(stdout="\n".join(json.dumps(x, ensure_ascii=False) for x in lines), stderr="", returncode=0)


class AiWeb(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        old_today = os.environ.pop("NEWPRODUCT_TODAY", None)     # 調べた日（実時刻）と「今日」をそろえる
        if old_today is not None:
            self.addCleanup(os.environ.__setitem__, "NEWPRODUCT_TODAY", old_today)
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, ai_web, idea
        seed.run()
        self.m, self.store, self.idea = ai_web, store, idea
        self.pid = project.create("u", expand=False, internal_name="推し色マグ", flow_type="meire",
                                  launch_date="2026-12-01")["id"]
        project.save_section(self.pid, "C.concept", "推しの色で毎日を過ごす" * 100, "u")

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + suffix)
            except FileNotFoundError:
                pass

    ROWS = {"rows": [
        {"shop": "A店", "item": "名入れマグ", "channel": "楽天", "url": "https://item.rakuten.co.jp/a/1/",
         "price_yen": "2,980円", "spec": "330ml", "review_count": 120, "review_avg": 4.5},
        {"shop": "B店", "item": "作られた URL のマグ", "channel": "その他", "url": "https://example.com/made-up",
         "price_yen": 1000},
        {"shop": "", "item": "店名なし", "url": "https://x.example/"}]}

    def test_competitor_candidates_and_trace(self):
        seen = {}

        def runner(cmd, **kw):
            seen["cmd"] = cmd
            return _stream(self.ROWS, opened=["https://item.rakuten.co.jp/a/1/"])
        r = self.m.start_run("competitor", self.pid, "u", runner=runner, sync=True, force=True)
        self.assertTrue(r["started"])
        c = seen["cmd"]
        self.assertEqual(c[c.index("--tools") + 1], "WebSearch,WebFetch", "渡す道具は検索と読み取りだけ")
        self.assertIn("--strict-mcp-config", c)
        prompt = c[c.index("-p") + 1]
        self.assertLess(prompt.count("推しの色で毎日を過ごす"), 60, "カルテの文は短くして渡す")
        w = self.m.of("project", self.pid, "competitor")[0]
        rows = w["body"]["rows"]
        self.assertEqual(len(rows), 2, "店名の無い行は捨てる")
        self.assertEqual((rows[0]["price_yen"], rows[0]["seen"]), (2980, "開いた"))
        self.assertEqual(rows[1]["seen"], "見ていない")
        self.assertEqual([x["tool"] for x in w["trace"]["calls"]], ["検索", "ページを開く"])
        with self.assertRaises(ValueError):
            self.m.adopt(w["id"], "boss", [1])                  # 見ていない URL は表に入れない
        self.m.adopt(w["id"], "boss", [0])
        t = self.store.one("SELECT * FROM competitor_item WHERE project_id=?", (self.pid,))
        self.assertEqual((t["shop"], t["price_yen"], t["checked_on"]), ("A店", 2980, w["created_at"][:10]))
        self.assertIn("AI調べ", t["note"])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM competitor_item WHERE project_id=?", (self.pid,)), 1)

    def test_demand_goes_to_scoring_only_when_adopted(self):
        from app import ai_score
        iid = self.idea.create("u", title="推しカラーの名札", summary="ライブで付ける", target_scene="推し活",
                               origin="internal", theme_id="oshikatsu")["id"]
        ans = {"summary": "需要はある", "findings": [
            {"point": "名札の話題が多い", "url": "https://item.rakuten.co.jp/a/1/"},
            {"point": "作られた出典", "url": "https://example.com/made-up"}]}
        self.m.start_run("demand", iid, "u", runner=lambda cmd, **kw: _stream(ans), sync=True, force=True)
        w = self.m.of("idea", iid, "demand")[0]
        idea = dict(self.store.one("SELECT * FROM idea WHERE id=?", (iid,)))
        self.assertNotIn("ウェブで調べたこと", ai_score.build_prompt([idea]), "採用するまで渡さない")
        self.m.adopt(w["id"], "boss")
        p = ai_score.build_prompt([idea])
        self.assertIn("名札の話題が多い", p)
        self.assertNotIn("作られた出典", p, "AI が見ていない出典の要点は渡さない")

    def test_not_logged_in(self):
        from app import ai_score
        runner = lambda cmd, **kw: types.SimpleNamespace(  # noqa: E731
            stdout=json.dumps({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}),
            stderr="", returncode=0)
        r = self.m.start_run("competitor", self.pid, "u", runner=runner, sync=True, force=True)
        self.assertEqual(ai_score.run_view(r["run_id"])["stage"], "失敗")
        self.assertEqual(self.m.of("project", self.pid, "competitor"), [])

    def test_roles(self):
        with self.assertRaises(PermissionError):
            self.m.start_run("competitor", self.pid, "誰でもない人")

    def test_no_network_imports(self):
        src = (BASE / "app" / "ai_web.py").read_text(encoding="utf-8")
        for bad in ("import urllib", "import http", "import socket", "from urllib", "from http"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
