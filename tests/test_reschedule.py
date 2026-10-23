#!/usr/bin/env python3
"""
発売予定日に合わせてタスクの日程を計算し直す（FR-225・ADR-124・石原さんの依頼書 §2）の検査。カレンダーは呼ばない。

見ているのは:
  - シートの式どおり: 発売日の N 営業日前／後（当日は数えない）、2か月後以降の最初の営業日（その4営業日後）
  - 会社休業日を飛ばす（年末年始）
  - **完了・対象外・固定のタスクは動かさない**
  - 書くのは apply だけ。元に戻せる。反映のあと人が直した行は戻さない
  - 移行分（ルールの無いタスク・開発タイプが空）は名前から引く。引けなければ理由を出す
  - 案件を起こしたときにルールをタスクに持つ
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

HOLI = {"2026-12-29", "2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02", "2027-01-03", "2027-01-04"}


def doc():
    d, closed = dt.date(2026, 1, 1), []
    while d <= dt.date(2028, 3, 31):
        if d.weekday() >= 5 or d.isoformat() in HOLI:
            closed.append(d.isoformat())
        d += dt.timedelta(days=1)
    return {"closed": closed, "covered": [{"from": "2026-01-01", "to": "2028-03-31"}]}


def nth(d: dt.date, n: int, sign: int) -> str:
    k = 0
    while k < n:
        d += dt.timedelta(days=sign)
        if d.weekday() < 5 and d.isoformat() not in HOLI:
            k += 1
    return d.isoformat()


class Reschedule(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, reschedule
        seed.run()
        self.store, self.project, self.m = store, project, reschedule
        self.pid = project.create("u", internal_name="名入れの案件", flow_type="meire", launch_date="2027-01-15")["id"]
        self.doc = doc()

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def task(self, title):
        return self.store.one("SELECT * FROM task WHERE project_id=? AND title LIKE ?", (self.pid, title + "%"))

    def test_rules_kept_on_task_and_sheet_formulas(self):
        t = self.task("競合調査")
        self.assertEqual((t["rule_start"], t["rule_due"], t["rule_src"]), ("L-30", "L-30", "sheet"), "案件を起こしたときにルールを持つ")
        pl = self.m.plan(self.pid, doc=self.doc)
        rows = {r["title"]: r for r in pl["rows"]}
        ld = dt.date(2027, 1, 15)
        self.assertEqual(rows["競合調査"]["new_start"], nth(ld, 30, -1), "発売日の30営業日前（年末年始を飛ばす）")
        self.assertEqual(rows["楽天に出品"]["new_start"], nth(ld, 4, +1), "発売日より後の4営業日目")
        p2 = [r for t, r in rows.items() if t.startswith("生産の問題点")][0]
        self.assertEqual(p2["new_start"], "2027-03-15", "暦で2か月後以降の最初の営業日（3/15 は月曜）")
        self.assertEqual(p2["new_due"], nth(dt.date(2027, 3, 15), 4, +1))
        self.assertIsNone(self.task("競合調査")["start_on"], "plan は書かない")

    def test_apply_skips_done_locked_and_undo(self):
        done, locked = self.task("競合調査"), self.task("ターゲット選定")
        with self.store.tx() as c:
            c.execute("UPDATE task SET status='完了', start_on='2026-11-01', due_on='2026-11-01' WHERE id=?", (done["id"],))
            c.execute("UPDATE task SET date_locked=1, start_on='2026-11-02', due_on='2026-11-02' WHERE id=?", (locked["id"],))
        r = self.m.apply(self.pid, "u", doc=self.doc)
        self.assertGreater(r["changed"], 20)
        self.assertEqual((self.task("競合調査")["start_on"], self.task("ターゲット選定")["start_on"]), ("2026-11-01", "2026-11-02"),
                         "完了と固定は動かさない")
        self.project.set_launch_date(self.pid, "2027-02-15", "延期", "u")
        r2 = self.m.apply(self.pid, "u", "2027-01-15", doc=self.doc)
        self.assertEqual(self.task("ニーズまとめ")["start_on"], nth(dt.date(2027, 2, 15), 30, -1), "延期に合わせて動く")
        self.assertEqual(self.task("競合調査")["start_on"], "2026-11-01", "完了は延期でも動かさない")
        # 反映のあと人が直した行は、元に戻すでも戻さない
        with self.store.tx() as c:
            c.execute("UPDATE task SET due_on='2027-01-20' WHERE id=?", (self.task("差別化")["id"],))
        u = self.m.undo(self.pid, "u")
        self.assertEqual(u["kept"], 1)
        self.assertEqual(self.task("ニーズまとめ")["start_on"], nth(dt.date(2027, 1, 15), 30, -1), "元の日付に戻る")
        self.assertEqual(self.task("差別化")["due_on"], "2027-01-20")
        self.assertEqual(r2["changed"] - 1, u["restored"])

    def test_migrated_tasks_use_names(self):
        pid = self.project.create("u", expand=False, internal_name="移行分", launch_date="2027-01-15")["id"]
        with self.store.tx() as c:
            for i, t in enumerate(["競合調査", "ターゲット選定", "Webdecoシミュレーションデータ提出　※メール件名に商品名も入れる",
                                   "この商品用のスタンプ・背景を検討", "知らない仕事"], 1):
                c.execute("INSERT INTO task (project_id,seq,title,role,status) VALUES (?,?,?,?,'未着手')", (pid, i, t, "admin"))
        pl = self.m.plan(pid, doc=self.doc)
        self.assertEqual((pl["flow"], pl["flow_guessed"]), ("webdeco", True), "開発タイプが空なら名前から推す")
        rows = {r["title"][:6]: r for r in pl["rows"]}
        self.assertEqual(rows["この商品用の"]["new_start"], nth(dt.date(2027, 1, 15), 7, -1))
        self.assertIn("ルールがありません", rows["知らない仕事"]["why"])
        self.m.apply(pid, "u", doc=self.doc)
        self.assertEqual(self.store.val("SELECT rule_start FROM task WHERE project_id=? AND seq=1", (pid,)), "L-30", "反映でルールを持つ")

    def test_calendar_gap_gives_reason(self):
        gap = {"closed": [], "covered": [{"from": "2027-01-01", "to": "2027-01-31"}]}
        pl = self.m.plan(self.pid, doc=gap)
        r = [x for x in pl["rows"] if x["title"] == "競合調査"][0]
        self.assertIn("登録されていません", r["why"])


if __name__ == "__main__":
    unittest.main()
