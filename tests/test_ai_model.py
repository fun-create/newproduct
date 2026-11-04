"""AI のモデル（ADR-127）: どの機能も Opus 5.5 を正式名で頼み、答えたモデルを正しく残す。"""
import json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import ai_cli  # noqa: E402


def _runner(seen, usage):
    def run(cmd, **kw):
        seen["cmd"] = cmd
        out = json.dumps({"type": "result", "result": "ok", "is_error": False,
                          "total_cost_usd": 0.01, "modelUsage": usage})
        return types.SimpleNamespace(stdout=out, stderr="", returncode=0)
    return run


class ModelTest(unittest.TestCase):
    def test_asks_opus_5_5_by_full_name(self):
        seen = {}
        ai_cli.ask("p", "s", runner=_runner(seen, {"claude-opus-5-5": {}}))
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5-5")

    def test_web_research_also_asks_opus(self):
        seen = {}
        ai_cli.ask("p", "s", runner=_runner(seen, {"claude-opus-5-5": {}}), web=True)
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-opus-5-5")

    def test_records_opus_not_the_helper_haiku(self):
        seen = {}
        r = ai_cli.ask("p", "s", runner=_runner(seen, {
            "claude-haiku-5-5": {"canonicalModel": "claude-haiku-5-5"},
            "claude-opus-5-5": {"canonicalModel": "claude-opus-5-5"}}))
        self.assertEqual(r["model"], "claude-opus-5-5")

    def test_no_feature_overrides_the_model(self):
        """機能ごとに別のモデルを渡していない（渡すと「全て Opus 5.5」から外れる）。"""
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
        for f in sorted(os.listdir(base)):
            if not f.endswith(".py") or f == "ai_cli.py":
                continue
            for n, line in enumerate(open(os.path.join(base, f), encoding="utf-8"), 1):
                if "ai_cli.ask(" in line:
                    self.assertNotIn("model=", line, f"{f}:{n}")


if __name__ == "__main__":
    unittest.main()
