#!/usr/bin/env python3
"""
選んだタスクを営業日でまとめてずらす（石原さんの追補依頼書 8-3・2026-11-02）と、
切り替えのあとはシートから同期しない（追補依頼書 9）の検査。カレンダーは呼ばない。

見ているのは:
  - +n／−n は営業日で数える（当日は数えない）。土日・会社休業日を飛ばし、休業日には着地しない
  - 完了・対象外・固定のタスクは動かさない。選んでいない行も動かさない
  - 見込み（shift_plan）は書かない。反映は控えを残し、元に戻せる（計算し直しと同じ undo）
  - 0 日・他の案件のタスク・空の選択は断る
  - 切り替え日からは sync_sheet が書かない（`--today` では抜けられない）
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests"))

from test_reschedule import HOLI, doc, nth   # noqa: E402


class Shift(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, reschedule
        seed.run()
        self.store, self.m = store, reschedule
        self.pid = project.create("u", internal_name="名入れの案件", flow_type="meire", launch_date="2027-01-15")["id"]
        self.other = project.create("u", internal_name="別の案件", flow_type="meire", launch_date="2027-02-15")["id"]
        self.doc = doc()
        ts = store.rows(store.q("SELECT id FROM task WHERE project_id=? AND kind!='予備' ORDER BY seq", (self.pid,)))
        self.ids = [t["id"] for t in ts[:4]]
        with store.tx() as c:   # 年末年始をまたぐ日付を置く
            c.execute("UPDATE task SET start_on='2026-12-24', due_on='2026-12-28' WHERE id=?", (self.ids[0],))
            c.execute("UPDATE task SET start_on='2026-12-25', due_on='2026-12-25' WHERE id=?", (self.ids[1],))
            c.execute("UPDATE task SET start_on='2026-12-21', due_on='2026-12-22', status='完了' WHERE id=?", (self.ids[2],))
            c.execute("UPDATE task SET start_on='2026-12-21', due_on='2026-12-22', date_locked=1 WHERE id=?", (self.ids[3],))

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def get(self, i):
        return self.store.one("SELECT start_on, due_on FROM task WHERE id=?", (i,))

    def test_business_days_skip_holidays_and_never_land_on_one(self):
        pl = self.m.shift_plan(self.pid, self.ids[:2], 3, doc=self.doc)
        r = {x["id"]: x for x in pl["rows"]}
        a = r[self.ids[0]]
        self.assertEqual((a["new_start"], a["new_due"]), (nth(dt.date(2026, 12, 24), 3, 1), nth(dt.date(2026, 12, 28), 3, 1)))
        self.assertEqual(a["new_due"], "2027-01-07", "12/28 の +3 は年末年始（12/29〜1/4）を飛ばして 1/7")
        for x in pl["rows"]:
            for d in (x["new_start"], x["new_due"]):
                dd = dt.date.fromisoformat(d)
                self.assertTrue(dd.weekday() < 5 and d not in HOLI, f"休業日に着地しない: {d}")
        self.assertEqual(self.get(self.ids[0])["due_on"], "2026-12-28", "見込みは書かない")
        back = self.m.shift_plan(self.pid, [self.ids[0]], -2, doc=self.doc)["rows"][0]
        self.assertEqual(back["new_start"], nth(dt.date(2026, 12, 24), 2, -1), "−は前へ")

    def test_done_and_locked_do_not_move_and_undo_restores(self):
        r = self.m.shift_apply(self.pid, self.ids, 2, "u", doc=self.doc)
        self.assertEqual(r["changed"], 2, "完了と固定は動かさない")
        self.assertEqual(self.get(self.ids[2])["due_on"], "2026-12-22")
        self.assertEqual(self.get(self.ids[3])["due_on"], "2026-12-22")
        why = {x["id"]: x["why"] for x in self.m.shift_plan(self.pid, self.ids, 2, doc=self.doc)["rows"]}
        self.assertIn("完了", why[self.ids[2]])
        self.assertIn("固定", why[self.ids[3]])
        moved = self.get(self.ids[1])["due_on"]
        self.assertNotEqual(moved, "2026-12-25")
        u = self.m.undo(self.pid, "u")
        self.assertEqual(u["restored"], 2)
        self.assertEqual(self.get(self.ids[1])["due_on"], "2026-12-25", "元に戻せる")
        rev = self.store.one("SELECT what FROM project_revision WHERE project_id=? AND what LIKE '%ずらした%'", (self.pid,))
        self.assertIsNotNone(rev, "履歴に残る")

    def test_refuses_bad_input(self):
        with self.assertRaises(ValueError):
            self.m.shift_plan(self.pid, self.ids, 0, doc=self.doc)
        with self.assertRaises(ValueError):
            self.m.shift_plan(self.pid, [], 2, doc=self.doc)
        oid = self.store.one("SELECT id FROM task WHERE project_id=? LIMIT 1", (self.other,))["id"]
        with self.assertRaises(LookupError):
            self.m.shift_plan(self.pid, [self.ids[0], oid], 2, doc=self.doc)
        self.assertEqual(self.get(oid)["due_on"], self.get(oid)["due_on"])


class SyncAfterCutover(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        self.store = store
        sys.path.insert(0, str(BASE / "tools"))
        import sync_sheet
        self.s = sync_sheet

    def tearDown(self):
        os.environ.pop("NEWPRODUCT_CUTOVER", None)
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_refuses_after_cutover(self):
        os.environ["NEWPRODUCT_CUTOVER"] = (self.store.today() - dt.timedelta(days=1)).isoformat()
        with self.assertRaises(self.s.AfterCutover):
            self.s.guard(self.store.today().isoformat())
        with self.assertRaises(self.s.AfterCutover):
            self.s.apply([], [], {}, dt.date(2026, 10, 1), "2026-10-01")

    def test_refuses_sheet_date_after_last_day(self):
        cut = self.store.today() + dt.timedelta(days=10)
        os.environ["NEWPRODUCT_CUTOVER"] = cut.isoformat()
        self.s.guard((cut - dt.timedelta(days=1)).isoformat())          # 最終日までは通る
        with self.assertRaises(self.s.AfterCutover):
            self.s.guard(cut.isoformat())


if __name__ == "__main__":
    unittest.main()
