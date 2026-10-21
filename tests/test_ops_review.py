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


    def test_sheet_diff(self):
        from app import sheetsync
        pid = self.project.create("u", expand=False, internal_name="x")["id"]
        with self.store.tx() as c:
            tid = c.execute("INSERT INTO task (project_id,seq,title,role,status) VALUES (?,?,?,?,?)",
                            (pid, 1, "撮影", "admin", "未着手")).lastrowid
        r = self.store.one("SELECT * FROM task WHERE id=?", (tid,))
        with self.store.tx() as c:
            c.execute("INSERT INTO sheet_sync (kind,local_id,sheet_key,digest,synced_at) VALUES ('task',?,?,?,?)",
                      (str(tid), "x#撮影#1", sheetsync.digest(*sheetsync.vals(r)), self.store.now_s()))
        self.assertEqual(sheetsync.diff_ids("task"), set(), "同期した値のままなら違わない")
        self.task.set_status("task", tid, "完了", "u")
        self.assertEqual(sheetsync.diff_ids("task"), {tid}, "アプリで変えた行はシートと違う")
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-26"
        row = [x for x in self.task.listing(when="all")["rows"] if x["id"] == tid][0]
        self.assertTrue(row["sheet_diff"])
        self.assertEqual([x["id"] for x in sheetsync.summary()["rows"]], [tid])

    def test_routine_quick(self):
        from app import routine
        with self.store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('devdept','chieko',?)", (self.store.now_s(),))
            rid = c.execute("INSERT INTO routine (title,cadence,active,created_at) VALUES ('月次の振り返り','monthly',0,?)",
                            (self.store.now_s(),)).lastrowid
        with self.assertRaises(ValueError):
            routine.set_quick(rid, "chieko", active="1")              # 日が未定
        with self.assertRaises(ValueError):
            routine.set_quick(rid, "chieko", assignee="no_such_user")
        routine.set_quick(rid, "chieko", assignee="chieko")
        with self.store.tx() as c:
            c.execute("UPDATE routine SET day='15' WHERE id=?", (rid,))
        routine.set_quick(rid, "chieko", active="1")
        self.assertEqual(tuple(self.store.one("SELECT active, assignee FROM routine WHERE id=?", (rid,))), (1, "chieko"))
        with self.assertRaises(PermissionError):
            routine.set_quick(rid, "nobody", active="0")

    def test_update_header(self):
        pid = self.project.create("u", expand=False, internal_name=None)["id"]
        r = self.project.update_header(pid, {"internal_name": "推し色アクスタ", "flow_type": "freecut", "_fields": "effort_point"}, "", "u")
        self.assertTrue(r["changed"], "空の欄を埋めるだけなら理由は要らない")
        p = self.store.one("SELECT * FROM project WHERE id=?", (pid,))
        self.assertEqual(p["internal_name"], "推し色アクスタ")
        self.assertEqual(p["effort_point"], self.project.flow("freecut")["effort_point"], "工数ポイントが空なら開発タイプの係数")
        with self.assertRaises(ValueError):
            self.project.update_header(pid, {"internal_name": "別の名前"}, "", "u")   # 入っている値を変えるには理由
        self.project.update_header(pid, {"internal_name": "別の名前"}, "シートの名前に合わせる", "u")
        rev = self.store.val("SELECT detail FROM project_revision WHERE project_id=? AND what='頭の欄' ORDER BY id DESC LIMIT 1", (pid,))
        self.assertIn("推し色アクスタ → 別の名前", rev)
        self.assertIn("理由: シートの名前に合わせる", rev)
        # 画面はフォーム形式で、空の欄は届かない。_fields に並べた欄は空として扱う（分類2を空にする）
        self.project.update_header(pid, {"cat2": "ペア"}, "", "u")
        self.project.update_header(pid, {"_fields": "cat2,internal_name", "internal_name": "別の名前"}, "使わない分類", "u")
        self.assertIsNone(self.store.val("SELECT cat2 FROM project WHERE id=?", (pid,)))
        with self.assertRaises(ValueError):
            self.project.update_header(pid, {"flow_type": "nope"}, "x", "u")
        with self.assertRaises(ValueError):
            self.project.update_header(pid, {"owner": "no_such_user"}, "x", "u")


if __name__ == "__main__":
    unittest.main()
