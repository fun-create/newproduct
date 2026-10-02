#!/usr/bin/env python3
"""ステージの遷移（FR-33）・バリエーション（FR-37）・案件外の仕事と他部署依頼（FR-47/48）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Ops(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, task
        seed.run()
        self.p, self.t, self.store = project, task, store
        self.pid = project.create("u", expand=False, internal_name="検査", flow_type="meire",
                                  launch_date="2026-12-01")["id"]

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def stage(self):
        return self.store.val("SELECT stage FROM project WHERE id=?", (self.pid,))

    def pass_gate(self, g):
        with self.store.tx() as c:
            c.execute("INSERT INTO gate_review (project_id,gate,result,approved_by,approved_at) "
                      "VALUES (?,?,'通過','x',?)", (self.pid, g, self.store.now_s()))

    def test_next_needs_the_gate(self):
        o = self.p.stage_options(dict(self.store.one("SELECT * FROM project WHERE id=?", (self.pid,))))
        self.assertEqual(o["next"], "評価済")
        self.assertIn("G1", o["next_why"])
        with self.assertRaises(ValueError):
            self.p.move_stage(self.pid, "next", "u")
        self.pass_gate("G1")
        self.p.move_stage(self.pid, "next", "u")
        self.assertEqual(self.stage(), "評価済")
        self.p.move_stage(self.pid, "next", "u")              # 候補 は関門なし
        self.assertEqual(self.stage(), "候補")

    def test_hold_resume_abort_need_reasons(self):
        with self.assertRaises(ValueError):
            self.p.move_stage(self.pid, "hold", "u")
        code = self.store.val("SELECT code FROM hold_reason LIMIT 1")
        self.p.move_stage(self.pid, "hold", "u", code)
        self.assertEqual(self.stage(), "保留")
        with self.assertRaises(ValueError):
            self.p.move_stage(self.pid, "next", "u")
        self.p.move_stage(self.pid, "resume", "u")
        self.assertEqual(self.stage(), "起票")
        ab = self.store.val("SELECT code FROM abort_reason LIMIT 1")
        self.p.move_stage(self.pid, "abort", "u", ab)
        self.assertEqual(self.stage(), "中止")
        for a in ("next", "hold", "resume"):
            with self.assertRaises(ValueError):
                self.p.move_stage(self.pid, a, "u", code)

    def test_variants(self):
        r = self.p.save_variant(self.pid, {"label": "iPhone 17"}, "u")
        with self.assertRaises(ValueError):
            self.p.save_variant(self.pid, {"label": "iPhone 17"}, "u")      # 重複
        with self.assertRaises(ValueError):
            self.p.save_variant(self.pid, {"label": "x", "state": "完了"}, "u")
        self.p.save_variant(self.pid, {"id": r["id"], "label": "iPhone 17", "state": "対応中"}, "u")
        self.assertEqual(self.store.val("SELECT state FROM project_variant WHERE id=?", (r["id"],)), "対応中")
        with self.store.tx() as c:
            c.execute("INSERT INTO seisan_registration (project_id,variant_id,state,product_code) "
                      "VALUES (?,?,'登録済','V1')", (self.pid, r["id"]))
        with self.assertRaises(ValueError):
            self.p.delete_variant(self.pid, r["id"], "u")                  # 登録済みは消さない
        r2 = self.p.save_variant(self.pid, {"label": "Pixel"}, "u")
        self.p.delete_variant(self.pid, r2["id"], "u")

    def test_variant_after_project_registration_is_refused(self):
        with self.store.tx() as c:
            c.execute("INSERT INTO seisan_registration (project_id,state,product_code) VALUES (?,'登録済','P1')",
                      (self.pid,))
        with self.assertRaises(ValueError):
            self.p.save_variant(self.pid, {"label": "iPhone 17"}, "u")

    def test_request_closes_only_by_receiver(self):
        with self.assertRaises(ValueError):
            self.t.create_work_item({"kind": "他部署依頼", "title": "撮影"}, "u")   # 依頼先なし
        w = self.t.create_work_item({"kind": "他部署依頼", "title": "撮影", "dept": "Webマーケ"}, "u")
        with self.assertRaises(ValueError):
            self.t.set_status("work_item", w["id"], "完了", "u")
        self.t.accept_work_item(w["id"], "u")
        r = self.store.one("SELECT status, accepted_at FROM work_item WHERE id=?", (w["id"],))
        self.assertEqual(r["status"], "完了")
        self.assertIsNotNone(r["accepted_at"])
        o = self.t.create_work_item({"kind": "案件外", "title": "FBA納品", "hours": "2"}, "u")
        self.t.set_status("work_item", o["id"], "完了", "u")          # 案件外は普通に閉じる
        with self.assertRaises(ValueError):
            self.t.accept_work_item(o["id"], "u")


if __name__ == "__main__":
    unittest.main()
