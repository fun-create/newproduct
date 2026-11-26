#!/usr/bin/env python3
"""年度累計（R01・FR-329・ADR-164）。依頼書の完了条件をそのまま確かめる:
4/30 以前は入らず 5/1 から入る・前年度発売は1年の前日まで入り、その日から入らない・1年を過ぎても前の分は残る・
2/29 発売は翌年 2/28 まで・重ねて取り込んでも増えない・月別の表では境の月を入れず暫定・結んでいないラベルは別に出す。"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
D = dt.date


class Ytd(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store, seed, project, newprod_daily, newprod_ytd
        store.close()
        seed.run()
        self.store, self.project, self.daily, self.y = store, project, newprod_daily, newprod_ytd

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def link(self, name, launch, labels, basis="全額"):
        pid = self.project.create("u", expand=False, internal_name=name, launch_date=launch, stage="発売済")["id"]
        self.store.ex("UPDATE project SET revenue_counted=1, revenue_basis=? WHERE id=?", (basis, pid))
        self.store.ex("INSERT INTO newprod_reg (project_id,state,labels,created_at) VALUES (?, '登録済み', ?, '2026-01-01')",
                      (pid, json.dumps(labels, ensure_ascii=False)))
        self.store.conn().commit()
        return pid

    def send(self, labels, f="2026-05-01", t="2026-11-19"):
        return self.daily.receive({"from": f, "to": t, "amount_col": "税込合計", "labels": labels})

    def row(self, r, lab):
        return next(x for x in r["labels"] if x["label"] == lab)

    def test_fiscal_year_and_one_year_window(self):
        self.link("前年度の商品", "2025-08-08", ["A"])               # 2026-08-08 から入らない
        self.link("今年度の商品", "2026-06-15", ["B"])
        self.send({"A": {"2026-05-01": 100, "2026-08-07": 10, "2026-08-08": 1000, "2026-10-01": 1000},
                   "B": {"2026-06-14": 7, "2026-06-15": 20, "2026-11-19": 5}})
        # 4/30 の分は年度の外（送られる期間にも入らない）。念のため前年度の日を入れておく
        self.store.ex("INSERT INTO newprod_daily (label, day, amount) VALUES ('A','2026-04-30',99999)")
        self.store.conn().commit()
        r = self.y.ytd(D(2026, 11, 20))
        self.assertEqual((r["fy"], r["from"], r["to"], r["source"]), (2026, "2026-05-01", "2026-11-19", "daily"))
        self.assertEqual(self.row(r, "A")["amount"], 110)              # 5/1 と 8/7 だけ。8/8 以降と 4/30 は入らない
        self.assertEqual(self.row(r, "A")["to"], "2026-08-07")
        self.assertEqual(self.row(r, "B")["amount"], 25)               # 発売前日の 7 は入らない
        self.assertEqual(r["total"], 135)
        self.assertFalse(r["provisional"])

    def test_resend_and_overlap_do_not_double(self):
        self.link("商品", "2026-06-01", ["B"])
        self.send({"B": {"2026-06-02": 100}})
        self.send({"B": {"2026-06-02": 100}})                          # 同じものをもう一度
        self.send({"B": {"2026-06-02": 100, "2026-11-20": 50}}, t="2026-11-20")   # 期間の重なる次の週
        self.assertEqual(self.y.ytd(D(2026, 11, 21))["total"], 150)

    def test_feb29_launch(self):
        self.assertEqual(self.y.one_year_end(D(2028, 2, 29)), D(2029, 3, 1))
        self.assertEqual(self.y.one_year_end(D(2026, 2, 28)), D(2027, 2, 28))

    def test_after_fiscal_year_end_counts_to_april_30(self):
        self.link("商品", "2026-12-01", ["B"])
        self.send({"B": {"2027-04-30": 10, "2027-05-01": 99}}, f="2026-05-01", t="2027-05-02")
        r = self.y.ytd(D(2027, 4, 30))
        self.assertEqual((r["to"], r["total"]), ("2027-04-30", 10))
        self.assertEqual(self.y.ytd(D(2027, 5, 3))["fy"], 2027)         # 5/1 から次の年度

    def test_monthly_fallback_is_provisional_and_skips_boundary_months(self):
        self.link("前年度の商品", "2025-08-08", ["A"])
        self.link("今年度の商品", "2026-06-01", ["B"])
        monthly = {"A": {"months": [f"2026-{m:02d}" for m in range(1, 12)], "sales": [1] * 4 + [100, 100, 100, 50, 0, 0, 0]},
                   "B": {"months": [f"2026-{m:02d}" for m in range(1, 12)], "sales": [0] * 5 + [10, 10, 10, 10, 10, 3]}}
        self.store.ex("INSERT INTO newprod_weekly (period_from,period_to,file_name,rows,monthly,received_at) VALUES "
                      "('2026-10-30','2026-11-12','x.xlsx',?,?,'2026-11-16 13:12:48')",
                      (json.dumps({"rows": [], "total": None}), json.dumps(monthly)))
        self.store.conn().commit()
        r = self.y.ytd(D(2026, 11, 20))
        self.assertEqual((r["source"], r["to"], r["received_at"]), ("monthly", "2026-11-12", "2026-11-16 13:12:48"))
        self.assertTrue(r["provisional"])
        self.assertEqual(self.row(r, "A")["amount"], 300)              # 5〜7月。8月（1年の境）は入れない
        self.assertEqual([k["month"] for k in self.row(r, "A")["skipped"]], ["2026-08"])
        self.assertEqual(self.row(r, "B")["amount"], 53)               # 6月1日発売は6月まるごと・11月は12日まで
        self.assertEqual(r["total"], 353)

    def test_unlinked_label_is_shown_not_summed(self):
        self.link("商品", "2026-06-01", ["B"])
        self.send({"B": {"2026-06-02": 100}, "どこにも結ばれていない": {"2026-07-01": 500}})
        r = self.y.ytd(D(2026, 11, 20))
        self.assertEqual(r["total"], 100)
        self.assertEqual(r["unlinked"], [{"label": "どこにも結ばれていない", "amount": 500}])

    def test_increment_projects_are_named(self):
        self.link("リニューアル", "2026-06-01", ["R"], basis="増分")
        self.send({"R": {"2026-06-02": 100}})
        r = self.y.ytd(D(2026, 11, 20))
        self.assertEqual(r["increment"], ["R"])
        self.assertEqual(r["total"], 100)

    def test_deactivated_label_stays_in_ytd(self):
        """ADR-166: 発売から1年で「有効」を外しても、年度内のそれまでの分は年度累計に残る"""
        self.link("外した商品", "2025-09-01", ["X"])
        self.link("続く商品", "2026-06-01", ["Y"])
        self.send({"X": {"2026-05-10": 300, "2026-08-31": 20}, "Y": {"2026-06-01": 5, "2026-11-19": 1}})
        self.send({"Y": {"2026-06-01": 5, "2026-11-19": 1}})           # X はもう送られない
        r = self.y.ytd(dt.date(2026, 11, 20))
        self.assertEqual(self.row(r, "X")["amount"], 320)
        self.assertEqual(r["total"], 326)
        self.assertFalse(any("二重" in n for n in r["notes"]))

    def test_double_count_is_noted(self):
        self.link("名前を変えた商品", "2026-06-01", ["旧", "新"])
        self.send({"旧": {"2026-06-10": 10}})
        self.send({"新": {"2026-06-10": 10}})
        r = self.y.ytd(dt.date(2026, 11, 20))
        self.assertTrue(any("二重" in n for n in r["notes"]))


if __name__ == "__main__":
    unittest.main()
