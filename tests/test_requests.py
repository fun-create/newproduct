#!/usr/bin/env python3
"""
他部署への依頼の受け渡し（FR-117・ADR-079）。依頼日・受領日・完了日と、その間の日数。

  - 受領は完了とは別。二度押しても最初の日時のまま
  - 受領を押さずに完了したら、完了日を受領日にも入れる
  - まだのときは「今日まで何日たったか」を出す
  - 案件外の仕事には使わない
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Requests(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-09"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, task
        seed.run()
        self.m, self.store = task, store

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def row(self, wid):
        r = dict(self.store.one("SELECT * FROM work_item WHERE id=?", (wid,)))
        return r, self.m.handoff_days(r)

    def test_requested_received_finished(self):
        wid = self.m.create_work_item({"kind": "他部署依頼", "title": "撮影", "dept": "Webマーケ"}, "u")["id"]
        with self.store.tx() as c:
            c.execute("UPDATE work_item SET created_at='2026-10-01 09:00:00' WHERE id=?", (wid,))
        r, d = self.row(wid)
        self.assertEqual((d["to_receive"], d["received"]), (8, False), "まだなら今日までの日数")
        first = self.m.receive_work_item(wid, "w")["received_at"]
        self.assertEqual(self.m.receive_work_item(wid, "w2")["received_at"], first, "二度押しても最初のまま")
        with self.store.tx() as c:
            c.execute("UPDATE work_item SET received_at='2026-10-03 10:00:00' WHERE id=?", (wid,))
        self.m.accept_work_item(wid, "w")
        r, d = self.row(wid)
        self.assertEqual((d["to_receive"], d["received"], d["finished"]), (2, True, True))

    def test_accept_without_receive_fills_received(self):
        wid = self.m.create_work_item({"kind": "他部署依頼", "title": "梱包", "dept": "生産部"}, "u")["id"]
        self.m.accept_work_item(wid, "w")
        r, _ = self.row(wid)
        self.assertEqual(r["received_at"], r["accepted_at"])
        own = self.m.create_work_item({"kind": "案件外", "title": "FBA納品"}, "u")["id"]
        with self.assertRaises(ValueError):
            self.m.receive_work_item(own, "u")


if __name__ == "__main__":
    unittest.main()
