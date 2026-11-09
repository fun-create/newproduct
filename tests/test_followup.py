#!/usr/bin/env python3
"""発売後の仕事を案件外に作る（2026-11-09 修正依頼書 H04・H05・H07・ADR-128）。"""
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


def fake_cal(fr, to):
    """土日が休み。全期間を登録済みとする"""
    a, b = dt.date.fromisoformat(fr), dt.date.fromisoformat(to)
    closed, d = [], a
    while d <= b:
        if d.weekday() >= 5:
            closed.append(d.isoformat())
        d += dt.timedelta(days=1)
    return {"closed": closed, "covered": [{"from": fr, "to": to}]}


class Followup(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project
        seed.run()
        self.store, self.project = store, project
        self.cal = mock.patch("app.calfc.fetch", side_effect=fake_cal)
        self.cal.start()
        # 2026-08-07 は金曜。+2か月の 10-07 は水曜、+6か月の 2027-02-07 は日曜
        self.pid = project.create("u", expand=False, internal_name="検査の商品", flow_type="readymade",
                                  launch_date="2026-08-07")["id"]

    def tearDown(self):
        self.cal.stop()
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def works(self):
        return {r["origin"]: dict(r) for r in self.store.q(
            "SELECT * FROM work_item WHERE project_id=? ORDER BY origin", (self.pid,))}

    def proj(self):
        return dict(self.store.one("SELECT * FROM project WHERE id=?", (self.pid,)))

    def test_g5_moves_to_launched_and_makes_work_items_once(self):
        from app import gate
        r1 = gate._launched(self.proj(), "u")
        r2 = gate._launched(self.proj(), "u")             # 通し直しても増えない
        self.assertEqual(self.proj()["stage"], "発売済")
        self.assertTrue(r1["stage_moved"])
        self.assertFalse(r2["stage_moved"])
        w = self.works()
        self.assertEqual(sorted(w), ["fba", "mall", "plc"])
        self.assertEqual((w["plc"]["role"], w["plc"]["due_on"], w["plc"]["kind"]), ("prod", "2026-10-07", "案件外"))
        self.assertEqual((w["mall"]["role"], w["mall"]["due_on"]), ("webmkt", "2026-08-13"))   # 発売より後の4営業日目
        self.assertEqual((w["fba"]["role"], w["fba"]["due_on"]), ("admin", "2027-02-08"))      # 日曜は次の営業日へ
        self.assertIn("検査の商品", w["plc"]["title"])
        # **新商品の案件の中にはタスクを足さない**
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM task WHERE project_id=?", (self.pid,), 0), 0)
        self.assertEqual(len(r2["followup"]["created"]), 0)

    def test_no_launch_date_makes_nothing(self):
        from app import followup
        self.store.ex("UPDATE project SET launch_date=NULL WHERE id=?", (self.pid,))
        r = followup.ensure(self.pid, "u")
        self.assertEqual(r["created"], [])
        self.assertIn("発売日が空", r["why"])
        self.assertEqual(self.works(), {})

    def test_plc_not_needed_or_custom(self):
        from app import followup
        followup.set_plc(self.pid, "none", "u")
        followup.ensure(self.pid, "u")
        self.assertNotIn("plc", self.works())
        followup.set_plc(self.pid, "days:45", "u")       # 2026-09-21（月）
        followup.ensure(self.pid, "u")
        self.assertEqual(self.works()["plc"]["due_on"], "2026-09-21")
        followup.set_plc(self.pid, "date:2026-12-01", "u")
        self.assertEqual(self.works()["plc"]["due_on"], "2026-12-01")
        followup.set_plc(self.pid, "none", "u")          # 不要にしても消さない。対象外にする
        self.assertEqual(self.works()["plc"]["status"], "対象外")
        with self.assertRaises(ValueError):
            followup.set_plc(self.pid, "months:0", "u")

    def test_default_need_depends_on_flow(self):
        from app import followup
        self.assertTrue(followup.plc_setting(self.proj())["need"])
        self.store.ex("UPDATE project SET flow_type='newmodel' WHERE id=?", (self.pid,))
        self.assertFalse(followup.plc_setting(self.proj())["need"])
        self.assertEqual([o for o, _r in followup.wanted(self.proj())], ["mall"])

    def test_launch_date_change_moves_only_open_items(self):
        from app import followup
        followup.ensure(self.pid, "u")
        self.store.ex("UPDATE work_item SET status='完了' WHERE project_id=? AND origin='mall'", (self.pid,))
        self.project.set_launch_date(self.pid, "2026-09-04", "延期", "u")
        w = self.works()
        self.assertEqual(w["plc"]["due_on"], "2026-11-04")
        self.assertEqual(w["mall"]["due_on"], "2026-08-13")   # 完了は動かさない

    def test_retired_g6_is_out_of_scope_everywhere(self):
        from app import gate
        st = {g["gate"]: g["state"] for g in gate.board(self.proj())}
        self.assertEqual(st["G6"], "対象外")
        self.store.ex("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('admin','u',?)", (self.store.now_s(),))
        with self.assertRaises(ValueError):                 # 資格があっても通せない（対象外）
            gate.review(self.pid, "G6", "通過", "u")


class BlankFlow(Followup):
    """開発タイプが空の移行案件は、案件のタスクに発売後の行があったかで決める"""

    def test_blank_flow_uses_migrated_rows(self):
        from app import followup
        self.store.ex("UPDATE project SET flow_type=NULL WHERE id=?", (self.pid,))
        self.assertEqual(followup.wanted(self.proj()), [])
        for i, t in enumerate(("18 DUO 楽天に出品", "生産の問題点を確認（2か月後）→解決とフィードバックをプラン",
                               "楽天に出品の準備をする"), 1):
            self.store.ex("INSERT INTO task (project_id,seq,title,status,created_at) VALUES (?,?,?,'対象外',?)",
                          (self.pid, i, t, self.store.now_s()))
        self.assertEqual([o for o, _r in followup.wanted(self.proj())], ["plc", "mall"])
