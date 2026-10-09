#!/usr/bin/env python3
"""
標準タスクのひな形の改訂（ADR-061）の検査。

  - 下書きは使用中の版を写す（⑦は空から）。版は2から。下書きは1つだけ
  - 予備時間の行は写すが、変えられない・消せない
  - 使い始めるには実作業の全行に標準時間が要る。理由が要る
  - 使い始めた後に起こした案件は新しい版、**起こし済みの案件のタスクは変わらない**
  - **再起動（seed.run）しても**使用中の版・決めた係数・ひな形の有無が元に戻らない
  - ひとつ前の版に戻せる（消さない）
  - 変えられるのは 商品開発部・管理者・社長 だけ
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Templates(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, templates, project
        seed.run()
        self.m, self.store, self.seed, self.project = templates, store, seed, project
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id) VALUES ('devdept','dev')")

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def n_tasks(self, pid):
        return [r["title"] for r in self.store.q("SELECT title FROM task WHERE project_id=? ORDER BY seq", (pid,))]

    def test_revise_meire_old_projects_keep_tasks_and_restart_keeps_version(self):
        old = self.project.create("u", expand=True, internal_name="前から", flow_type="meire", launch_date="2026-12-01")["id"]
        before = self.n_tasks(old)
        with self.assertRaises(PermissionError):
            self.m.new_draft("meire", "nobody")
        v = self.m.new_draft("meire", "dev")["version"]
        self.assertEqual(v, 2)
        with self.assertRaises(ValueError):
            self.m.new_draft("meire", "dev")                       # 下書きは1つ
        d = self.m.detail("meire")["draft"]
        reserve = [r for r in d["rows"] if r["kind"] == "予備"]
        work = [r for r in d["rows"] if r["kind"] != "予備"]
        self.assertTrue(reserve, "予備の行も写す")
        with self.assertRaises(ValueError):
            self.m.save_row("meire", {"id": reserve[0]["id"], "title": "x", "role": "admin", "standard_hours": "1"}, "dev")
        self.m.delete_row("meire", work[0]["id"], "dev")
        self.m.save_row("meire", {"title": "新しい確認", "role": "devdept"}, "dev")    # 時間は空欄
        with self.assertRaises(ValueError):
            self.m.activate("meire", "dev", "現場に合わせた")           # 空欄の行がある
        row = next(r for r in self.m.detail("meire")["draft"]["rows"] if r["title"] == "新しい確認")
        self.assertLess(row["seq"], max(r["seq"] for r in self.m.detail("meire")["draft"]["rows"]), "予備の行より前に入る")
        self.m.save_row("meire", {"id": row["id"], "title": "新しい確認", "role": "devdept", "standard_hours": "1.5"}, "dev")
        diff = self.m.detail("meire")["draft"]["diff"]
        self.assertEqual(diff["added"], ["新しい確認"])
        self.assertEqual(diff["removed"], [work[0]["title"]])
        with self.assertRaises(ValueError):
            self.m.activate("meire", "dev", " ")                       # 理由が要る
        self.m.activate("meire", "dev", "現場に合わせた")
        self.seed.run()                                                 # 再起動
        self.assertEqual(self.store.val("SELECT active_template_version FROM flow_type WHERE code='meire'"), 2)
        new = self.project.create("u", expand=True, internal_name="これから", flow_type="meire", launch_date="2027-01-01")["id"]
        self.assertIn("新しい確認", self.n_tasks(new))
        self.assertNotIn(work[0]["title"], self.n_tasks(new))
        self.assertEqual(self.n_tasks(old), before, "起こし済みの案件は変えない")
        self.m.revert("meire", "dev", "戻す")
        self.assertEqual(self.store.val("SELECT active_template_version FROM flow_type WHERE code='meire'"), 1)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM task_template WHERE flow_type='meire' AND template_version=2") > 0, True, "消さない")

    def test_pagerenew_from_empty_and_effort_survive_restart(self):
        self.assertEqual(self.store.val("SELECT has_template FROM flow_type WHERE code='pagerenew'"), 0)
        self.m.new_draft("pagerenew", "dev")
        self.assertEqual(self.m.detail("pagerenew")["draft"]["rows"], [], "⑦は空から")
        self.m.save_row("pagerenew", {"title": "ページ構成", "role": "webmkt", "standard_hours": "3"}, "dev")
        self.m.activate("pagerenew", "dev", "商品開発部の定義")
        self.m.set_effort("material", "1.5", "2027年度の見込み", "dev")
        self.seed.run()
        f = {r["code"]: r for r in self.store.q("SELECT * FROM flow_type")}
        self.assertEqual(f["pagerenew"]["has_template"], 1)
        self.assertEqual(f["material"]["effort_point"], 1.5, "決めた係数は再起動で戻らない")
        with self.assertRaises(ValueError):
            self.m.set_effort("material", "2", "", "dev")


    def test_promote_request_to_template_draft_or_work(self):
        """FR-168。標準タスクに無い作業を、ひな形の下書き（無ければ作る）か案件外の仕事へ。依頼にメモが残る。"""
        from app import automation
        rid = automation.create("dev", title="背景登録", requester="増地さん", dept="商品開発部")["id"]
        with self.assertRaises(PermissionError):
            self.m.promote_to_template(rid, "meire", "devdept", "1", "nobody")
        r = self.m.promote_to_template(rid, "meire", "devdept", "", "dev")
        self.assertTrue(r["draft_created"])
        row = next(x for x in self.m.detail("meire")["draft"]["rows"] if x["title"] == "背景登録")
        self.assertIsNone(row["standard_hours"], "時間は後から入れられる（使い始める前に必須）")
        self.assertEqual(self.store.val("SELECT active_template_version FROM flow_type WHERE code='meire'"), 1,
                         "下書きに入れるだけで、使用中の版は変えない")
        with self.assertRaises(ValueError):
            self.m.promote_to_template(rid, "meire", "devdept", "2", "dev")      # 同じ下書きに二重に入れない
        r2 = self.m.promote_to_template(rid, "freecut", "devdept", "2", "dev")
        self.assertTrue(r2["draft_created"], "別の開発タイプには入れられる")
        w = self.m.promote_to_work(rid, "devdept", "dev")
        self.assertEqual(self.store.val("SELECT kind FROM work_item WHERE id=?", (w["work_item"]["id"],)), "案件外")
        notes = [n["body"] for n in automation.detail(rid)["notes"]]
        self.assertEqual(len(notes), 3)
        self.assertIn("下書き", notes[0])


if __name__ == "__main__":
    unittest.main()
