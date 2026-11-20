#!/usr/bin/env python3
"""企画スプシの URL（FR-332 段階1・ADR-163）。G0 を通したら URL を入れるタスクが1行並ぶ・URL を入れたら完了・
Google スプレッドシート以外の URL は断る・発売済には足さない・2回通しても2行にしない。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests"))
from test_followup import fake_cal  # noqa: E402

SID = "1CqJ8_hs_XTdR53zOCBlKfAsaAmjXkH6Mm-Zz3xOd3kA"


class PlanSheet(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store, seed, project, gate, plansheet
        store.close()
        seed.run()
        self.store, self.project, self.gate, self.ps = store, project, gate, plansheet
        self.cal = mock.patch("app.calfc.fetch", side_effect=fake_cal)
        self.cal.start()
        self.miss = mock.patch("app.gate.missing", return_value=[])
        self.miss.start()
        g0 = next(g for g in gate.defs() if g["gate"] == "G0")
        for rc in g0["approver_role"]:
            store.ex("INSERT OR IGNORE INTO role_member (user_id, role_code) VALUES ('boss', ?)", (rc,))
        store.conn().commit()
        self.pid = project.create("u", expand=False, internal_name="検査の商品", flow_type="readymade",
                                  launch_date="2027-03-01")["id"]

    def tearDown(self):
        self.miss.stop()
        self.cal.stop()
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def tasks(self):
        return [dict(r) for r in self.store.q("SELECT * FROM task WHERE project_id=? AND origin='plan_sheet'", (self.pid,))]

    def test_g0_adds_one_task_with_business_day_due(self):
        with mock.patch("app.store.today_s", return_value="2026-11-20"):      # 金曜 → 3営業日後は 11-25（水）
            r = self.gate.review(self.pid, "G0", "通過", "boss")
        self.assertTrue(r["plan_sheet"]["added"])
        t = self.tasks()
        self.assertEqual(len(t), 1)
        self.assertEqual((t[0]["status"], t[0]["due_on"], t[0]["role"]), ("未着手", "2026-11-25", "devdept"))
        note = self.store.val("SELECT detail FROM project_revision WHERE project_id=? AND what LIKE 'タスクを足した:%'", (self.pid,))
        self.assertIn("2026-11-20 より後の3営業日目", note)
        self.assertNotIn("発売日", note)
        self.gate.review(self.pid, "G0", "通過", "boss")                       # 通し直しても2行にしない
        self.assertEqual(len(self.tasks()), 1)

    def test_other_results_do_not_add(self):
        self.gate.review(self.pid, "G0", "差戻し", "boss")
        self.assertEqual(self.tasks(), [])

    def test_url_completes_task_and_is_normalized(self):
        self.gate.review(self.pid, "G0", "通過", "boss")
        self.project.update_header(self.pid, {"plan_sheet_url": f"https://docs.google.com/spreadsheets/d/{SID}/edit?gid=0#gid=0"}, "", "u")
        p = self.store.one("SELECT plan_sheet_url FROM project WHERE id=?", (self.pid,))
        self.assertEqual(p["plan_sheet_url"], f"https://docs.google.com/spreadsheets/d/{SID}")
        self.assertEqual(self.tasks()[0]["status"], "完了")
        # 入っている URL を変えるには理由が要る（頭の欄の決まり）
        with self.assertRaises(ValueError):
            self.project.update_header(self.pid, {"plan_sheet_url": f"https://docs.google.com/spreadsheets/d/{SID[::-1]}"}, "", "u")

    def test_url_already_set_no_task(self):
        self.project.update_header(self.pid, {"plan_sheet_url": f"https://docs.google.com/spreadsheets/d/{SID}"}, "", "u")
        r = self.gate.review(self.pid, "G0", "通過", "boss")
        self.assertFalse(r["plan_sheet"]["added"])
        self.assertEqual(self.tasks(), [])

    def test_rejects_non_sheet_urls(self):
        for bad in ("https://example.com/spreadsheets/d/" + SID, "http://docs.google.com/spreadsheets/d/" + SID,
                    "https://docs.google.com/document/d/" + SID, "javascript:alert(1)", "シートの名前"):
            with self.assertRaises(ValueError, msg=bad):
                self.project.update_header(self.pid, {"plan_sheet_url": bad}, "", "u")

    def test_backfill_only_g0_passed_and_not_launched(self):
        launched = self.project.create("u", expand=False, internal_name="出た商品", flow_type="readymade",
                                       launch_date="2026-01-01", stage="発売済")["id"]
        held = self.project.create("u", expand=False, internal_name="止めた商品", flow_type="readymade",
                                   launch_date="2027-01-01", stage="保留")["id"]
        for pid in (self.pid, launched, held):
            self.store.ex("INSERT INTO gate_review (project_id,gate,result,approved_by,approved_at) VALUES (?, 'G0','通過','boss','2026-11-01')", (pid,))
        self.store.conn().commit()
        dry = self.ps.backfill("u")
        self.assertEqual(dry["targets"], [self.pid])
        self.assertEqual(self.tasks(), [])                                     # 既定は数えるだけ
        self.ps.backfill("u", apply=True)
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.ps.backfill("u")["targets"], [])                 # 2回目は対象なし


if __name__ == "__main__":
    unittest.main()
