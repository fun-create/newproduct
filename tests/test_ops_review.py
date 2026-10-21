#!/usr/bin/env python3
"""
入力と運用の流れの点検（docs/ui-review-2026-10-20.md・ADR-119）で直したところの検査。

見ているのは:
  - 切り替えの段階: 11/8 まではシートが正（parallel）、11/9 からアプリ（app）
  - 案件を起こすときの発売予定日は YYYY-MM-DD だけ（`2026/12/1` を通さない）
  - 案件外の仕事は、担当かロールのどちらかが要る。知らない担当は入れない
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class OpsReview(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, task, cutover
        seed.run()
        self.store, self.project, self.task, self.cutover = store, project, task, cutover
        import auth
        self._users = auth.users
        auth.users = lambda: [{"user_id": "chieko"}]

    def tearDown(self):
        import auth
        auth.users = self._users
        self.store.close()
        os.environ.pop("NEWPRODUCT_TODAY", None)
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_phase(self):
        os.environ["NEWPRODUCT_TODAY"] = "2026-11-08"
        self.assertEqual(self.cutover.phase(), "parallel")
        self.assertEqual(self.cutover.last_sheet_day(), "2026-11-08")
        os.environ["NEWPRODUCT_TODAY"] = "2026-11-09"
        self.assertEqual(self.cutover.phase(), "app")

    def test_launch_date_is_checked_on_create(self):
        with self.assertRaises(ValueError):
            self.project.create("u", expand=False, internal_name="x", launch_date="2026/12/1")
        r = self.project.create("u", expand=False, internal_name="x", launch_date="2026-12-01")
        self.assertEqual(self.store.val("SELECT launch_date FROM project WHERE id=?", (r["id"],)), "2026-12-01")

    def test_work_item_needs_someone(self):
        with self.assertRaises(ValueError):
            self.task.create_work_item({"kind": "案件外", "title": "FBA納品"}, "u")
        with self.assertRaises(ValueError):
            self.task.create_work_item({"kind": "案件外", "title": "FBA納品", "assignee": "no_such_user"}, "u")
        w = self.task.create_work_item({"kind": "案件外", "title": "FBA納品", "assignee": "chieko"}, "u")
        self.assertEqual(self.store.val("SELECT assignee FROM work_item WHERE id=?", (w["id"],)), "chieko")


if __name__ == "__main__":
    unittest.main()
