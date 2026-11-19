#!/usr/bin/env python3
"""カルテのタスク一覧の並び（FR-333・ADR-158）。ui/app.js の sortTasks を取り出して JavaScript で動かす。
期限あり（早い順）→ 期限なしで目安あり → どちらも無い → 予備時間（998・999）。同じ日は番号の順。番号順にも戻せる。
JavaScript を動かせる環境（Mac の osascript）だけで走る。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _fn() -> str:
    src = (BASE / "ui" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"\n  function sortTasks\(tasks, order\) \{.*?\n  \}\n", src, re.S)
    assert m, "sortTasks が見つかりません"
    return m.group(0)


TASKS = [
    {"id": 1, "seq": 1, "due_on": None, "plan_due": None, "kind": "実作業"},
    {"id": 2, "seq": 2, "due_on": "2026-12-04", "plan_due": None, "kind": "実作業"},
    {"id": 3, "seq": 3, "due_on": None, "plan_due": "2026-11-30", "kind": "実作業"},
    {"id": 4, "seq": 4, "due_on": "2026-11-27", "plan_due": "2026-12-20", "kind": "実作業"},
    {"id": 5, "seq": 998, "due_on": "2026-11-01", "plan_due": None, "kind": "予備"},
    {"id": 6, "seq": 6, "due_on": "2026-12-04", "plan_due": None, "kind": "実作業"},
    {"id": 7, "seq": 999, "due_on": None, "plan_due": None, "kind": "予備"},
    {"id": 8, "seq": 8, "due_on": None, "plan_due": "2026-11-28", "kind": "実作業"},
]


@unittest.skipUnless(shutil.which("osascript"), "JavaScript を動かせる環境（Mac）だけ")
class TaskOrder(unittest.TestCase):
    def run_js(self, order: str) -> list[int]:
        js = _fn() + f"\nJSON.stringify(sortTasks({json.dumps(TASKS)}, {json.dumps(order)}).map(function (t) {{ return t.id; }}));"
        out = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip())

    def test_due_order(self):
        self.assertEqual(self.run_js("due"), [4, 2, 6, 8, 3, 1, 5, 7])

    def test_seq_order(self):
        self.assertEqual(self.run_js("seq"), [1, 2, 3, 4, 6, 8, 5, 7])

    def test_does_not_reorder_the_original(self):
        js = _fn() + f"\nvar a = {json.dumps(TASKS)}; sortTasks(a, 'due'); JSON.stringify(a.map(function (t) {{ return t.id; }}));"
        out = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(out.stdout.strip()), [t["id"] for t in TASKS])


if __name__ == "__main__":
    unittest.main()
