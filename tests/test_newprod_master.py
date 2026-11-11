#!/usr/bin/env python3
"""週次レポートの新商品マスタへ足す1行（2026-11-10 依頼書 4-4・ADR-139）。
人が決めたキーワードだけ・書き込み待ちだけ渡す・追加済みで登録済みになりタスク完了・重なりは要確認・二重に結ばない・1年で数え直しを止める。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Master(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, newprod_master, gate
        seed.run()
        self.store, self.m = store, newprod_master
        self.pid = project.create("u", expand=False, internal_name="iPhone DUOスキンシール ※急ぎ", flow_type="newmodel",
                                  launch_date="2026-11-13")["id"]
        store.ex("UPDATE project SET stage='発売済' WHERE id=?", (self.pid,))
        store.ex("INSERT INTO task (project_id,seq,title,status,created_at) VALUES (?,1,'新商品売上レポートに計上するよう設定','未着手','x')", (self.pid,))
        store.conn().commit()
        self.p_roles = mock.patch.object(gate, "roles_of", side_effect=lambda u: ["admin"] if u == "chieko" else [])
        self.p_roles.start()

    def tearDown(self):
        self.p_roles.stop()
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def _req(self, **kw):
        f = {"label": "Web deco スキンシール【iPhone DUO】", "include": "スキンシール;DUO", "exclude": "", "month_sales": "10000"}
        f.update(kw)
        return self.m.request(self.pid, f, "chieko")

    def test_proposal_is_not_applied_until_a_person_requests(self):
        pr = self.m.propose(dict(self.store.one("SELECT * FROM project WHERE id=?", (self.pid,))))
        self.assertEqual(pr["label"], "iPhone DUOスキンシール")              # ※以降は落とす
        self.assertEqual(self.m.rows_for_feed()["add"], [])                   # 案だけでは渡さない
        with self.assertRaises(PermissionError):
            self.m.request(self.pid, {"label": "x", "include": "x"}, "nobody")
        for bad in ({"include": ""}, {"include": "(A|"}, {"label": ""}):
            with self.assertRaises(ValueError):
                self._req(**bad)

    def test_added_links_label_and_completes_task_once(self):
        self._req()
        rows = self.m.rows_for_feed(dt.date(2026, 11, 16))
        self.assertEqual(len(rows["add"]), 1)
        a = rows["add"][0]
        self.assertEqual((a["include"], a["monthly_sales"][0], a["monthly_qty"][0], a["release_date"]),
                         ("スキンシール;DUO", 10000, 0, "2026-11-13"))
        self.m.apply_results([{"project_id": self.pid, "result": "追加済み", "matches": 3}])
        g = self.store.one("SELECT * FROM newprod_reg WHERE project_id=?", (self.pid,))
        self.assertEqual((g["state"], g["labels"]), ("登録済み", '["Web deco スキンシール【iPhone DUO】"]'))
        self.assertEqual(self.store.val("SELECT status FROM task WHERE project_id=?", (self.pid,)), "完了")
        self.assertEqual(self.m.rows_for_feed(dt.date(2026, 11, 16))["add"], [])          # 2度渡さない
        self.assertEqual(len(self.m.rows_for_feed(dt.date(2026, 11, 16))["check"]), 1)    # 当たりは数え直す
        r = self.m.apply_results([{"project_id": self.pid, "result": "追加済み"}])          # 2度目の追加済みは無視
        self.assertEqual(r["applied"], 0)
        with self.assertRaises(ValueError):
            self._req()
        self.assertEqual(self.m.rows_for_feed(dt.date(2027, 11, 13))["check"], [])          # 1年で数え直しを止める

    def test_overlap_is_check_and_can_be_fixed(self):
        self._req()
        self.m.apply_results([{"project_id": self.pid, "result": "要確認", "matches": 5, "overlaps": ["Web deco iPhone スキンシール【iPhone 18 Pro 用】"],
                               "note": "既存の行と同じ明細に当たります"}])
        v = self.m.view(self.pid)
        self.assertEqual((v["state"], v["overlaps"]), ("要確認", ["Web deco iPhone スキンシール【iPhone 18 Pro 用】"]))
        self.assertEqual(self.store.val("SELECT state FROM newprod_reg WHERE project_id=?", (self.pid,)), "登録待ち")
        self.assertEqual(self.store.val("SELECT state FROM launch_link WHERE project_id=? AND kind='weekly'", (self.pid,)), "要確認")
        self._req(exclude="18 Pro")                                          # 直してもう一度
        self.assertEqual(self.m.view(self.pid)["state"], "書き込み待ち")

    def test_zero_matches_after_three_weeks_is_flagged(self):
        self._req()
        self.m.apply_results([{"project_id": self.pid, "result": "追加済み", "matches": 0}])
        with mock.patch.object(self.store, "today", return_value=dt.date(2026, 12, 7)):
            self.m.apply_results([{"project_id": self.pid, "result": "当たり", "matches": 0}])
        self.assertIn("0件", self.m.view(self.pid)["note"])

    def test_same_label_on_two_projects_is_refused(self):
        from app import project
        self._req()
        other = project.create("u", expand=False, internal_name="y", flow_type="meire")["id"]
        with self.assertRaises(ValueError):
            self.m.request(other, {"label": "Web deco スキンシール【iPhone DUO】", "include": "y"}, "chieko")


if __name__ == "__main__":
    unittest.main()
