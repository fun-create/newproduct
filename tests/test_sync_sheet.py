#!/usr/bin/env python3
"""
進捗管理シート → アプリの同期（ADR-105）の検査。

見ているのは:
  - Excel の日付（通し番号）を年まで正しく読む（前回の取り込みは「2月2日」を翌年と読んでいた）
  - 見出し（年度・発売月・商品発売以外）でグループを分け、同じ名前のタスクは何番目かで結ぶ
  - 進捗・期限を直す、増えた行を足す、無い案件を作る、名前の付け替え、発売予定日とステージ
  - **2回流しても変わらない**
  - **同期の後にアプリで直された行は上書きしない**（衝突として数える）
  - シートから消えた行は「対象外」にする（完了済み・アプリで足した行は触らない）
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

D = dt.date(1899, 12, 30)


def ser(iso: str) -> str:
    return str((dt.date.fromisoformat(iso) - D).days)


def ledger(rows_extra=()) -> list[list[str]]:
    rows = [["", "タスク", "担当者", "進捗状況", "開始", "期限", "所要時間"],
            ["", "2026年度（5～4月）"],
            ["", "9月発売"],
            ["", "ウォールステッカー", "", "", "", "", "", "←終了したらグループを折り畳む"],
            ["ウォールステッカー", "競合調査", "管理者", "完了", ser("2026-03-05"), ser("2026-03-05"), "1"],
            ["ウォールステッカー", "販売開始", "メンバー", "完了", ser("2026-09-29"), ser("2026-09-29"), "0.25"],
            ["", "11月発売"],
            ["", "ウェルカムボックス", "", "", "", "", "", "←終了したらグループを折り畳む"],
            ["ウェルカムボックス", "商品ページチェック・修正依頼", "管理者", "未着手", ser("2026-11-20"), ser("2026-11-20"), "0.25"],
            ["ウェルカムボックス", "商品ページチェック・修正依頼", "管理者", "未着手", ser("2026-11-21"), ser("2026-11-21"), "0.25"],
            ["", "新しい案件Z", "", "", "", "", "", "←終了したらグループを折り畳む"],
            ["新しい案件Z", "試作・調整", "メンバー", "取組み中", "", "", "2"],
            ["", "商品発売以外のタスク"],
            ["", "Amazon"],
            ["Amazon", "FBA納品", "管理者", "完了", ser("2026-06-04"), ser("2026-06-04"), "0.5"]]
    return rows + list(rows_extra)


PLAN = [["", "2026年\\n9月", "", "", "", "ウォールステッカー", "アイコス", "フリーカット", "", ser("2026-04-02"), "TRUE", ser("2026-09-29"), "FALSE"],
        ["", "2026年\\n11月", "", "", "", "ウェルカムボックス", "横並び・その他", "Web deco", "", ser("2026-06-01"), "TRUE", ser("2026-11-27"), "FALSE"],
        ["", "", "", "", "", "新しい案件Z", "グッズ", "名入れ", "", "", "", ser("2026-11-10"), "FALSE"]]


class SyncSheet(unittest.TestCase):
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
        import tools.sync_sheet as S
        self.S, self.store = S, store
        # 前回の取り込みの状態を作る（年を1年ずれて読んでいた・名前が少し違う・シートから消えた行がある）
        def mk(name, tasks):
            pid = project.create("import", expand=False, internal_name=name, stage="開発中")["id"]
            store.ex("UPDATE project SET source_sheet=?, source_key=? WHERE id=?", (S.SHEET, name, pid))
            for i, (title, status, due) in enumerate(tasks, 1):
                store.ex("INSERT INTO task (project_id,seq,title,role,due_on,status,created_at,kind,source_row) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", (pid, i, title, "admin", due, status, store.now_s(), "実作業", 100 + i))
            store.conn().commit()
            return pid
        self.wall = mk("ウォールステッカー", [("競合調査", "完了", "2027-03-05"), ("販売開始", "未着手", None),
                                          ("データ入稿テンプレート作成", "未着手", None)])
        self.wel = mk("ウェルカムBOX", [("商品ページチェック・修正依頼", "未着手", None)])

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def run_sync(self, rows=None, renames=None):
        S = self.S
        g = S.parse_ledger(rows or ledger())
        rn = {"ウェルカムBOX": "ウェルカムボックス"} if renames is None else renames
        return S.apply(g, S.parse_plan(PLAN), rn, dt.date(2026, 10, 18), "2026-10-18", "tester")

    def task(self, pid, title, nth=1):
        rs = self.store.q("SELECT * FROM task WHERE project_id=? AND title=? ORDER BY seq", (pid, title))
        return dict(rs[nth - 1])

    def test_serial_and_parse(self):
        self.assertEqual(self.S.serial_date("46294.0"), "2026-09-29")
        self.assertIsNone(self.S.serial_date("8月17日"), "テキストの日付は推測しない")
        gs = self.S.parse_ledger(ledger())
        names = [(g["name"], g["month"], g["outside"]) for g in gs]
        self.assertIn(("ウォールステッカー", 9, False), names)
        self.assertIn(("Amazon", None, True), names)
        wel = next(g for g in gs if g["name"] == "ウェルカムボックス")
        self.assertEqual([t["nth"] for t in wel["tasks"]], [1, 2], "同じ名前のタスクは何番目かで分ける")
        self.assertEqual(self.S.parse_plan(PLAN)[0]["launch"], "2026-09-29")

    def test_apply_aligns_and_is_idempotent(self):
        n = self.run_sync()
        self.assertEqual(n["renamed"], 1)
        self.assertEqual(n["projects_created"], 1)
        self.assertEqual(n["conflicts"], 0)
        # 年のずれが直る・進捗が直る
        self.assertEqual(self.task(self.wall, "競合調査")["due_on"], "2026-03-05")
        self.assertEqual(self.task(self.wall, "販売開始")["status"], "完了")
        # シートから消えた行は対象外（完了は触らない）
        self.assertEqual(self.task(self.wall, "データ入稿テンプレート作成")["status"], "対象外")
        # 発売予定日とステージ
        w = dict(self.store.one("SELECT * FROM project WHERE id=?", (self.wall,)))
        self.assertEqual((w["launch_date"], w["stage"]), ("2026-09-29", "発売済"))
        b = dict(self.store.one("SELECT * FROM project WHERE id=?", (self.wel,)))
        self.assertEqual((b["internal_name"], b["source_key"], b["launch_date"], b["stage"]),
                         ("ウェルカムボックス", "ウェルカムボックス", "2026-11-27", "開発中"))
        self.assertEqual(self.task(self.wel, "商品ページチェック・修正依頼", 2)["due_on"], "2026-11-21", "2番目は足す")
        z = dict(self.store.one("SELECT * FROM project WHERE internal_name='新しい案件Z'"))
        self.assertEqual((z["flow_type"], z["launch_date"], z["stage"]), ("meire", "2026-11-10", "開発中"))
        self.assertEqual(self.task(z["id"], "試作・調整")["status"], "着手")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM work_item WHERE title='FBA納品'"), 1)
        # 2回目は何も変わらない
        n2 = self.run_sync(renames={})
        n3 = self.run_sync()                              # 同じ --rename をもう一度付けて流しても増えない
        self.assertEqual((n3["projects_created"], n3["tasks_added"]), (0, 0))
        self.assertEqual((n2["tasks_updated"], n2["tasks_added"], n2["projects_created"], n2["work_added"], n2["tasks_retired"]),
                         (0, 0, 0, 0, 0))

    def test_app_side_edit_is_not_overwritten(self):
        self.run_sync()
        t = self.task(self.wel, "商品ページチェック・修正依頼")
        self.store.ex("UPDATE task SET status='完了' WHERE id=?", (t["id"],))   # アプリで人が完了にした
        self.store.conn().commit()
        rows = ledger()
        rows[8] = rows[8][:3] + ["未着手", ser("2026-11-25"), ser("2026-11-25"), "0.25"]   # シートでは期限が動いた
        n = self.run_sync(rows=rows, renames={})
        self.assertEqual(n["conflicts"], 1)
        self.assertEqual(self.task(self.wel, "商品ページチェック・修正依頼")["status"], "完了", "アプリで直した値を残す")
        rep = self.S.report(self.S.parse_ledger(rows), self.S.parse_plan(PLAN), {}, dt.date(2026, 10, 18))
        self.assertIn("★衝突", rep)

    def test_stage_is_not_touched_when_held(self):
        self.store.ex("UPDATE project SET stage='保留' WHERE id=?", (self.wall,))
        self.store.conn().commit()
        self.run_sync()
        self.assertEqual(self.store.val("SELECT stage FROM project WHERE id=?", (self.wall,)), "保留")


if __name__ == "__main__":
    unittest.main()
