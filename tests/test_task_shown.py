#!/usr/bin/env python3
"""
タスク画面に出すのは「未着手」と「着手」だけ（2026-11-27 石原さんの修正依頼 ①・FR-336）。
完了・対象外・保留（進捗）は出さない。保留中の案件のタスクも出さない（十文字さん 11/27）。
ボタンの件数も一覧と同じ絞りで数える。外した保留の案件の分は held で数だけ返す。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class TaskShown(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-11-27"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, task
        seed.run()
        self.store, self.task = store, task
        live = project.create("u", expand=False, internal_name="開発中の案件", flow_type="meire", launch_date="2027-01-15")["id"]
        held = project.create("u", expand=False, internal_name="止めた案件", flow_type="meire", launch_date="2026-12-11")["id"]
        with store.tx() as c:
            c.execute("UPDATE project SET stage='保留' WHERE id=?", (held,))
            for pid, seq, title, due, st in [
                (live, 1, "未着手・期限切れ", "2026-11-20", "未着手"),
                (live, 2, "着手・今日", "2026-11-27", "着手"),
                (live, 3, "完了・今日", "2026-11-27", "完了"),
                (live, 4, "対象外・今日", "2026-11-27", "対象外"),
                (live, 5, "保留・期限切れ", "2026-11-20", "保留"),
                (live, 6, "未着手・期限なし", None, "未着手"),
                (held, 1, "止めた案件の未着手", "2026-11-20", "未着手"),
                (held, 2, "止めた案件の着手", None, "着手"),
            ]:
                c.execute("INSERT INTO task (project_id,seq,title,role,due_on,status,created_at) VALUES (?,?,?,?,?,?,?)",
                          (pid, seq, title, "devdept", due, st, "2026-11-01"))
            c.execute("INSERT INTO work_item (kind,title,role,due_on,status,created_at) VALUES ('案件外','案件外・完了','devdept','2026-11-27','完了','2026-11-01')")
            c.execute("INSERT INTO work_item (kind,title,role,due_on,status,created_at) VALUES ('案件外','案件外・未着手','devdept','2026-11-27','未着手','2026-11-01')")

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def titles(self, when, tab="project"):
        return [r["title"] for r in self.task.listing(when, tab)["rows"]]

    def test_only_open_and_started(self):
        self.assertEqual(self.titles("all"), ["未着手・期限切れ", "着手・今日", "未着手・期限なし"])
        self.assertEqual(self.titles("overdue+today"), ["未着手・期限切れ", "着手・今日"])   # 今日の完了・対象外も出さない
        self.assertEqual(self.titles("all", "work"), ["案件外・未着手"])

    def test_counts_match_listing(self):
        d = self.task.listing("all")
        self.assertEqual(d["counts"]["all"], 3 + 1)                 # 案件のタスク3＋案件外1
        self.assertEqual(d["counts"]["overdue+today"], 2 + 1)
        self.assertEqual(d["overdue_total"], 1)                     # 保留（進捗）と止めた案件は数えない
        self.assertEqual(d["no_due_total"], 1)

    def test_held_project_counted_separately(self):
        d = self.task.listing("all")
        self.assertNotIn("止めた案件の未着手", [r["title"] for r in d["rows"]])
        self.assertEqual(d["held"], 2)

    def test_dashboard_bounds_unchanged(self):
        sql, pr = self.task._bounds("overdue", self.task.store.today())
        n = self.store.val(f"SELECT COUNT(*) FROM task WHERE {sql}", pr, 0)
        self.assertEqual(n, 3)                                       # 期間の境界そのものは変えていない


if __name__ == "__main__":
    unittest.main()
