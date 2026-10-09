#!/usr/bin/env python3
"""
タスクの目安（FR-41・ADR-084）の検査。カレンダーアプリは呼ばない（休業日の返事を差し替える）。

  - 目安は期限（due_on）に入れない。「目安を期限にする」で、期限の空いたタスクだけに入る
  - 営業日は会社休業日だけで決める。登録の外の日がかかれば計算しない
  - 今日から発売日の前日までの営業日を、標準工数の比で順に割る（最初は初日・最後は最終営業日）
  - 発売日が無い・標準工数が全部空なら、理由を返して目安を出さない
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


def _doc(fr, to, closed=()):
    return {"closed": list(closed), "covered": [{"from": fr, "to": to, "name": "検査", "key": "t"}]}


class Schedule(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-11-02"            # 月曜
        self.addCleanup(os.environ.pop, "NEWPRODUCT_TODAY", None)
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, schedule
        seed.run()
        self.m, self.store = schedule, store
        self.pid = project.create("u", expand=False, internal_name="目安", flow_type="meire",
                                  launch_date="2026-11-12")["id"]
        with store.tx() as c:
            for seq, h, st in ((1, 1.0, "未着手"), (2, 3.0, "未着手"), (3, None, "未着手"),
                               (4, 4.0, "未着手"), (5, 2.0, "完了")):
                c.execute("INSERT INTO task (project_id,seq,title,hours,status) VALUES (?,?,?,?,?)",
                          (self.pid, seq, f"t{seq}", h, st))

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + suffix)
            except FileNotFoundError:
                pass

    def _tasks(self):
        return {r["seq"]: dict(r) for r in self.store.q("SELECT * FROM task WHERE project_id=?", (self.pid,))}

    def test_split_by_hours_over_business_days(self):
        # 11/2(月)〜11/11(水)。土日（7・8）と 11/3 を会社休業日に → 営業日7日
        doc = _doc("2026-11-01", "2026-11-30", ["2026-11-03", "2026-11-07", "2026-11-08"])
        r = self.m.refresh(self.pid, "u", doc)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["days"], r["from"], r["to"]), (7, "2026-11-02", "2026-11-11"))
        t = self._tasks()
        self.assertEqual((t[1]["plan_start"], t[1]["plan_due"]), ("2026-11-02", "2026-11-02"))
        self.assertEqual(t[4]["plan_due"], "2026-11-11", "最後のタスクは発売前の最終営業日")
        self.assertEqual(t[3]["plan_start"], t[3]["plan_due"], "工数が空なら前と同じ日に置く")
        self.assertIsNone(t[5]["plan_due"], "完了したタスクは割り当てない")
        for x in t.values():
            self.assertIsNone(x["due_on"], "目安は期限に入れない")
            if x["plan_start"]:
                self.assertNotIn(x["plan_start"], ("2026-11-03", "2026-11-07", "2026-11-08"))

    def test_adopt_fills_only_empty_due_dates(self):
        doc = _doc("2026-11-01", "2026-11-30")
        self.m.refresh(self.pid, "u", doc)
        self.store.ex("UPDATE task SET due_on='2026-11-05' WHERE project_id=? AND seq=1", (self.pid,))
        self.store.conn().commit()
        r = self.m.adopt(self.pid, "boss")
        self.assertEqual(r["adopted"], 3)
        t = self._tasks()
        self.assertEqual(t[1]["due_on"], "2026-11-05", "人が入れた期限は上書きしない")
        self.assertEqual(t[4]["due_on"], t[4]["plan_due"])
        self.assertIsNone(t[5]["due_on"])
        last = self.store.one("SELECT * FROM project_revision WHERE project_id=? ORDER BY id DESC LIMIT 1", (self.pid,))
        self.assertEqual(last["changed_by"], "boss")
        self.assertEqual(self.m.adopt(self.pid, "boss")["adopted"], 0, "二度目は何も変えない")

    def test_unknown_days_are_not_counted(self):
        doc = _doc("2026-11-01", "2026-11-05")            # 11/6 以降は登録が無い
        r = self.m.refresh(self.pid, "u", doc)
        self.assertFalse(r["ok"])
        self.assertIn("登録されていません", r["why"])
        self.assertTrue(all(x["plan_due"] is None for x in self._tasks().values()))

    def test_no_launch_date_no_plan(self):
        from app import project
        pid = project.create("u", expand=False, internal_name="日未定", flow_type="meire")["id"]
        r = self.m.compute(pid, _doc("2026-11-01", "2026-12-31"))
        self.assertFalse(r["ok"])
        self.assertIn("発売予定日", r["why"])

    def test_all_hours_empty(self):
        self.store.ex("UPDATE task SET hours=NULL WHERE project_id=?", (self.pid,))
        self.store.conn().commit()
        r = self.m.compute(self.pid, _doc("2026-11-01", "2026-11-30"))
        self.assertFalse(r["ok"])
        self.assertIn("標準工数", r["why"])


if __name__ == "__main__":
    unittest.main()
