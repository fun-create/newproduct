#!/usr/bin/env python3
"""スキンシール展開判断レポート（Auto GROWTH から引き取り・ADR-140・FR-251）。
見る側: 8桁の日付だけ・パス抜けしない・0本でも落ちない・伏せたものを新発売に数えない・今週の分が無ければ（タイマー有効のときだけ・月曜正午まで待つ）。
作る側: claude は data/aihome・コネクタを読まない・調達先の WebFetch を apex とサブドメインの両方で禁じる・トークンは app/chatwork・サーバーから import しない。"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def _load_tool():
    """作る側を読み込む。**読み込むと名前解決が IPv4 優先に変わる**ので、テストの後で戻す"""
    orig = socket.getaddrinfo
    spec = importlib.util.spec_from_file_location("skin_tool", BASE / "tools" / "skin_report" / "skin_report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    socket.getaddrinfo = orig
    return m


class Viewer(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        os.environ["SKIN_REPORT_OUT"] = str(self.d)
        from app import skin_report
        self.m = skin_report

    def tearDown(self):
        os.environ.pop("SKIN_REPORT_OUT", None)
        for f in self.d.iterdir():
            f.unlink()
        self.d.rmdir()

    def _put(self, d8, meta=None):
        (self.d / f"スキンシール展開判断レポート_{d8}.html").write_text("<html>r" + d8 + "</html>", encoding="utf-8")
        if meta is not None:
            (self.d / f"skin_report_{d8}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def test_empty_is_not_an_error(self):
        with mock.patch.object(self.m, "timer_enabled", return_value=False):
            v = self.m.view()
        self.assertEqual((v["reports"], v["error"], v["cur"]), ([], None, None))

    def test_file_only_8_digits_and_no_traversal(self):
        self._put("20261109")
        self.assertIn(b"r20261109", self.m.file_bytes("20261109"))
        for bad in ("2026110", "../../config/chatwork.env", "20261109/../x", "", "2026-11-09"):
            self.assertIsNone(self.m.file_bytes(bad))
        self.assertIsNone(self.m.file_bytes("20991231"))

    def test_counts_skip_hidden_and_newest_first(self):
        meta = {"reach": [{"name": "VecRas", "ok": True}, {"name": "ARMobileSkin", "ok": False, "reason": "429"}],
                "data": {"series": [{"judgment": "BUY"}, {"judgment": "WAIT"}, {"judgment": "WAIT"}, {"judgment": "UNKNOWN"}],
                         "releases": [{"name": "A", "bucket": "past"}, {"name": "B", "bucket": "future"},
                                      {"name": "C", "bucket": "past"}, {"name": "D", "bucket": "watch"}],
                         "unverified": ["x", " "]},
                "mold_hidden": [{"kind": "releases", "name": "C"}]}
        self._put("20261102")
        self._put("20261109", meta)
        with mock.patch.object(self.m, "timer_enabled", return_value=False):
            v = self.m.view()
        self.assertEqual([r["date"] for r in v["reports"]], ["20261109", "20261102"])
        c = v["cur"]
        self.assertEqual({x["key"]: x["n"] for x in c["counts"]}, {"BUY": 1, "ALT": 0, "REAL": 0, "WAIT": 2, "UNKNOWN": 1})  # 判定なしも落とさない
        self.assertEqual(c["n_new"], 2)                       # C は伏せた・D は発売ではない
        self.assertEqual((c["unverified"], [r["ok"] for r in c["reach"]]), (1, [True, False]))
        with mock.patch.object(self.m, "timer_enabled", return_value=False):
            old = self.m.view("20261102")["cur"]
        self.assertEqual((old["date"], old["counts"], old["has_meta"]), ("20261102", None, False))  # 無い週を0にしない

    def test_missing_this_week_only_when_enabled_and_after_noon(self):
        rows = [{"date": "20261109", "ja": "2026年11月9日（月）"}]
        mon_am, mon_pm = dt.datetime(2026, 11, 16, 9), dt.datetime(2026, 11, 16, 13)
        self.assertIsNone(self.m.missing_this_week(rows, mon_pm, enabled=False))   # 切り替え前は鳴らさない
        self.assertIsNone(self.m.missing_this_week(rows, mon_am, enabled=True))    # 昼までは待つ
        self.assertIn("2026-11-16", self.m.missing_this_week(rows, mon_pm, enabled=True))
        rows.insert(0, {"date": "20261116", "ja": "x"})
        self.assertIsNone(self.m.missing_this_week(rows, dt.datetime(2026, 11, 18, 9), enabled=True))


class Tool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = _load_tool()

    def test_config_is_12_series_6_sites(self):
        series, sites, _watch = self.t.load_config()
        self.assertEqual((len(series), len(sites)), (12, 6))

    def test_claude_cmd_no_connectors_and_deny_both_forms(self):
        _s, sites, _w = self.t.load_config()
        cmd = self.t.claude_cmd("p", sites)
        self.assertIn("--strict-mcp-config", cmd)
        self.assertIn("--no-session-persistence", cmd)
        deny = cmd[cmd.index("--disallowed-tools") + 1].split(",")
        self.assertEqual(len(deny), 2 * len(sites))
        self.assertIn("WebFetch(domain:vecras.com)", deny)
        self.assertIn("WebFetch(domain:*.vecras.com)", deny)

    def test_claude_runs_with_aihome_outside_the_repo(self):
        seen = {}

        def fake(cmd, **kw):
            seen.update(kw)
            return mock.Mock(returncode=0, stdout=json.dumps({"structured_output": {"series": []}}), stderr="")
        _s, sites, _w = self.t.load_config()
        with mock.patch.object(self.t.subprocess, "run", side_effect=fake):
            self.assertEqual(self.t.ask_claude("p", sites, log=lambda *_: None), {"series": []})
        self.assertEqual(seen["env"]["HOME"], self.t.AI_HOME)
        self.assertTrue(self.t.AI_HOME.endswith("data/aihome"))
        self.assertEqual(seen["env"]["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        self.assertFalse(str(seen["cwd"]).startswith(str(BASE)))       # このアプリの CLAUDE.md を読ませない

    def test_token_from_app_chatwork(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("CHATWORK_API_TOKEN=abc\n")
        try:
            with mock.patch.dict(os.environ, {"NEWPRODUCT_CHATWORK_ENV": f.name}):
                self.assertEqual(self.t._chatwork_token()[0], "abc")
            with mock.patch.dict(os.environ, {"NEWPRODUCT_CHATWORK_ENV": f.name + ".none"}):
                with self.assertRaises(self.t.Fail) as e:
                    self.t._chatwork_token()
                self.assertEqual(e.exception.code, 1)
        finally:
            os.unlink(f.name)

    def test_server_does_not_import_the_tool(self):
        for p in [BASE / "server.py", *sorted((BASE / "app").glob("*.py"))]:
            src = p.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"^\s*(from|import)\s+tools", src, re.M), p.name)


if __name__ == "__main__":
    unittest.main()
