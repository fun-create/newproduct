#!/usr/bin/env python3
"""
繰り返しの仕事（ADR-108）の検査。

見ているのは:
  - 回の日付（毎週の曜日・毎月の日と月末・毎年の月）
  - 時期が近づいた回だけ「案件外の仕事」に起こし、同じ回は二度起こさない。「使わない」は起こさない。登録より前の回はさかのぼらない
  - 毎日の仕事はタスクにしない
  - シートからの取り込み: 時期が読めないものは「使わない」と理由。二度入れない
  - 登録できるのは業務ロールの人だけ。書き方の検査
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


class Routine(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-19"          # 月曜
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, routine
        seed.run()
        self.store, self.m = store, routine
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('devdept','chieko',?)", (store.now_s(),))

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass
        os.environ.pop("NEWPRODUCT_TODAY", None)

    def d(self, s):
        return dt.date.fromisoformat(s)

    def test_occurrence_dates(self):
        occ = self.m.occurrences
        self.assertEqual(occ({"cadence": "weekly", "weekdays": "0,4"}, self.d("2026-10-19"), self.d("2026-10-26")),
                         [self.d("2026-10-19"), self.d("2026-10-23"), self.d("2026-10-26")])
        self.assertEqual(occ({"cadence": "monthly", "day": "last"}, self.d("2026-10-19"), self.d("2026-12-01")),
                         [self.d("2026-10-31"), self.d("2026-11-30")])
        self.assertEqual(occ({"cadence": "monthly", "day": "31"}, self.d("2026-11-01"), self.d("2026-11-30")),
                         [self.d("2026-11-30")], "31日の無い月は月末")
        self.assertEqual(occ({"cadence": "yearly", "months": "4,8,12", "day": "1"}, self.d("2026-10-19"), self.d("2027-05-01")),
                         [self.d("2026-12-01"), self.d("2027-04-01")])

    def test_ensure_creates_once_and_respects_active(self):
        r = self.m.save({"title": "翌週のプラン作成", "cadence": "weekly", "weekdays": "4", "role": "devdept", "assignee": "chieko", "hours": "0.5"}, "chieko")
        self.m.save({"title": "使わない仕事", "cadence": "weekly", "weekdays": "4", "active": "0"}, "chieko")
        self.m.save({"title": "全店の注文メールチェック", "cadence": "daily", "timing": "出勤時"}, "chieko")
        ws = self.store.rows(self.store.q("SELECT * FROM work_item WHERE category='ルーティン'"))
        self.assertEqual([(w["title"], w["due_on"], w["assignee"], w["role"]) for w in ws],
                         [("翌週のプラン作成", "2026-10-23", "chieko", "devdept")], "6日以内の金曜1回だけ・使わない／毎日は起こさない")
        self.assertEqual(self.m.ensure(), 0, "同じ回は二度起こさない")
        self.assertEqual(self.m.ensure(self.d("2026-10-24")), 1, "次の週の分は時期が来たら起きる")
        self.assertEqual(self.m.ensure(self.d("2026-10-10")), 0, "登録より前の回はさかのぼらない")
        lst = self.m.listing()
        row = next(x for x in lst["rows"] if x["id"] == r["id"])
        self.assertEqual((row["rule"], row["next"]), ("毎週 金曜", "2026-10-23"))

    def test_import_sheet(self):
        n = self.m.import_sheet("import", active=False)
        self.assertGreaterEqual(n["added"], 15)
        self.assertEqual(n["inactive"], n["added"], "全部「使わない」で入れられる")
        self.assertEqual(self.m.import_sheet("import")["added"], 0, "二度入れない")
        r = self.store.one("SELECT * FROM routine WHERE title='FUN-Meeting-商品開発'")
        self.assertIn("月2回", r["note"])
        g = self.store.one("SELECT * FROM routine WHERE title='目標作成と評価'")
        self.assertEqual((g["cadence"], g["months"]), ("yearly", "4,8,12"))
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM work_item"), 0, "使わないものは起こさない")

    def test_undetermined_timing(self):
        """時期の読めなかった行は「1日」と見せず、使うにしても回を作らない。時期を決めて保存したら注意を外す。"""
        self.m.import_sheet("import", active=False)
        r = dict(self.store.one("SELECT * FROM routine WHERE title='FUN-Meeting-商品開発'"))
        self.assertEqual(self.m.rule_text(r), "毎月（日が未定）")
        ip = dict(self.store.one("SELECT * FROM routine WHERE title='新型iPhone用商品準備'"))
        self.assertEqual(self.m.rule_text(ip), "毎年（月が未定）")
        self.store.ex("UPDATE routine SET active=1 WHERE id=?", (r["id"],))
        self.store.conn().commit()
        self.assertEqual(self.m.occurrences(r, self.d("2026-10-01"), self.d("2027-10-01")), [])
        self.assertEqual(self.m.ensure(), 0, "時期が未定なら起こさない")
        f = {"id": r["id"], "title": r["title"], "cadence": "monthly", "day": "", "note": r["note"]}
        with self.assertRaises(ValueError):
            self.m.save(f, "chieko")                                 # 日を空のまま
        self.m.save({**f, "day": "15"}, "chieko")
        r2 = self.store.one("SELECT day, note FROM routine WHERE id=?", (r["id"],))
        self.assertEqual((r2["day"], r2["note"]), ("15", None))

    def test_permission_and_validation(self):
        with self.assertRaises(PermissionError):
            self.m.save({"title": "x", "cadence": "weekly", "weekdays": "0"}, "nobody")
        for bad in ({"title": "x", "cadence": "weekly"}, {"title": "x", "cadence": "yearly"},
                    {"title": "x", "cadence": "monthly", "day": "40"}, {"title": "x", "cadence": "monthly"},
                    {"title": "", "cadence": "daily"},
                    {"title": "x", "cadence": "hourly"}):
            with self.assertRaises(ValueError):
                self.m.save(bad, "chieko")


if __name__ == "__main__":
    unittest.main()
