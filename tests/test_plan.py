#!/usr/bin/env python3
"""
年間プランの枠（F-3 ／ FR-82〜FR-86）の検査。**外部通信をしない。**

    python3 -m unittest discover -s tests -v
    python3 tests/test_plan.py

見ているのは、要件が「これを直す」と言った所そのもの。

  - **警告は保存を止めない**（F-3-3 ／ FR-84）。ルール違反のまま保存できること
  - **承認済みは年度に1つだけ**（F-3-4 ／ FR-85）。アプリだけでなく **DB が縛る**こと
  - **数えられないルールを OK と言わない**（N-10）。連休ルールが `unavailable` を返すこと
  - **工数ポイント未確定を 0 として足さない**こと（足すと軽い月に見える）
  - **ページリニューアルを発売本数に数えない**こと（F-10-11）。
    逆に**商品タイプ未設定は数える**こと（選び忘れが静かに消えないため）
  - **枠→案件は1操作**で、タスク設定期限を**発売の2か月前**に割り戻すこと（FR-86）。
    日が未定の枠は**その月の1日**で逆算すること（月末だと最大30日うしろへずれる）
  - 変換済みの枠を**二度変換できない**こと
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

TODAY = "2026-09-22"


class Base(unittest.TestCase):
    """**検査ごとに空のDB。**枠は積み上がるので、共有すると数のルールが互いに汚す。"""

    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = TODAY
        self.dbfile = p
        from app import store
        store.close()
        from app import seed
        seed.run()
        from app import plan
        self.m = plan
        self.vid = plan.create_version("tester", 2026)["id"]

    def tearDown(self):
        from app import store
        store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + suffix)
            except OSError:
                pass

    def slot(self, month="2026-05", kind="original", **f):
        return self.m.create_slot("tester", version_id=self.vid,
                                  launch_month=month, product_kind=kind, **f)["id"]

    def rule(self, rule, scope):
        for r in self.m.check(self.vid)["results"]:
            if r["rule"] == rule and r["scope"] == scope:
                return r
        return None


# ══════════════════════════════════════════════════════════
class TestMasters(Base):
    def test_product_kind_is_seeded_with_pagerenew_excluded_from_counts(self):
        """**ページリニューアルを正式な商品タイプとして持つ**（F-3-8）。

        本数には数えない（F-10-11）。計画の38%を占めるので、
        混ぜると「月3商品」が達成に見える。
        """
        k = {r["code"]: r for r in self.m.kinds()}
        self.assertIn("pagerenew", k)
        self.assertEqual(k["pagerenew"]["counts_as_launch"], 0)
        self.assertIsNone(k["pagerenew"]["ratio_group"], "比率の対象外であること")
        self.assertEqual(k["pagerenew"]["default_flow"], "pagerenew")
        self.assertEqual(k["original"]["ratio_group"], "original")
        self.assertEqual(k["uchiwa"]["ratio_group"], "uchiwa")

    def test_seed_does_not_overwrite_a_hand_changed_ratio_group(self):
        """**括りは商品開発部が決める。**付け替えたものを種で戻さない。"""
        from app import seed, store
        store.ex("UPDATE product_kind SET ratio_group='uchiwa' WHERE code='original'")
        store.conn().commit()
        seed.run()
        self.assertEqual(
            store.val("SELECT ratio_group FROM product_kind WHERE code='original'"),
            "uchiwa")

    def test_effort_point_comes_from_the_flow_and_stays_null_when_unmeasured(self):
        """⑤資材リニューアルは係数が未実測。**0 で埋めない**（NULL のまま）。"""
        from app import store
        sid = self.slot(kind="other", flow_type="meire")
        self.assertEqual(store.val("SELECT effort_point FROM plan_slot WHERE id=?",
                                   (sid,)), 5.5)
        self.assertEqual(store.val("SELECT effort_src FROM plan_slot WHERE id=?",
                                   (sid,)), "flow_type")
        sid2 = self.slot(kind="other", flow_type="material")
        self.assertIsNone(store.val("SELECT effort_point FROM plan_slot WHERE id=?",
                                    (sid2,)))
        self.assertIsNone(store.val("SELECT effort_src FROM plan_slot WHERE id=?",
                                    (sid2,)))


# ══════════════════════════════════════════════════════════
class TestVersions(Base):
    def test_only_one_approved_version_per_fiscal_year_enforced_by_the_database(self):
        """**FR-85。**アプリ側のチェックだけだと、手作業の INSERT が素通りする。"""
        from app import store
        other = self.m.create_version("tester", 2026, "もう1案")["id"]
        self.m.approve(self.vid, "tester")
        r = self.m.approve(other, "tester")
        self.assertEqual(r["superseded"], self.vid)
        self.assertEqual(store.val("SELECT state FROM plan_version WHERE id=?",
                                   (self.vid,)), "失効")
        # DB が縛っていること（アプリを通さない経路）
        third = self.m.create_version("tester", 2026, "3案目")["id"]
        with self.assertRaises(sqlite3.IntegrityError):
            store.ex("UPDATE plan_version SET state='承認済' WHERE id=?", (third,))
        store.conn().rollback()

    def test_approved_version_cannot_be_edited(self):
        self.slot()
        self.m.approve(self.vid, "tester")
        with self.assertRaises(ValueError):
            self.slot(month="2026-06")

    def test_revise_copies_slots_but_not_the_conversion(self):
        """改訂版は枠ごと写すが、**変換済みの案件は写さない**（二重に作らない）。"""
        from app import store
        a = self.slot(month="2026-05", launch_date="2026-05-20")
        self.slot(month="2026-06")
        self.m.convert(a, "tester")
        self.m.approve(self.vid, "tester")
        new = self.m.revise(self.vid, "tester")
        self.assertEqual(new["slots_copied"], 2)
        self.assertEqual(new["state"], "策定中")
        self.assertEqual(
            store.val("SELECT COUNT(*) FROM plan_slot WHERE version_id=? "
                      "AND project_id IS NOT NULL", (new["id"],)), 0)
        self.assertEqual(store.val("SELECT based_on FROM plan_version WHERE id=?",
                                   (new["id"],)), self.vid)


# ══════════════════════════════════════════════════════════
class TestRules(Base):
    def test_a_violating_slot_still_saves(self):
        """**FR-84。**警告は出すが、保存は止めない。止めると表計算に戻る。"""
        for i in range(9):
            self.slot(month="2026-05", kind="original")
        self.assertEqual(self.rule("count", "2026-05")["level"], "warn")
        self.assertEqual(self.rule("effort", "2026-05")["level"], "warn")
        from app import store
        self.assertEqual(store.val("SELECT COUNT(*) FROM plan_slot"), 9)

    def test_ratio_uses_a_one_slot_tolerance(self):
        for _ in range(3):
            self.slot(kind="original")
        self.slot(kind="uchiwa")
        r = self.rule("ratio", "FY")
        self.assertEqual(r["level"], "ok")
        self.assertEqual((r["original"], r["uchiwa"]), (3, 1))
        # 7本 : 1本（計8本）でも、目標 2.0本 との差は 1.0 なので**許容内**。
        # 年間26枠で 3:1 は割り切れず、端数のたびに警告が出ると誰も読まなくなる
        for _ in range(4):
            self.slot(kind="original")
        self.assertEqual(self.rule("ratio", "FY")["level"], "ok")
        # 11本 : 1本（計12本）は目標 3.0本 との差が 2.0 → 警告
        for _ in range(4):
            self.slot(kind="original")
        r = self.rule("ratio", "FY")
        self.assertEqual(r["level"], "warn")
        self.assertEqual((r["original"], r["uchiwa"], r["target_uchiwa"]),
                         (11, 1, 3.0))

    def test_ratio_is_unavailable_not_ok_when_no_kind_is_set(self):
        self.slot(kind=None)
        self.slot(kind=None)
        r = self.rule("ratio", "FY")
        self.assertEqual(r["level"], "unavailable")
        self.assertEqual(r["excluded"], 2)

    def test_unknown_effort_points_are_not_counted_as_zero(self):
        """**未確定を 0 として足さない。**足すと「軽い月」に見えて枠を足してしまう。"""
        self.slot(kind="other", flow_type="meire")      # 5.5
        self.slot(kind="other", flow_type="meire")      # 5.5 → 合計 11.0（10〜15）
        self.assertEqual(self.rule("effort", "2026-05")["level"], "ok")
        self.slot(kind="other", flow_type="material")   # 係数 NULL
        r = self.rule("effort", "2026-05")
        self.assertEqual(r["total"], 11.0, "NULL を 0 として足していないこと")
        self.assertEqual(r["unknown"], 1)
        self.assertEqual(r["level"], "warn", "未確定があるなら OK と言わないこと")
        self.assertIn("実際はこれより重くなります", r["message"])

    def test_pagerenew_is_not_counted_but_a_missing_kind_is(self):
        """ページリニューアルは数えない（F-10-11）。**タイプ未設定は数える。**"""
        for _ in range(3):
            self.slot(kind="original")
        self.slot(kind="pagerenew")
        self.assertEqual(self.rule("count", "2026-05")["level"], "ok")
        self.assertEqual(self.rule("count", "2026-05")["skipped"], 1)
        self.slot(kind=None)
        r = self.rule("count", "2026-05")
        self.assertEqual(r["n"], 4, "商品タイプ未設定の枠が静かに消えないこと")
        self.assertEqual(r["level"], "warn")

    def test_holiday_rule_reports_unavailable_instead_of_passing(self):
        """**N-10。**正本（Calendar の会社休業日）が未連携。推測で 1・5・8月と置かない。"""
        self.slot(month="2026-05")
        r = self.rule("holiday", "FY")
        self.assertEqual(r["level"], "unavailable")
        self.assertIn("Calendar", r["message"])
        self.assertEqual(self.m.check(self.vid)["counts"]["unavailable"], 1)

    def test_holiday_rule_works_once_the_months_are_filled_in(self):
        from app import store
        store.ex("UPDATE setting SET value='1 5 8' WHERE key='plan.holiday_months'")
        store.ex("UPDATE setting SET value='2' "
                 "WHERE key='plan.holiday_month_max_slots'")
        store.conn().commit()
        for _ in range(3):
            self.slot(month="2026-05")
        self.slot(month="2026-07")
        self.assertEqual(self.rule("holiday", "2026-05")["level"], "warn")
        self.assertIsNone(self.rule("holiday", "2026-07"), "連休でない月は出さない")

    def test_effort_is_unavailable_when_the_bounds_are_cleared(self):
        from app import store
        store.ex("UPDATE setting SET value=NULL WHERE key='plan.effort_max'")
        store.conn().commit()
        self.slot()
        self.assertEqual(self.rule("effort", "2026-05")["level"], "unavailable")


# ══════════════════════════════════════════════════════════
class TestExceptions(Base):
    def test_ack_needs_a_reason_and_does_not_hide_the_warning(self):
        """**消える作りにすると、理由を書かずに消すほうが速くなる**（F-3-3）。"""
        for _ in range(9):
            self.slot(month="2026-05")
        with self.assertRaises(ValueError):
            self.m.ack(self.vid, "count", "2026-05", "   ", "tester")
        self.m.ack(self.vid, "count", "2026-05", "式典向けの集中投入。社長承認済", "tester")
        r = self.rule("count", "2026-05")
        self.assertEqual(r["level"], "warn", "承知しても警告は消えないこと")
        self.assertEqual(r["acked"]["reason"], "式典向けの集中投入。社長承認済")
        self.assertEqual(self.m.check(self.vid)["warn_unacked"],
                         sum(1 for x in self.m.check(self.vid)["results"]
                             if x["level"] == "warn" and not x["acked"]))

    def test_unknown_rule_is_rejected(self):
        with self.assertRaises(ValueError):
            self.m.ack(self.vid, "でっちあげ", "FY", "理由", "tester")


# ══════════════════════════════════════════════════════════
class TestConvert(Base):
    def test_convert_makes_a_project_and_backdates_the_task_setup_deadline(self):
        """**FR-86。**発売の2か月前を「タスクを組み終える期限」として返す。"""
        sid = self.slot(month="2026-05", launch_date="2026-05-20",
                        flow_type="meire", occasion="母の日")
        r = self.m.convert(sid, "tester")
        self.assertEqual(r["task_setup_due"]["due"], "2026-03-20")
        self.assertEqual(r["task_setup_due"]["lead_months"], 2)
        self.assertGreater(r["tasks_created"], 0, "標準タスクが展開されること")
        from app import store
        self.assertEqual(store.val("SELECT stage FROM project WHERE id=?",
                                   (r["project_id"],)), "年間プラン採択")
        self.assertEqual(store.val("SELECT launch_date FROM project WHERE id=?",
                                   (r["project_id"],)), "2026-05-20")
        self.assertEqual(store.val("SELECT project_id FROM plan_slot WHERE id=?",
                                   (sid,)), r["project_id"])
        self.assertEqual(store.val(
            "SELECT COUNT(*) FROM project_revision WHERE project_id=? "
            "AND what='年間プランの枠から変換'", (r["project_id"],)), 1)

    def test_a_month_only_slot_counts_back_from_the_first_of_the_month(self):
        """**月末で計算すると期限が最大30日うしろへずれる。**早いほうに倒す。"""
        sid = self.slot(month="2026-05", flow_type="meire")
        r = self.m.convert(sid, "tester")
        self.assertEqual(r["task_setup_due"]["due"], "2026-03-01")
        self.assertIn("月の1日で逆算", r["task_setup_due"]["based_on"])
        from app import store
        self.assertIsNone(store.val("SELECT launch_date FROM project WHERE id=?",
                                    (r["project_id"],)),
                          "仮の発売日を置かないこと")

    def test_a_slot_cannot_be_converted_twice(self):
        sid = self.slot(month="2026-05", flow_type="meire")
        self.m.convert(sid, "tester")
        with self.assertRaises(ValueError):
            self.m.convert(sid, "tester")
        from app import store
        self.assertEqual(store.val("SELECT COUNT(*) FROM project"), 1)

    def test_converted_slot_is_kept_and_cannot_be_deleted(self):
        sid = self.slot(month="2026-05", flow_type="meire")
        self.m.convert(sid, "tester")
        with self.assertRaises(ValueError):
            self.m.delete_slot(sid)

    def test_pagerenew_conversion_says_it_is_not_counted_as_a_launch(self):
        """**黙って新商品売上・本数に混ぜない**（F-4-9・F-10-11）。"""
        sid = self.slot(month="2026-05", kind="pagerenew")
        r = self.m.convert(sid, "tester")
        self.assertIn("発売本数に数えません", r["message"])
        self.assertIn("標準タスクが未定義", r["message"])
        from app import store
        self.assertEqual(store.val("SELECT revenue_counted FROM project WHERE id=?",
                                   (r["project_id"],)), 0)


# ══════════════════════════════════════════════════════════
class TestOverview(Base):
    def test_it_starts_from_zero_rows(self):
        """**計画が0行の状態から立ち上がる**（D-3 ／ FR-134）。"""
        from app import plan, store
        store.ex("DELETE FROM plan_version")
        store.conn().commit()
        o = plan.overview()
        self.assertEqual(o["versions"], [])
        self.assertIsNone(o["current"])
        self.assertIn("0行", o["empty_note"])

    def test_month_view_separates_unknown_effort(self):
        self.slot(month="2026-05", kind="other", flow_type="meire")
        self.slot(month="2026-05", kind="other", flow_type="material")
        d = self.m.detail(self.vid)
        m = d["months"][0]
        self.assertEqual(m["effort"], 5.5)
        self.assertEqual(m["effort_unknown"], 1)
        self.assertIn("**時間(h)**ではありません", d["rules"]["effort_unit_note"])

    def test_bad_month_format_is_rejected(self):
        with self.assertRaises(ValueError):
            self.m.create_slot("tester", version_id=self.vid, launch_month="2026/05")


if __name__ == "__main__":
    unittest.main()
