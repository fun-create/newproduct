#!/usr/bin/env python3
"""R04・R03 の移行（tools/migrate_r04.py・ADR-159）。
発売済・移行補正・未完了は対象外・ラベルは3件だけ登録済み・発売後の仕事を作らない・照合表と合わなければ止める・
2回流しても増えない・目印チャームの新規分へ移す・国旗ピンズを足す。"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def _ledger(path: str, extra: str | None = None) -> None:
    from tools import migrate_r04 as m
    rows = [["", "2025年度（5～4月）"], ["", "7月発売"]]
    names = list(m.ADD) + [n for ns, _l, _b in m.MERGE.values() for n in ns] + list(m.SKIP) + ([extra] if extra else [])
    for n in names:
        rows.append(["", n])
        rows.append([n, "企画", "メンバー", "完了", "", "45900", "1"])
        if n == "シルエットステッカー（テニス男女）":
            rows.append([n, "残りの作業", "メンバー", "未着手", "", "", ""])
    rows += [["", "2026年度（5～4月）"], ["", "5月発売"], ["", "別の案件"], ["別の案件", "企画", "メンバー", "完了", "", "", ""]]
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter="\t").writerows(rows)


class R04(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, gate
        seed.run()
        self.store = store
        self.p_roles = mock.patch.object(gate, "roles_of", side_effect=lambda u: ["admin"] if u == "masateru" else [])
        self.p_roles.start()
        self.dir = tempfile.mkdtemp()
        self.ledger = os.path.join(self.dir, "ledger.tsv")
        _ledger(self.ledger)
        from tools import migrate_r04
        self.m = migrate_r04

    def tearDown(self):
        self.p_roles.stop()
        self.store.close()
        os.unlink(self.dbfile)

    def run_r04(self, apply=True):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.m.r04(self.ledger, "2026-11-19", apply, "masateru")
        return rc, buf.getvalue()

    def test_creates_launched_projects_with_migration_gates(self):
        rc, _ = self.run_r04()
        self.assertEqual(rc, 0)
        ps = self.store.rows(self.store.q("SELECT * FROM project WHERE source_sheet=?", (self.m.SRC,)))
        self.assertEqual(len(ps), len(self.m.ADD) + len(self.m.MERGE) + len(self.m.EXTRA))
        self.assertTrue(all(p["stage"] == "発売済" and p["revenue_counted"] == 1 for p in ps))
        names = {p["internal_name"] for p in ps}
        self.assertNotIn("【改修】バッグ", names)
        self.assertNotIn("夫婦食器の在庫処分", names)
        stamp = next(p for p in ps if p["internal_name"].startswith("スタンプ 浸透印"))
        self.assertEqual(stamp["launch_date"], "2026-03-03")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM task WHERE project_id=?", (stamp["id"],)), 2)
        g = self.store.rows(self.store.q("SELECT DISTINCT kind, approved_role FROM gate_review WHERE project_id=?", (stamp["id"],)))
        self.assertEqual(g, [{"kind": "migration", "approved_role": "移行補正"}])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM work_item"), 0)          # 発売後の仕事は作らない

    def test_open_tasks_become_out_of_scope_and_done_keep_dates(self):
        self.run_r04()
        pid = self.store.val("SELECT id FROM project WHERE internal_name='シルエットステッカー（テニス男女）'")
        ts = self.store.rows(self.store.q("SELECT title, status, due_on, done_at FROM task WHERE project_id=? ORDER BY seq", (pid,)))
        self.assertEqual([t["status"] for t in ts], ["完了", "対象外"])
        self.assertEqual(ts[0]["due_on"], "2025-08-31")
        self.assertEqual(ts[0]["done_at"], "2025-08-31")

    def test_only_three_registered_rest_pending(self):
        import json
        self.run_r04()
        regs = self.store.rows(self.store.q(
            "SELECT p.internal_name, g.state, g.labels FROM newprod_reg g JOIN project p ON p.id=g.project_id"))
        done = {r["internal_name"]: json.loads(r["labels"]) for r in regs if r["state"] == "登録済み"}
        self.assertEqual(set(done), {"お名前シール 衣類用", "ブロックメモ", "等身大アクスタ"})
        self.assertNotIn("LOVOT ブロックメモ", done["ブロックメモ"])
        self.assertEqual(len(regs), len(self.m.ADD) + len(self.m.MERGE) + len(self.m.EXTRA))

    def test_twice_adds_nothing(self):
        self.run_r04()
        n = self.store.val("SELECT COUNT(*) FROM task")
        rc, out = self.run_r04()
        self.assertEqual(rc, 0)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM task"), n)
        self.assertIn("案件 0 件", out)

    def test_unknown_sheet_project_stops(self):
        _ledger(self.ledger, extra="照合表に無い新しい案件")
        rc, out = self.run_r04()
        self.assertEqual(rc, 1)
        self.assertIn("照合表に無い新しい案件", out)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM project"), 0)

    def test_dry_run_writes_nothing(self):
        rc, _ = self.run_r04(apply=False)
        self.assertEqual(rc, 0)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM project"), 0)


class R03(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, gate, newprod
        seed.run()
        self.store = store
        self.p_roles = mock.patch.object(gate, "roles_of", side_effect=lambda u: ["admin"] if u == "masateru" else [])
        self.p_roles.start()
        from tools import migrate_r04 as m
        self.m = m
        now = "2026-10-18 10:16:12"
        store.ex("INSERT INTO project (id, internal_name, stage, launch_date, revenue_counted, revenue_basis, source_of_truth, created_at) "
                 "VALUES ('f0bb','目印チャームに移行（リニューアル分）','発売済','2026-09-18',1,'全額','app',?)", (now,))
        store.ex("INSERT INTO project (id, internal_name, stage, launch_date, flow_type, revenue_counted, revenue_basis, source_of_truth, created_at) "
                 "VALUES ('8afa','ユニフォーム型・長方形ピンズ','発売済','2026-07-17','meire',1,'全額','app',?)", (now,))
        for tid, head in m.MOVE + [(20, "簡単オーダー 目印チャーム　販売開始・全体に周知"), (331, "リニューアルページの依頼")]:
            store.ex("INSERT INTO task (id, project_id, seq, title, status, created_at) VALUES (?, 'f0bb', ?, ?, '完了', ?)",
                     (tid, tid, head + "（続き）", now))
        store.ex("INSERT INTO plan_version (id, fiscal_year, label, state, created_at) VALUES ('d542',2026,'2026年度','承認済',?)", (now,))
        store.ex("INSERT INTO plan_slot (id, version_id, launch_month, product) VALUES ('c008','d542','2026-10',?)", (m.NEW_NAME,))
        store.conn().commit()
        newprod.register("8afa", ["ユニフォーム型・長方形ピンズ"], "x")

    def tearDown(self):
        self.p_roles.stop()
        self.store.close()
        os.unlink(self.dbfile)

    def run_r03(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.m.r03(True, "masateru")
        return rc, buf.getvalue()

    def test_moves_tasks_and_links(self):
        import json
        rc, _ = self.run_r03()
        self.assertEqual(rc, 0)
        pid = self.store.val("SELECT id FROM project WHERE internal_name=?", (self.m.NEW_NAME,))
        moved = [r["id"] for r in self.store.q("SELECT id FROM task WHERE project_id=? ORDER BY seq", (pid,))]
        self.assertEqual(moved, [t for t, _ in self.m.MOVE])
        left = {r["id"] for r in self.store.q("SELECT id FROM task WHERE project_id='f0bb'")}
        self.assertEqual(left, {20, 331})                           # 簡単オーダーとリニューアル分は残す
        self.assertEqual(self.store.val("SELECT project_id FROM plan_slot WHERE id='c008'"), pid)
        reg = self.store.one("SELECT state, labels FROM newprod_reg WHERE project_id=?", (pid,))
        self.assertEqual((reg["state"], json.loads(reg["labels"])), ("登録済み", self.m.NEW_LABELS))
        pins = json.loads(self.store.val("SELECT labels FROM newprod_reg WHERE project_id='8afa'"))
        self.assertEqual(pins, ["ユニフォーム型・長方形ピンズ", "国旗ピンズ"])
        self.assertEqual(self.store.val("SELECT launch_date FROM project WHERE id=?", (pid,)), "2026-10-15")

    def test_twice_is_quiet(self):
        self.run_r03()
        n = self.store.val("SELECT COUNT(*) FROM project_revision")
        rc, _ = self.run_r03()
        self.assertEqual(rc, 0)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM project_revision"), n)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM project WHERE internal_name=?", (self.m.NEW_NAME,)), 1)

    def test_wrong_task_stops(self):
        self.store.ex("UPDATE task SET title='ちがう名前' WHERE id=21")
        self.store.conn().commit()
        rc, out = self.run_r03()
        self.assertEqual(rc, 1)
        self.assertIn("★", out)
        self.assertIsNone(self.store.val("SELECT id FROM project WHERE internal_name=?", (self.m.NEW_NAME,)))


    def test_kantan_separate_project(self):
        import json
        self.run_r03()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.store.ex("INSERT INTO task (id, project_id, seq, title, status, created_at) VALUES "
                          "(341, 'f0bb', 18, '簡単オーダー 目印チャーム　アプリ実装可否を確認、発売リプラン', '対象外', 'x')")
            self.store.ex("UPDATE task SET title='簡単オーダー 目印チャーム　販売開始・全体に周知', status='対象外' WHERE id=20")
            self.store.conn().commit()
            rc = self.m.kantan(True, "masateru")
            rc2 = self.m.kantan(True, "masateru")
        self.assertEqual((rc, rc2), (0, 0))
        pid = self.store.val("SELECT id FROM project WHERE internal_name=?", (self.m.KANTAN,))
        p = self.store.one("SELECT stage, launch_date FROM project WHERE id=?", (pid,))
        self.assertEqual((p["stage"], p["launch_date"]), ("開発中", "2026-12-04"))
        ts = self.store.rows(self.store.q("SELECT id, status, due_on FROM task WHERE project_id=? ORDER BY seq", (pid,)))
        self.assertEqual(ts, [{"id": 341, "status": "未着手", "due_on": "2026-11-27"},
                              {"id": 20, "status": "未着手", "due_on": "2026-12-04"}])
        self.assertEqual(json.loads(self.store.val("SELECT labels FROM newprod_reg WHERE project_id=?", (pid,))), self.m.KANTAN_LABELS)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM project WHERE internal_name=?", (self.m.KANTAN,)), 1)
        self.assertIsNone(self.store.val("SELECT id FROM plan_slot WHERE project_id=?", (pid,)))

    def test_kantan_label_clash_stops(self):
        from app import newprod
        newprod.register("f0bb", ["目印チャーム", "フルオーダー 目印チャーム【剣道】"], "x")
        self.store.ex("UPDATE task SET title='簡単オーダー 目印チャーム　販売開始・全体に周知' WHERE id=20")
        self.store.ex("INSERT INTO task (id, project_id, seq, title, status, created_at) VALUES "
                      "(341, 'f0bb', 18, '簡単オーダー 目印チャーム　アプリ実装可否を確認', '対象外', 'x')")
        self.store.conn().commit()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.m.kantan(True, "masateru")
        self.assertEqual(rc, 1)
        self.assertIsNone(self.store.val("SELECT id FROM project WHERE internal_name=?", (self.m.KANTAN,)))

    def test_labels_add_new_names_keep_old(self):
        import json
        self.store.ex("UPDATE task SET title='簡単オーダー 目印チャーム　販売開始・全体に周知' WHERE id=20")
        self.store.ex("INSERT INTO task (id, project_id, seq, title, status, created_at) VALUES "
                      "(341, 'f0bb', 18, '簡単オーダー 目印チャーム　アプリ実装可否を確認', '対象外', 'x')")
        self.store.conn().commit()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.m.kantan(True, "masateru")
            rc_missing = self.m.labels(True, "masateru")            # 2025年度の案件がまだ無い → 止める
        self.assertEqual(rc_missing, 1)
        from app import project as pm
        for name, _ in self.m.LABELS_1119:
            if not self.store.val("SELECT id FROM project WHERE internal_name=?", (name,)):
                pm.create("x", expand=False, internal_name=name, stage="発売済", launch_date="2026-03-03")
        with redirect_stdout(buf):
            self.assertEqual(self.m.labels(True, "masateru"), 0)
            self.assertEqual(self.m.labels(True, "masateru"), 0)    # 2回目は何も足さない
        kid = self.store.val("SELECT id FROM project WHERE internal_name=?", (self.m.KANTAN,))
        ls = json.loads(self.store.val("SELECT labels FROM newprod_reg WHERE project_id=?", (kid,)))
        self.assertEqual(ls[:8], self.m.KANTAN_LABELS)                # 古い名前は残す
        self.assertIn("簡単オーダー 目印チャーム【剣道】", ls)
        self.assertEqual(len(ls), 16)

if __name__ == "__main__":
    unittest.main()
