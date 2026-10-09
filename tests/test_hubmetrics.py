#!/usr/bin/env python3
"""
HUB へ指標を書き出す（FR-123・ADR-091）の検査。**Auto GROWTH へは送らない**（受け口を差し替える）。

  - 新商品の月別売上は、12か月以内の商品がそろう 2023-04 から表の最後の月まで。発売本数は 2022-05 から
  - 起票数は 2026-10 が締まってから。コンセプト在庫は未計測なら書かない（0 と書かない）
  - 送った点は二度送らない。値が後から変わった点は送らずに知らせる。送れなかった点は覚えない
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class HubMetrics(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, hubmetrics
        seed.run()
        self.m, self.store = hubmetrics, store
        now = store.now_s()
        with store.tx() as c:
            c.execute("INSERT INTO past_product (code,name,launch_date,fy,first12_yen,months_seen,complete,months_json,source,imported_at) "
                      "VALUES ('P1','a','2023-03-10',2022,1200,12,1,?,'t',?)", (json.dumps([100] * 12), now))
            # 表は数字の無い先の月まで 0 で埋めてある（P2 は 3か月目までしか数字が無い）
            c.execute("INSERT INTO past_product (code,name,launch_date,fy,first12_yen,months_seen,complete,months_json,source,imported_at) "
                      "VALUES ('P2','b','2023-05-01',2023,30,3,0,?,'t',?)", (json.dumps([10, 10, 10] + [0] * 9), now))
            c.execute("INSERT INTO past_product (code,name,launch_date,fy,first12_yen,months_seen,complete,months_json,source,imported_at) "
                      "VALUES ('P3','c','2024-03-01',2023,0,0,0,?,'t',?)", (json.dumps([0] * 12), now))
        self.today = dt.date(2026, 11, 3)

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + suffix)
            except FileNotFoundError:
                pass

    def _rows(self, metric):
        return [r for r in self.m.build(self.today)["rows"] if r["metric"] == metric]

    def test_ranges_and_values(self):
        rev = {r["captured_at"][:7]: r["value"] for r in self._rows("revenue_newproduct")}
        self.assertEqual(min(rev), "2023-04", "12か月以内の商品がそろう月から")
        self.assertEqual(max(rev), "2024-02", "表の最後の月（合計が 0 でない最後の月）まで。0 で埋めた先の月は送らない")
        self.assertEqual(rev["2023-05"], 110)
        self.assertEqual(rev["2023-08"], 100)
        la = {r["captured_at"][:7]: r["value"] for r in self._rows("launches")}
        self.assertEqual((min(la), la["2023-03"], la["2022-06"]), ("2022-05", 1, 0))
        self.assertEqual(self._rows("revenue_newproduct")[0]["captured_at"], "2023-04-30 23:59:00")
        self.assertEqual(self._rows("revenue_newproduct")[0]["dims"], '{"window": "month"}')
        self.assertEqual([r["captured_at"][:7] for r in self._rows("ideas_created")], ["2026-10"])
        self.assertEqual(self._rows("concept_stock_months"), [], "未計測なら書かない")
        self.assertEqual([r for r in self.m.build(dt.date(2026, 10, 20))["rows"] if r["metric"] == "ideas_created"], [],
                         "2026-10 が締まるまで書かない")

    def _ok(self, sent):
        class Res:
            def __init__(self, n): self.n = n
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({"inserted": self.n}).encode()

        def op(req, timeout):
            body = json.loads(req.data)
            sent.append(body)
            return Res(len(body))
        return op

    def test_send_once_and_report_changes(self):
        sent = []
        r = self.m.push(self.today, opener=self._ok(sent))
        n = r["sent"]
        self.assertGreater(n, 0)
        self.assertEqual(sent[0][0]["source"], "newproduct")
        self.assertEqual(self.m.push(self.today, opener=self._ok(sent))["sent"], 0, "二度送らない")
        self.store.ex("UPDATE past_product SET months_json=? WHERE code='P2'", (json.dumps([50, 10, 10]),))
        self.store.conn().commit()
        p = self.m.push(self.today, opener=self._ok(sent))
        self.assertEqual(p["sent"], 0)
        self.assertEqual([(c["captured_at"][:7], c["sent_value"], c["value"]) for c in p["changed"]], [("2023-05", 110, 150)])

    def test_failure_is_not_remembered(self):
        def bad(req, timeout):
            raise urllib.error.URLError("refused")
        r = self.m.push(self.today, opener=bad)
        self.assertEqual(r["sent"], 0)
        self.assertIn("送れませんでした", r["error"])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM hub_metric_sent", (), 0), 0)


if __name__ == "__main__":
    unittest.main()
