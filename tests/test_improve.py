#!/usr/bin/env python3
"""発売後の改善要望（ADR-132）: 受付・直す・優先度の並び・タスク化（案件外の仕事）・タスクとの同期。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Improve(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, improve
        seed.run()
        self.store, self.m = store, improve
        self.pid = project.create("u", expand=False, internal_name="等身大パネル", flow_type="freecut",
                                  launch_date="2026-05-26")["id"]

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_create_list_order_and_edit(self):
        a = self.m.create({"title": "180cm以上つくか", "project_id": self.pid}, "u")["id"]
        b = self.m.create({"title": "規定サイズを画像入稿にも", "priority": "高", "source": "外部署から"}, "u")["id"]
        c = self.m.create({"title": "台座を分ける", "priority": "中", "due_on": "2026-12-01"}, "u")["id"]
        rows = self.m.listing()["rows"]
        self.assertEqual([r["id"] for r in rows], [b, c, a])               # 高 → 中 → 未設定
        self.assertEqual(rows[2]["status"], "受付")
        self.assertEqual(rows[2]["received_on"], self.store.today_s())
        self.m.update(a, {"priority": "低", "detail": "画サイズ拡大に切り替える前に", "status": "検討中"}, "u")
        r = self.store.one("SELECT * FROM improve_request WHERE id=?", (a,))
        self.assertEqual((r["priority"], r["status"], r["detail"]), ("低", "検討中", "画サイズ拡大に切り替える前に"))
        self.assertEqual(self.m.update(a, {"priority": "低"}, "u")["changed"], 0)
        self.m.update(a, {"status": "見送り"}, "u")                           # 消さずに見送り
        self.assertNotIn(a, [x["id"] for x in self.m.listing()["rows"]])
        self.assertIn(a, [x["id"] for x in self.m.listing("見送り")["rows"]])
        for bad in ({"title": ""}, {"priority": "最優先"}, {"status": "保留"}, {"due_on": "12/1"}, {"role": "nope"}):
            with self.assertRaises(ValueError):
                self.m.update(b, bad, "u")

    def test_to_task_once_and_sync(self):
        rid = self.m.create({"title": "フレークシールの最小サイズ", "priority": "中"}, "u")["id"]
        with self.assertRaises(ValueError):
            self.m.to_task(rid, "u")                                         # 担当が無いとタスクにしない
        self.m.update(rid, {"role": "admin", "assignee": "chieko", "due_on": "2026-12-10"}, "u")
        t1 = self.m.to_task(rid, "u")
        t2 = self.m.to_task(rid, "u")
        self.assertTrue(t1["created"])
        self.assertEqual((t2["created"], t2["id"]), (False, t1["id"]))
        self.assertEqual(t1["calendar"], "カレンダーに出ます")
        w = self.store.one("SELECT * FROM work_item WHERE id=?", (t1["id"],))
        self.assertEqual((w["kind"], w["category"], w["role"], w["assignee"], w["due_on"]),
                         ("案件外", "改善要望", "admin", "chieko", "2026-12-10"))
        self.assertEqual(self.store.val("SELECT status FROM improve_request WHERE id=?", (rid,)), "対応中")
        # 要望を直すと、まだ終わっていないタスクにも写る
        self.m.update(rid, {"due_on": "2026-12-20", "title": "フレークシールの最小サイズを決める"}, "u")
        w = self.store.one("SELECT * FROM work_item WHERE id=?", (t1["id"],))
        self.assertEqual((w["due_on"], w["title"]), ("2026-12-20", "改善要望: フレークシールの最小サイズを決める"))
        # タスクを完了にすると要望も完了
        self.store.ex("UPDATE work_item SET status='完了' WHERE id=?", (t1["id"],))
        self.m.listing()
        self.assertEqual(self.store.val("SELECT status FROM improve_request WHERE id=?", (rid,)), "完了")

    def test_dismiss_closes_open_task(self):
        rid = self.m.create({"title": "パズルフレームの代替", "role": "admin"}, "u")["id"]
        wid = self.m.to_task(rid, "u")["id"]
        self.m.update(rid, {"status": "見送り"}, "u")
        self.assertEqual(self.store.val("SELECT status FROM work_item WHERE id=?", (wid,)), "対象外")
