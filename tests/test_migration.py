#!/usr/bin/env python3
"""
進行中の案件と年間プランの取り込み（F-4-8・F-5・F-3 ／ 2026-09-26 十文字さんの選択A）の検査。
**外部通信をしない。**

見ているのは「**推測で埋めていないか**」と「**二重に入らないか**」。

  - 年間プラン: 26枠・全件に発売日／注意あり0件／2回流しても増えない
  - 年間プラン: セル内の `\\n`（文字列）とゼロ幅スペースで壊れないこと（2026-09-26 に実際に壊れた）
  - 年間プラン: LOVOT の3行は「エリア列に商品名」を商品として読むこと
  - 進捗管理: **タスクが1件も無い見出し（発売済みの記録39件）を入れない**
  - 進捗管理: 日付の年は**年度見出しから**（1〜4月は翌年）。推測ではない
  - 進捗管理: **SE部は役割マスタに無いので NULL**（発明しない）
  - 進捗管理: 段階は**発売月と今日の比較**で決まり、今日を固定すれば再現する
  - 進捗管理: **雛形のタスクを展開しない**（完了137と雛形の未着手が二重に並ぶため）
  - 進捗管理: 年間プランの枠と**自動で結びつけない**（候補を出すだけ）
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, BASE / "tools" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-09-26"
        cls.dbfile = p
        from app import store
        store.close()
        from app import seed
        seed.run()
        cls.plan = _tool("import_plan2026")
        cls.ledger = _tool("import_ledger")

    @classmethod
    def tearDownClass(cls):
        from app import store
        store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(cls.dbfile + s)
            except OSError:
                pass


class TestPlan2026(Base):
    def test_26_slots_all_dated_one_real_anomaly_flagged(self):
        """元表には**本物の食い違いが1つ**ある: 新型iPhoneスキンシールは「11月発売」の
        見出しの下にあるのに発売予定日が 10/30。**推測で直さず、注意を付けて残す。**"""
        items = self.plan.parse(self.plan.read_rows())
        self.assertEqual(len(items), 26)
        self.assertTrue(all(e["launch"] for e in items), "全件に発売日があること")
        flagged = [e for e in items if e["flags"]]
        self.assertEqual(len(flagged), 1)
        self.assertIn("iPhone", flagged[0]["product"])
        self.assertEqual(flagged[0]["launch"], "2026-10-30", "日付は元表の m/d のまま")
        self.assertTrue(any("見出しの月" in f for f in flagged[0]["flags"]))

    def test_cell_noise_is_stripped(self):
        """書き出しは改行を文字列 `\\n` で持ち、ゼロ幅スペースも混じる。**両方落とす。**"""
        r = ["", "2026年\\n5月", "​", "x"]
        self.assertEqual(self.plan._g(r, 1), "2026年 5月")
        self.assertEqual(self.plan._g(r, 2), "")

    def test_lovot_rows_are_read_by_vocabulary(self):
        """LOVOT の行は作成エリアが「アイロン」「UV」。**語彙で読むので列がずれても壊れない。**"""
        items = self.plan.parse(self.plan.read_rows())
        lovot = [e for e in items if "LOVOT" in (e["product"] or "")]
        self.assertGreaterEqual(len(lovot), 2)
        for e in lovot:
            self.assertIn(e["area"], self.plan.AREAS)
            self.assertTrue(e["product"])
        self.assertTrue(all(e["product"] for e in items), "商品名が空の枠が無いこと")

    def test_kind_follows_adr030(self):
        items = self.plan.parse(self.plan.read_rows())
        kinds = [self.plan.kind_of(e) for e in items]
        self.assertEqual(kinds.count("uchiwa"), 3)
        self.assertEqual(kinds.count("pagerenew"), 9)
        self.assertEqual(kinds.count("original"), 14)

    def test_apply_is_idempotent(self):
        from app import store
        a = self.plan.apply()
        b = self.plan.apply()
        self.assertEqual(a["created"], 26)
        self.assertEqual(b["created"], 0)
        self.assertEqual(b["kept"], 26)
        self.assertEqual(store.val("SELECT COUNT(*) FROM plan_slot WHERE source_sheet IS NOT NULL"), 26)
        self.assertEqual(store.val("SELECT COUNT(*) FROM plan_version WHERE fiscal_year=2026"), 1)
        self.assertEqual(store.val("SELECT state FROM plan_version WHERE fiscal_year=2026"), "策定中",
                         "承認は画面で人が行う")
        self.assertEqual(store.val("SELECT COUNT(*) FROM plan_slot WHERE effort_point IS NULL"), 0,
                         "工数ポイントは開発タイプの係数から入る（元表の月合計は使わない）")


class TestLedger(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.plan.apply()
        cls.d = cls.ledger.parse(cls.ledger.read_rows())

    def test_task_less_headings_are_not_imported(self):
        gs = [g for g in self.d["groups"] if not g["outside"]]
        keep = [g for g in gs if self.ledger.importable(g)]
        skip = [g for g in gs if not self.ledger.importable(g)]
        self.assertEqual(len(keep), 20)
        self.assertEqual(len(skip), 39, "発売済みの記録（タスク無し）は入れない")
        self.assertTrue(all(len(g["tasks"]) == 0 for g in skip))

    def test_year_comes_from_the_fiscal_header_not_a_guess(self):
        self.assertEqual(self.ledger._date("8月17日", 2025), "2025-08-17")
        self.assertEqual(self.ledger._date("1月15日", 2025), "2026-01-15", "1〜4月は翌年")
        self.assertIsNone(self.ledger._date("8月17日", None), "年度が無ければ日付を作らない")
        self.assertIsNone(self.ledger._date("期限", 2025))

    def test_unknown_role_stays_null(self):
        allt = [t for g in self.d["groups"] for t in g["tasks"]]
        se = [t for t in allt if t["role_raw"] == "SE部"]
        self.assertEqual(len(se), 1)
        self.assertIsNone(se[0]["role"], "役割マスタに無いものを発明しない")

    def test_status_and_role_vocab(self):
        allt = [t for g in self.d["groups"] for t in g["tasks"]]
        self.assertEqual(len(allt), 384)
        st = [t["status"] for t in allt]
        self.assertEqual((st.count("未着手"), st.count("完了"), st.count("着手")), (244, 137, 3))
        self.assertTrue(all(t["status"] in ("未着手", "着手", "完了") for t in allt))

    def test_stage_is_derived_from_launch_month_and_today(self):
        by = {g["name"]: g for g in self.d["groups"]}
        self.assertEqual(self.ledger.stage_of(by["等身大パネル"]), "発売済")        # 2026-05
        self.assertEqual(self.ledger.stage_of(by["ウォールステッカー"]), "開発中")   # 2026-09 = 今月
        self.ledger.TODAY_YM = "2026-04"
        try:
            self.assertEqual(self.ledger.stage_of(by["等身大パネル"]), "開発中", "今日を変えれば変わる")
        finally:
            self.ledger.TODAY_YM = None

    def test_apply_is_idempotent_and_does_not_expand_templates(self):
        from app import store
        a = self.ledger.apply()
        b = self.ledger.apply()
        self.assertEqual((a["project"], a["task"], a["work_item"]), (20, 310, 74))
        self.assertEqual((b["project"], b["task"], b["work_item"]), (0, 0, 0))
        self.assertEqual(store.val("SELECT COUNT(*) FROM project"), 20)
        self.assertEqual(store.val("SELECT COUNT(*) FROM task"), 310)
        self.assertEqual(store.val("SELECT COUNT(*) FROM task WHERE template_id IS NOT NULL"), 0,
                         "雛形のタスクを展開していないこと")
        self.assertEqual(store.val("SELECT COUNT(*) FROM task WHERE status='完了' AND done_at IS NULL"), 0)
        self.assertEqual(store.val("SELECT COUNT(*) FROM project WHERE stage='発売済'"), 2)
        self.assertEqual(store.val("SELECT COUNT(*) FROM work_item WHERE kind='案件外'"), 74)

    def test_slots_are_suggested_but_never_linked(self):
        from app import store
        self.ledger.apply()
        cands = dict(self.ledger.plan_candidates(self.d["groups"]))
        self.assertTrue(cands["等身大パネル"], "同名の枠は候補に出る")
        self.assertTrue(cands["うちの子 足形 アクスタスタンプ"], "名前が揺れていても候補に出る")
        self.assertEqual(store.val("SELECT COUNT(*) FROM plan_slot WHERE project_id IS NOT NULL"), 0,
                         "**自動では結びつけない**")


if __name__ == "__main__":
    unittest.main()
