#!/usr/bin/env python3
"""機会カレンダー（FR-78〜81）。発売の目安＝イベントの2か月前・枠は人が押して作る・二重に作らない。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Opp(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-02"
        os.environ["NEWPRODUCT_FCTR"] = "/nonexistent.json"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, opportunity, plan
        seed.run()
        self.m, self.plan, self.store = opportunity, plan, store
        with store.tx() as c:
            c.execute("INSERT INTO theme (id,label,kind,month,day,sort) VALUES ('e1','七五三','年間イベント',11,'11月15日前後',1)")
            c.execute("INSERT INTO theme (id,label,kind,month,day,sort) VALUES ('e2','成人式','年間イベント',1,'1月第2月曜',2)")
            c.execute("INSERT INTO theme (id,label,kind,sort) VALUES ('l1','結婚','ライフイベント',1)")

    def tearDown(self):
        self.store.close()
        for k in ("NEWPRODUCT_TODAY", "NEWPRODUCT_FCTR"):
            os.environ.pop(k, None)
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_launch_is_two_months_before_the_next_occurrence(self):
        a = {x["id"]: x for x in self.m.calendar()["annual"]}
        self.assertEqual((a["e1"]["event_month"], a["e1"]["launch_month"]), ("2026-11", "2026-09"))
        self.assertEqual((a["e2"]["event_month"], a["e2"]["launch_month"]), ("2027-01", "2026-11"),
                         "1月は来年の回。目安は年をまたいで前年11月")

    def test_slot_once_and_needs_a_draft_version(self):
        with self.assertRaises(ValueError):
            self.m.to_slot("e1", "u")                                 # 策定中の版が無い
        v = self.plan.create_version("u", 2026, "検査", "")
        r = self.m.to_slot("e1", "u")
        self.assertEqual(r["launch_month"], "2026-09")
        self.assertEqual(self.store.val("SELECT occasion FROM plan_slot WHERE id=?", (r["id"],)), "七五三")
        with self.assertRaises(ValueError):
            self.m.to_slot("e1", "u")                                 # 二重に作らない
        self.assertTrue({x["id"]: x for x in self.m.calendar()["annual"]}["e1"]["in_plan"])
        with self.assertRaises(ValueError):
            self.m.to_slot("l1", "u")                                 # ライフイベントは月が無い

    def test_calendar_works_without_fctr(self):
        c = self.m.calendar()
        self.assertIsNotNone(c["trends"]["why"])
        self.assertEqual(len(c["life"]), 1)


if __name__ == "__main__":
    unittest.main()
