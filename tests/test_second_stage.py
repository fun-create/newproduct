#!/usr/bin/env python3
"""
第2段の検査。**外部通信をしない。**サーバも起こさない（それは selfcheck.py の仕事）。

    python3 -m unittest discover -s tests -v
    python3 tests/test_second_stage.py

DB は毎回 `NEWPRODUCT_DB` を一時ファイルに向けて作り直す。
**「今日」は `NEWPRODUCT_TODAY` で固定する。**期間フィルタの境界は日付ひとつで
結果が変わるので、固定できないと「境界を検査した」と言えない。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

TODAY = "2026-09-21"


def _fresh():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(p)
    os.environ["NEWPRODUCT_DB"] = p
    os.environ["NEWPRODUCT_TODAY"] = TODAY
    from app import store
    store.close()
    return p


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dbfile = _fresh()
        from app import seed
        cls.counts = seed.run()

    @classmethod
    def tearDownClass(cls):
        from app import store
        store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(cls.dbfile + suffix)
            except OSError:
                pass


# ══════════════════════════════════════════════════════════
class TestMigration(Base):
    def test_all_tables_exist(self):
        from app import store
        names = {r["name"] for r in store.q(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        want = {"role", "role_member", "flow_type", "task_template", "project",
                "project_section", "project_variant", "project_revision",
                "task", "work_item", "gate_def", "gate_review",
                "hold_reason", "abort_reason", "audit"}
        self.assertEqual(want - names, set(), f"足りないテーブル: {want - names}")

    def test_migrate_is_idempotent(self):
        """**何度流しても同じ結果。**起動のたびに流すので、ここが崩れると毎朝壊れる。"""
        from app import store
        before = store.val("SELECT COUNT(*) FROM task_template")
        store.migrate()
        from app import seed
        seed.run()
        self.assertEqual(store.val("SELECT COUNT(*) FROM task_template"), before)

    def test_migration_recorded(self):
        from app import store
        n = store.val("SELECT COUNT(*) FROM schema_migration")
        self.assertGreaterEqual(n, 5)


class TestSeedCounts(Base):
    def test_task_template_is_178(self):
        """**178行が正。**196行の古いテンプレートは採用しない。"""
        from app import store
        self.assertEqual(self.counts["task_template"], 178)
        self.assertEqual(store.val(
            "SELECT COUNT(*) FROM task_template WHERE template_version=1"), 178)

    def test_task_template_per_flow(self):
        from app import store
        got = {r["flow_type"]: r["n"] for r in store.q(
            "SELECT flow_type, COUNT(*) AS n FROM task_template GROUP BY flow_type")}
        self.assertEqual(got, {"meire": 32, "freecut": 32, "webdeco": 39,
                               "newmodel": 14, "material": 39, "readymade": 22})

    def test_masters(self):
        self.assertEqual(self.counts["flow_type"], 7)
        self.assertEqual(self.counts["role"], 7)
        self.assertEqual(self.counts["gate_def"], 7)
        self.assertEqual(self.counts["hold_reason"], 5)
        self.assertEqual(self.counts["abort_reason"], 4)
        self.assertIsNone(self.counts["missing"])

    def test_material_effort_point_is_null_not_zero(self):
        """**推測で埋めない。**⑤資材リニューアルは係数が実測できていない。

        0 で埋めると「工数ゼロの安いフロー」に見えて、逆に危ない。
        """
        from app import store
        r = store.one("SELECT * FROM flow_type WHERE code='material'")
        self.assertIsNone(r["effort_point"])
        self.assertIn("実測できない", r["effort_point_note"])

    def test_pagerenew_has_no_template(self):
        """**ページリニューアルには標準タスクが無い。発明しない。**"""
        from app import store
        self.assertEqual(store.val(
            "SELECT COUNT(*) FROM task_template WHERE flow_type='pagerenew'"), 0)
        self.assertEqual(store.val(
            "SELECT has_template FROM flow_type WHERE code='pagerenew'"), 0)
        self.assertIn("標準タスク未定義",
                      store.val("SELECT note FROM flow_type WHERE code='pagerenew'"))

    def test_template_role_totals_match_source(self):
        """**43.25h／27.28h は6フロー合算。**種データと一致することを見る。"""
        from app import task
        tt = task.template_totals()
        by = {r["role"]: r for r in tt["by_role"]}
        self.assertAlmostEqual(by["admin"]["hours"], 43.25, places=2)
        self.assertAlmostEqual(by["admin"]["ai_hours"], 27.28, places=2)
        self.assertAlmostEqual(by["member"]["hours"], 44.50, places=2)
        self.assertAlmostEqual(by["member"]["ai_hours"], 18.36, places=2)
        self.assertIn("6フロー合算", tt["caption"])
        self.assertIn("1本あたりではない", tt["caption"])

    def test_reserve_hours_are_missing_from_the_adopted_source(self):
        """**F-5-7 の「予備時間」は、採用した178行には1行も無い。**

        古い表にだけあり、フローごとに 管理者/メンバー 各 6.75〜19.75h。
        **推測で足さない。**差分として記録し、採否は商品開発部の確認に委ねる。
        """
        from app import seed, store
        self.assertEqual(store.val(
            "SELECT COUNT(*) FROM task_template WHERE title LIKE '%予備時間%'"), 0)
        rh = seed.diff_report()["reserve_hours_only_in_old"]
        self.assertEqual(len(rh["meire"]["rows"]), 2)
        self.assertEqual(rh["meire"]["total_hours"], 32.0)
        self.assertEqual(rh["newmodel"]["total_hours"], 13.5)

    def test_old_template_diff_is_recorded_not_adopted(self):
        from app import seed
        d = seed.diff_report()
        self.assertEqual(d["adopted"]["rows"], 178)
        self.assertTrue(d["not_adopted"]["task_rows"] > 0)
        self.assertIn("採用しない", d["note"])


# ══════════════════════════════════════════════════════════
class TestProjectAndGates(Base):
    def setUp(self):
        from app import project
        self.p = project.create("tester", internal_name="検査用",
                                flow_type="meire", launch_date="2026-11-27",
                                occasion="七五三(11/15)", area="推し活",
                                summary="検査用の案件", owner="tester",
                                cat1="アクリル製品", cat2="アクスタ", size="90×120mm")

    def test_tasks_expanded_from_template(self):
        from app import store
        self.assertEqual(self.p["tasks_created"], 32)
        self.assertEqual(store.val(
            "SELECT COUNT(*) FROM task WHERE project_id=?", (self.p["id"],)), 32)

    def test_product_label_hides_product_name(self):
        """**商品名を表示しない**（N-6-2）。分類とサイズで表す。"""
        from app import project
        d = project.detail(self.p["id"])
        self.assertIn("アクスタ", d["header"]["product"])
        self.assertIn("90×120mm", d["header"]["product"])

    def test_missing_items_are_computed(self):
        """ゲートの `required_items` との差分を**機械的に**出す（F-6-2）。"""
        from app import gate
        miss = {m["key"] for m in gate.missing(self.p["id"], "G0")}
        # summary と internal_name は入れてある → 欠けていない
        self.assertNotIn("internal_name", miss)
        self.assertNotIn("summary", miss)
        # C.target はまだ空 → 欠けている
        self.assertIn("target_scene", miss)
        # 起票経路は第1段のデータ。人の確認記録が無いので欠けている
        self.assertIn("origin", miss)

    def test_missing_shrinks_when_section_filled(self):
        from app import gate, project
        project.save_section(self.p["id"], "C.target", "七五三の記念撮影", "tester")
        miss = {m["key"] for m in gate.missing(self.p["id"], "G0")}
        self.assertNotIn("target_scene", miss)

    def test_lines_check_counts_bullets(self):
        """差別化は「3点以上」。**行数で数える。**2行では通さない。"""
        from app import gate, project
        project.save_section(self.p["id"], "C.diff", "強み1\n強み2", "tester")
        self.assertIn("diff3", {m["key"] for m in gate.missing(self.p["id"], "G3")})
        project.save_section(self.p["id"], "C.diff", "強み1\n強み2\n強み3", "tester")
        self.assertNotIn("diff3", {m["key"] for m in gate.missing(self.p["id"], "G3")})

    def test_manual_check_records_who_and_when(self):
        from app import gate, project, store
        project.save_check(self.p["id"], "origin", True, "社内", "tester")
        self.assertNotIn("origin", {m["key"] for m in gate.missing(self.p["id"], "G0")})
        r = store.one("SELECT * FROM project_check WHERE project_id=? AND item_key=?",
                      (self.p["id"], "origin"))
        self.assertEqual(r["updated_by"], "tester")
        self.assertTrue(r["updated_at"])

    def test_effort_point_inherits_flow_coefficient(self):
        from app import store
        self.assertEqual(store.val("SELECT effort_point FROM project WHERE id=?",
                                   (self.p["id"],)), 5.5)


class TestSimpleFlow(Base):
    """**④ニューモデル追加は G2 → G5 の簡易フロー**（F-6-4・§5-6）。

    G1・G3・G4 が空欄だと**永久に「未達」に見える**。`対象外` と書いてあれば、
    放置ではなく設計だと分かる。
    """

    def setUp(self):
        from app import project
        self.nm = project.create("tester", internal_name="機種追加",
                                 flow_type="newmodel", launch_date="2026-12-01")
        self.me = project.create("tester", internal_name="名入れ",
                                 flow_type="meire", launch_date="2026-12-01")

    def test_newmodel_skips_g1_g3_g4(self):
        from app import gate, store
        p = dict(store.one("SELECT * FROM project WHERE id=?", (self.nm["id"],)))
        st = {g["gate"]: g["state"] for g in gate.board(p)}
        self.assertEqual(st["G1"], "対象外")
        self.assertEqual(st["G3"], "対象外")
        self.assertEqual(st["G4"], "対象外")
        self.assertNotEqual(st["G2"], "対象外")
        self.assertNotEqual(st["G5"], "対象外")

    def test_other_flows_do_not_skip(self):
        from app import gate, store
        p = dict(store.one("SELECT * FROM project WHERE id=?", (self.me["id"],)))
        st = {g["gate"]: g["state"] for g in gate.board(p)}
        for g in ("G0", "G1", "G2", "G3", "G4", "G5", "G6"):
            self.assertNotEqual(st[g], "対象外", g)

    def test_taigai_is_a_state_not_a_blank(self):
        from app import gate
        self.assertIn("対象外", gate.STATES)
        self.assertEqual(gate.STATES["対象外"]["word"], "対象外")

    def test_pagerenew_project_has_no_tasks_and_says_so(self):
        from app import project
        r = project.create("tester", internal_name="ページ改訂",
                           flow_type="pagerenew", launch_date="2027-01-10")
        self.assertEqual(r["tasks_created"], 0)
        self.assertFalse(r["template_defined"])
        d = project.detail(r["id"])
        self.assertFalse(d["template_defined"])
        self.assertIn("標準タスク未定義", d["header"]["flow_note"])

    def test_material_project_effort_point_stays_unknown(self):
        from app import project
        r = project.create("tester", internal_name="資材更新", flow_type="material")
        d = project.detail(r["id"])
        self.assertIsNone(d["header"]["effort_point"])
        self.assertIn("実測できない", d["header"]["effort_point_note"])


# ══════════════════════════════════════════════════════════
class TestPermissions(Base):
    """**ゲート承認はアプリ権限(admin/user)で決めない**（§10-2 ②）。

    `gate_def.approver_role` × `role_member` で判定する。
    2値の admin/user に承認を載せると、承認させたいだけの社長を admin にするか、
    全ての管理者に社長の承認を許すかのどちらかになる。どちらも誤り。
    """

    def setUp(self):
        from app import project, store
        store.ex("DELETE FROM role_member")
        # 社長。**アプリ権限は user**（admin ではない）
        store.ex("INSERT INTO role_member (role_code,user_id,granted_at) "
                 "VALUES ('president','shacho',?)", (store.now_s(),))
        # 管理者。**アプリ権限は admin** だが、社長の承認はできてはいけない
        store.ex("INSERT INTO role_member (role_code,user_id,granted_at) "
                 "VALUES ('admin','kanri',?)", (store.now_s(),))
        store.ex("INSERT INTO role_member (role_code,user_id,granted_at) "
                 "VALUES ('prod','seisan',?)", (store.now_s(),))
        store.conn().commit()
        self.p = project.create("kanri", internal_name="権限検査",
                                flow_type="meire", launch_date="2026-11-27")

    def _fill(self, gate_name):
        """そのゲートの必須項目を埋める。**検査のための下ごしらえ。**"""
        from app import gate, project, store
        for m in gate.missing(self.p["id"], gate_name):
            if m["check"] == "manual":
                project.save_check(self.p["id"], m["key"], True, "検査", "tester")
            elif m["check"].startswith("section:"):
                project.save_section(self.p["id"], m["check"].split(":", 1)[1],
                                     "検査用の記述", "tester")
            elif m["check"].startswith("project:"):
                col = m["check"].split(":", 1)[1]
                v = 5.5 if col == "effort_point" else "検査"
                store.ex(f"UPDATE project SET {col}=? WHERE id=?", (v, self.p["id"]))
                store.conn().commit()
            elif m["check"].startswith("lines:"):
                _, key, n = m["check"].split(":")
                project.save_section(self.p["id"], key,
                                     "\n".join(f"点{i}" for i in range(int(n))),
                                     "tester")
        self.assertEqual(gate.missing(self.p["id"], gate_name), [])

    def test_president_who_is_not_app_admin_can_pass_g2_and_g3(self):
        from app import gate
        self.assertTrue(gate.can_approve("shacho", "G2"))
        self.assertTrue(gate.can_approve("shacho", "G3"))
        self._fill("G2")
        r = gate.review(self.p["id"], "G2", "通過", "shacho", "枠を取る")
        self.assertEqual(r["result"], "通過")
        self._fill("G3")
        r = gate.review(self.p["id"], "G3", "通過", "shacho", "中身を見た")
        self.assertEqual(r["result"], "通過")

    def test_app_admin_manager_cannot_pass_g3(self):
        from app import gate
        self.assertTrue(gate.can_approve("kanri", "G2"))      # G2 は管理者も承認者
        self.assertFalse(gate.can_approve("kanri", "G3"))     # G3 は社長だけ
        with self.assertRaises(PermissionError):
            gate.review(self.p["id"], "G3", "通過", "kanri")

    def test_manager_cannot_pass_g4_which_is_production(self):
        from app import gate
        self.assertFalse(gate.can_approve("kanri", "G4"))
        self.assertTrue(gate.can_approve("seisan", "G4"))

    def test_user_without_business_role_cannot_pass_anything(self):
        from app import gate
        for g in ("G0", "G1", "G2", "G3", "G4", "G5", "G6"):
            self.assertFalse(gate.can_approve("nobody", g), g)

    def test_hold_and_abort_require_a_reason_code(self):
        """**理由は選択式**（F-6-5・B-14）。自由記述だけにすると数えられない。"""
        from app import gate
        with self.assertRaises(ValueError):
            gate.review(self.p["id"], "G2", "保留", "shacho", "なんとなく")
        with self.assertRaises(ValueError):
            gate.review(self.p["id"], "G2", "保留", "shacho", "", "そんなコードは無い")
        r = gate.review(self.p["id"], "G2", "保留", "shacho", "", "effort_over")
        self.assertEqual(r["result"], "保留")

    def test_pass_is_refused_while_items_are_missing(self):
        """**揃わないと通さない**（F-6 の「通過条件」）。

        通せてしまうと `required_items` は飾りになり、
        「どこまで埋まれば完成か」の定義が無かった状態を作り直してしまう。
        """
        from app import gate
        with self.assertRaises(ValueError) as cm:
            gate.review(self.p["id"], "G2", "通過", "shacho")
        self.assertIn("欠けているもの", str(cm.exception))
        # 差戻し・保留は、欠けていても記録できる（むしろ欠けているから止める）
        gate.review(self.p["id"], "G2", "差戻し", "shacho", "機会が無い")

    def test_review_records_what_was_missing(self):
        from app import gate, store
        gate.review(self.p["id"], "G2", "差戻し", "shacho", "機会が無い")
        r = store.one("SELECT * FROM gate_review WHERE project_id=? ORDER BY id DESC",
                      (self.p["id"],))
        self.assertEqual(r["result"], "差戻し")
        self.assertEqual(r["approved_by"], "shacho")
        self.assertIn("occasion", r["missing_items"])

    def test_drive_is_source_of_truth_blocks_editing(self):
        """R-2。**Drive 側が正の案件は、アプリ側を編集不可にする**（§4-4）。"""
        from app import gate, project, store
        store.ex("UPDATE project SET source_of_truth='drive' WHERE id=?",
                 (self.p["id"],))
        store.conn().commit()
        with self.assertRaises(PermissionError):
            project.save_section(self.p["id"], "C.concept", "x", "kanri")
        with self.assertRaises(PermissionError):
            gate.review(self.p["id"], "G2", "通過", "shacho")
        store.ex("UPDATE project SET source_of_truth='app' WHERE id=?",
                 (self.p["id"],))
        store.conn().commit()


# ══════════════════════════════════════════════════════════
class TestPeriodFilter(Base):
    """期間フィルタの境界（画面設計 3-10）。

    **既定は「期限切れ＋今日」。**「今日」だけにすると、期限切れ（実運用台帳で
    未着手256件）が永久に見えない。**「期限なし」は常設。**
    """

    def setUp(self):
        from app import project, store
        store.ex("DELETE FROM task")
        store.ex("DELETE FROM work_item")
        store.conn().commit()
        self.p = project.create("tester", internal_name="期間検査",
                                flow_type="pagerenew")   # テンプレートが無いので空
        rows = [
            ("昨日", "2026-09-20", "未着手"),
            ("昨日だが完了", "2026-09-20", "完了"),
            ("昨日だが対象外", "2026-09-20", "対象外"),
            ("今日", "2026-09-21", "未着手"),
            ("今日だが完了", "2026-09-21", "完了"),
            ("明日", "2026-09-22", "未着手"),
            ("6日後", "2026-09-27", "未着手"),
            ("7日後", "2026-09-28", "未着手"),
            ("期限なし", None, "未着手"),
        ]
        for i, (t, d, s) in enumerate(rows):
            store.ex("INSERT INTO task (project_id,seq,title,role,due_on,hours,"
                     "status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (self.p["id"], i, t, "admin", d, 1.0, s, store.now_s()))
        store.conn().commit()

    def _titles(self, when):
        from app import task
        return [r["title"] for r in task.listing(when)["rows"]]

    def test_overdue_excludes_done_and_out_of_scope(self):
        """**完了・対象外は「詰まっているもの」ではない。**"""
        self.assertEqual(self._titles("overdue"), ["昨日"])

    def test_today_is_exactly_today(self):
        self.assertEqual(sorted(self._titles("today")), ["今日", "今日だが完了"])

    def test_default_is_overdue_plus_today(self):
        from app import task
        got = sorted(self._titles(task.DEFAULT_WHEN))
        self.assertEqual(got, ["今日", "今日だが完了", "昨日"])

    def test_plus_n_is_a_single_day(self):
        self.assertEqual(self._titles("+1"), ["明日"])
        self.assertEqual(self._titles("+6"), ["6日後"])
        self.assertEqual(self._titles("+2"), [])

    def test_plus_7_is_refused(self):
        with self.assertRaises(ValueError):
            self._titles("+7")

    def test_week_is_today_through_plus6_inclusive(self):
        got = sorted(self._titles("week"))
        self.assertEqual(got, sorted(["今日", "今日だが完了", "明日", "6日後"]))

    def test_week_excludes_day7(self):
        self.assertNotIn("7日後", self._titles("week"))

    def test_none_is_a_first_class_button(self):
        """**「期限なし」を隠すと、期限を入れない運用が固定する**（88件・22%）。"""
        from app import task
        self.assertEqual(self._titles("none"), ["期限なし"])
        self.assertIn("none", task.WHENS)
        self.assertEqual(task.counts()["none"], 1)

    def test_all_includes_everything(self):
        self.assertEqual(len(self._titles("all")), 9)

    def test_no_due_rows_sort_last(self):
        """**期限なしは最後にまとめる。**期限のある行と混ぜない。"""
        from app import task
        rows = task.listing("all")["rows"]
        self.assertIsNone(rows[-1]["due_on"])

    def test_unknown_filter_is_refused(self):
        with self.assertRaises(ValueError):
            self._titles("last-week")


class TestEffortMonth(Base):
    """**工数は発売月ではなくタスク実施月に積む**（F-3-6）。"""

    def setUp(self):
        from app import project, store
        store.ex("DELETE FROM task")
        store.ex("DELETE FROM work_item")
        store.conn().commit()
        # 発売は 2027-03 だが、タスクは 2026-10 に実施する
        self.p = project.create("tester", internal_name="実施月検査",
                                flow_type="pagerenew", launch_date="2027-03-01")
        store.ex("INSERT INTO task (project_id,seq,title,role,due_on,hours,status,"
                 "created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (self.p["id"], 1, "コンセプト", "admin", "2026-10-15", 3.0,
                  "未着手", store.now_s()))
        store.ex("INSERT INTO task (project_id,seq,title,role,start_on,hours,status,"
                 "created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (self.p["id"], 2, "期限なし・開始だけ", "member", "2026-11-02", 2.0,
                  "未着手", store.now_s()))
        store.ex("INSERT INTO task (project_id,seq,title,role,hours,status,"
                 "created_at) VALUES (?,?,?,?,?,?,?)",
                 (self.p["id"], 3, "日付なし", "admin", 9.0, "未着手", store.now_s()))
        store.conn().commit()

    def test_hours_land_on_the_task_month_not_the_launch_month(self):
        from app import task
        load = task.load_by_month_role()
        months = {m["month"]: m for m in load["months"]}
        self.assertIn("2026-10", months)
        self.assertNotIn("2027-03", months)
        self.assertEqual(months["2026-10"]["own"][0]["hours"], 3.0)

    def test_start_on_is_used_when_due_on_is_missing(self):
        from app import task
        months = {m["month"]: m for m in task.load_by_month_role()["months"]}
        self.assertIn("2026-11", months)

    def test_rows_without_any_date_are_counted_not_hidden(self):
        """**どの月にも積まない。**だが「無かったこと」にもしない。"""
        from app import task
        load = task.load_by_month_role()
        self.assertEqual(load["no_month"], 1)
        self.assertIn("1 件", load["no_month_caption"])

    def test_external_roles_are_kept_separate(self):
        """他部署（生産部・Webマーケ）は別列。**自部署と足さない。**"""
        from app import store, task
        store.ex("INSERT INTO task (project_id,seq,title,role,due_on,hours,status,"
                 "created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (self.p["id"], 4, "他部署", "webmkt", "2026-10-20", 5.0,
                  "未着手", store.now_s()))
        store.conn().commit()
        m = {x["month"]: x for x in task.load_by_month_role()["months"]}["2026-10"]
        self.assertEqual([r["role"] for r in m["own"]], ["admin"])
        self.assertEqual([r["role"] for r in m["external"]], ["webmkt"])
        self.assertIn("試算対象外", task.load_by_month_role()["external_caption"])


class TestDashboard(Base):
    """**1段目は「いま詰まっているもの」**（§10-2 ①）。
    2段目は第1段・第4段が未実装なので **「未計測」と出す**（0 ではない）。"""

    def test_first_row_is_what_is_stuck(self):
        from app import task
        d = task.dashboard("tester")
        self.assertEqual(set(d["stuck"]), {"overdue", "gate_waiting", "today"})

    def test_second_row_says_unmeasured_not_zero(self):
        from app import task
        d = task.dashboard("tester")
        labels = [m["label"] for m in d["monthly"]]
        self.assertEqual(labels, ["コンセプト在庫月数", "今月の枠の消化",
                                  "発売後チェックの未処理"])
        for m in d["monthly"]:
            self.assertIsNone(m["value"])
            self.assertEqual(m["state"], "未計測")
            self.assertTrue(m["why"])


@unittest.skipUnless((BASE / "auth.py").is_file(),
                     "auth.py が無い場所（開発用の写し）では server.py を読み込めない。"
                     "本番の /opt/newproduct では必ず走る")
class TestGateBoardShape(Base):
    """ゲート盤（画面設計 3-9）が返す形。**app.js が読む鍵が欠けていないこと。**

    `server.py` の `gates_board()` を HTTP を通さずに呼ぶ。
    **外部通信をしない**ので、ここで形を固定しておく。
    """

    def setUp(self):
        from app import project, store
        store.ex("DELETE FROM role_member")
        store.ex("INSERT INTO role_member (role_code,user_id,granted_at) "
                 "VALUES ('president','shacho',?)", (store.now_s(),))
        store.conn().commit()
        self.nm = project.create("tester", internal_name="機種追加",
                                 flow_type="newmodel", launch_date="2026-12-01",
                                 cat1="スキンシール", cat2="iPhone")

    def test_board_has_seven_gates_and_marks_out_of_scope(self):
        import server
        d = server.gates_board("shacho")
        self.assertEqual([g["gate"] for g in d["gates"]],
                         ["G0", "G1", "G2", "G3", "G4", "G5", "G6"])
        row = next(r for r in d["rows"] if r["id"] == self.nm["id"])
        st = {c["gate"]: c["state"] for c in row["cells"]}
        self.assertEqual(st["G1"], "対象外")
        self.assertEqual(st["G3"], "対象外")
        self.assertEqual(st["G4"], "対象外")
        for c in row["cells"]:
            # **記号だけにしない。**語を必ず持つ（N-11）
            self.assertTrue(c["word"])
            self.assertTrue(c["glyph"])

    def test_board_puts_my_rows_first(self):
        import server
        d = server.gates_board("shacho")
        self.assertIn("my_role_labels", d)
        self.assertIsNotNone(d["passed_30d"])
        mine = [r["mine"] for r in d["rows"]]
        self.assertEqual(mine, sorted(mine, reverse=True))

    def test_board_does_not_leak_product_names(self):
        """**商品名を表示しない**（N-6-2）。分類で表す。"""
        import server
        d = server.gates_board("shacho")
        row = next(r for r in d["rows"] if r["id"] == self.nm["id"])
        self.assertNotIn("機種追加", row["product"])
        self.assertIn("スキンシール", row["product"])


class TestAudit(Base):
    def test_audit_records_operations(self):
        from app import store
        n0 = store.val("SELECT COUNT(*) FROM audit")
        store.audit("tester", "test.action", "x", {"a": 1}, "127.0.0.1")
        self.assertEqual(store.val("SELECT COUNT(*) FROM audit"), n0 + 1)
        r = store.one("SELECT * FROM audit ORDER BY id DESC LIMIT 1")
        self.assertEqual(r["user_id"], "tester")
        self.assertEqual(r["action"], "test.action")


if __name__ == "__main__":
    unittest.main(verbosity=2)
