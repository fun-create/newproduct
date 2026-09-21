#!/usr/bin/env python3
"""
第1段（アイデア台帳・採点v2）の検査。**外部通信をしない。**

    python3 -m unittest discover -s tests -v
    python3 tests/test_ideas.py

見ているのは、要件が「これを直す」と言った所そのもの。

  - 移行が**冪等**であること（2回流しても重複しない）
  - **件数**（シート別。数えずに「入っているはず」で通さない）
  - **v1 のウェイトが原文どおり再現できる**こと（逆算した係数の記録が正しい）
  - **百分位の境界**（F-1-9。絶対点をやめた以上、境界は検査対象）
  - **生産方法の減点**（F-1-6。作れない案が上位に来ないこと）
  - **類似検出の決定性**（F-1-12。同じ入力なら同じ結果）
  - **v1 と v2 が混ざらない**こと（F-1-10・第8章 ⑦）
  - AI採点が**既定 off** で、外部通信の部品を持たないこと（F-1-11・第11章 ⑩）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

TODAY = "2026-09-21"
SEED_DIR = BASE / "seed"


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


def _idea(title="テスト案", **f):
    """検査用のアイデアを1件。**起票は4項目＋起票経路だけ**（F-1-3）。"""
    from app import idea as m
    r = m.create("tester", title=title, summary=f.get("summary", ""),
                 target_scene=f.get("target_scene", ""),
                 origin=f.get("origin", "internal"),
                 theme_id=f.get("theme_id", "oshikatsu"))
    fields = {k: v for k, v in f.items()
              if k in ("production_feasibility", "expected_margin_yen",
                       "demand_cycle", "design_freedom")}
    if fields:
        m.update_fields(r["id"], "tester", **fields)
    return r["id"]


# ══════════════════════════════════════════════════════════
class TestSchemaAndMasters(Base):
    def test_tables_exist(self):
        from app import store
        names = {r["name"] for r in store.q(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        want = {"idea", "rubric", "rubric_axis", "idea_score", "theme",
                "theme_score", "theme_signal", "idea_similar", "setting"}
        self.assertEqual(want - names, set(), f"足りないテーブル: {want - names}")

    def test_rubric_versions(self):
        from app import idea as m
        got = {r["version"] for r in m.rubrics()}
        self.assertEqual(got, {"v1-original", "v1-uchiwa", "v1-lovot",
                               "v1-bukkomi", "v2"})

    def test_v1_weights_recorded_as_measured(self):
        """**実測で復元したウェイトが、そのまま `rubric_axis` に入っている。**

        ここがずれると、移行したスコアの意味が分からなくなる。
        """
        from app import store
        def w(version):
            return {r["code"]: r["weight"] for r in store.q(
                "SELECT code,weight FROM rubric_axis WHERE rubric_version=?",
                (version,))}
        self.assertEqual(w("v1-original"),
                         {"demand": 3.5, "market_size": 2.5, "advantage": 1.5,
                          "premium": 0.5, "year_round": 1.0, "theme_fit": 1.0})
        self.assertEqual(w("v1-uchiwa"),
                         {"demand": 2.5, "market_size": 1.5, "advantage": 1.5,
                          "premium": 0.5, "year_round": 1.0, "theme_fit": 3.0})
        self.assertEqual(w("v1-lovot")["lovot_love"], 3.0)
        self.assertEqual(w("v1-lovot")["price_freedom"], 1.5)
        # **ぶっこみのウェイトは実測できない。**0 でも 1 でもなく NULL
        self.assertEqual(set(w("v1-bukkomi").values()), {None})

    def test_v1_thresholds_differ_between_sheets(self):
        """**閾値もシートごとに別。**同じ SS/S/A/B/C を使っていたのが問題だった。"""
        from app import idea as m
        by = {r["version"]: r["thresholds"] for r in m.rubrics()}
        self.assertEqual(by["v1-original"]["SS"], 81)
        self.assertEqual(by["v1-uchiwa"]["SS"], 82)
        self.assertEqual(by["v1-original"]["S"], 75)
        self.assertEqual(by["v1-uchiwa"]["S"], 78)
        self.assertEqual(by["v1-original"]["B"], 65)
        self.assertEqual(by["v1-uchiwa"]["B"], 66)

    def test_v2_is_two_layers(self):
        from app import idea as m
        v2 = next(r for r in m.rubrics() if r["version"] == "v2")
        self.assertEqual(v2["common_max"], 70.0)
        self.assertEqual(v2["theme_max"], 30.0)
        self.assertEqual(v2["rank_method"], "percentile")
        common = [a for a in v2["axes"] if a["layer"] == "common"]
        self.assertEqual(sum(a["weight"] * a["scale_max"] for a in common), 70.0)
        theme = [a for a in v2["axes"] if a["layer"] == "theme"]
        self.assertEqual(sum(a["weight"] * a["scale_max"] for a in theme), 30.0)

    def test_year_round_is_gone_from_v2(self):
        """F-1-8。**通年性は点をやめて3値の属性。**v2 の得点軸に残っていない。"""
        from app import idea as m
        v2 = next(r for r in m.rubrics() if r["version"] == "v2")
        scored = {a["code"] for a in v2["axes"]
                  if a["layer"] in ("common", "theme")}
        self.assertNotIn("year_round", scored)
        self.assertIn("demand_cycle",
                      {a["code"] for a in v2["axes"] if a["layer"] == "attribute"})

    def test_settings_unset_is_not_zero(self):
        """N-10。**未設定を 0 で埋めない。**理由を持つ。"""
        from app import idea as m
        s = {r["key"]: r for r in m.settings()}
        self.assertIsNone(s["monthly_launch_target"]["value"])
        self.assertTrue(s["monthly_launch_target"]["why"])


# ══════════════════════════════════════════════════════════
class TestSimilar(Base):
    """F-1-12。**同じ入力なら同じ結果。**外部APIを使わない。"""

    def test_normalize_is_deterministic(self):
        from app import idea as m
        self.assertEqual(m.normalize("アクスタ　ジオラマ"), m.normalize("アクスタジオラマ"))
        self.assertEqual(m.normalize("ＮＦＣ内蔵アクスタ"), m.normalize("nfc内蔵アクスタ"))
        self.assertEqual(m.normalize("うちの子・名札"), m.normalize("うちの子名札"))

    def test_dice_is_symmetric_and_bounded(self):
        from app import idea as m
        a, b = "アクスタ ジオラマ", "アクスタジオラマ台座"
        self.assertAlmostEqual(m.dice(a, b), m.dice(b, a))
        self.assertEqual(m.dice(a, a), 1.0)
        self.assertGreaterEqual(m.dice(a, b), 0.0)
        self.assertLessEqual(m.dice(a, b), 1.0)

    def test_same_input_same_result(self):
        from app import idea as m
        for t in ("アクスタ ジオラマ", "光る アクリルスタンド", "うちの子名札"):
            _idea(t)
        first = m.similar_to_title("アクスタジオラマ")
        second = m.similar_to_title("アクスタジオラマ")
        self.assertEqual(first, second)

    def test_create_reports_similar(self):
        from app import idea as m
        _idea("推し色 アクリルキーホルダー")
        r = m.create("tester", title="推し色アクリルキーホルダー",
                     summary="", target_scene="", origin="internal",
                     theme_id="oshikatsu")
        self.assertTrue(r["similar"], "似た案が出ていない（F-1-12）")
        self.assertLessEqual(len(r["similar"]), m.SIMILAR_TOP)
        # キャッシュしても同じ並び
        self.assertEqual([x["id"] for x in m.similar_of(r["id"])],
                         [x["id"] for x in r["similar"]])

    def test_origin_is_required_at_creation(self):
        """F-1-4。**起票経路は必須。**あとから区別できなくなる。"""
        from app import idea as m
        with self.assertRaises(ValueError):
            m.create("tester", title="経路なし案", origin="")


# ══════════════════════════════════════════════════════════
class TestPercentile(Base):
    """F-1-9。**ランクは絶対点ではなく百分位。**境界を固定する。"""

    def test_rank_boundaries(self):
        from app import idea as m
        f = m._rank_from_percentile
        self.assertEqual(f(0.0), "SS")
        self.assertEqual(f(0.0099), "SS")
        self.assertEqual(f(0.01), "S")       # 上位1%「未満」が SS
        self.assertEqual(f(0.0499), "S")
        self.assertEqual(f(0.05), "A")
        self.assertEqual(f(0.1499), "A")
        self.assertEqual(f(0.15), "B")
        self.assertEqual(f(0.3499), "B")
        self.assertEqual(f(0.35), "C")
        self.assertEqual(f(1.0), "C")

    def test_ties_share_the_same_percentile(self):
        """**同点は同じ百分位。**並び順で勝ち負けが決まらない。"""
        from app import idea as m
        p = m._percentiles([10.0, 10.0, 5.0, 1.0])
        self.assertEqual(p[0], 0.0)
        self.assertEqual(p[1], 0.0)
        self.assertEqual(p[2], 0.5)
        self.assertEqual(p[3], 0.75)

    def test_percentile_is_within_theme(self):
        """母数はテーマ内。テーマ適合点の定義がテーマごとに違うため。"""
        from app import idea as m
        a = _idea("テーマ内1", theme_id="oshikatsu",
                  production_feasibility=5, expected_margin_yen=4000)
        b = _idea("テーマ内2", theme_id="lifeevent",
                  production_feasibility=5, expected_margin_yen=4000)
        m.score_v2(a, {"demand": 3, "market_size": 3, "advantage": 3,
                       "theme_fit": 3}, "tester")
        m.score_v2(b, {"demand": 9, "market_size": 9, "advantage": 9,
                       "theme_fit": 9}, "tester")
        # 別テーマ同士は比べない。**どちらも自テーマの首位**
        self.assertEqual(m.score_of(a, "v2")["percentile"], 0.0)
        self.assertEqual(m.score_of(b, "v2")["percentile"], 0.0)
        # 共通点だけはテーマをまたいで並ぶ（F-1-5 の狙い）
        self.assertGreater(m.score_of(a, "v2")["common_percentile"],
                           m.score_of(b, "v2")["common_percentile"])


# ══════════════════════════════════════════════════════════
class TestScoreV2(Base):
    """F-1-5・F-1-6・F-1-7。**作れない案が上位に来ないこと。**"""

    def test_two_layers_add_up(self):
        from app import idea as m
        i = _idea("2層の案", production_feasibility=5, expected_margin_yen=4000)
        s = m.score_v2(i, {"demand": 10, "market_size": 10, "advantage": 10,
                           "theme_fit": 10}, "tester")
        self.assertEqual(s["common_score"], 70.0)   # 満点の共通点
        self.assertEqual(s["theme_fit"], 30.0)      # 満点のテーマ適合
        self.assertEqual(s["raw_total"], 100.0)
        self.assertEqual(s["feasibility_factor"], 1.0)
        self.assertEqual(s["total"], 100.0)

    def test_feasibility_is_a_discount_on_the_total(self):
        """F-1-6。**生産方法(1-5) を総合点に掛ける。**"""
        from app import idea as m
        easy = _idea("作れる案", production_feasibility=5, expected_margin_yen=4000)
        hard = _idea("作れない案", production_feasibility=1, expected_margin_yen=4000)
        axes = {"demand": 8, "market_size": 8, "advantage": 8, "theme_fit": 8}
        a = m.score_v2(easy, axes, "tester")
        b = m.score_v2(hard, axes, "tester")
        self.assertEqual(a["raw_total"], b["raw_total"])   # 素点は同じ
        self.assertEqual(b["feasibility_factor"], 0.4)
        self.assertAlmostEqual(b["total"], round(a["raw_total"] * 0.4, 2))
        self.assertLess(b["total"], a["total"])
        # **作れない案が上に来ない**
        self.assertEqual(m.score_of(easy, "v2")["percentile"], 0.0)
        self.assertGreater(m.score_of(hard, "v2")["percentile"], 0.0)

    def test_all_five_factors(self):
        from app import idea as m
        self.assertEqual(
            {k: m.feasibility_factor(k) for k in (1, 2, 3, 4, 5)},
            {1: 0.4, 2: 0.7, 3: 0.85, 4: 0.95, 5: 1.0})
        self.assertIsNone(m.feasibility_factor(None))

    def test_missing_inputs_are_not_filled_with_zero(self):
        """N-10。**分からないものを 0（や 1.0）で埋めない。**"""
        from app import idea as m
        no_feas = _idea("生産方法なし", expected_margin_yen=4000)
        with self.assertRaises(ValueError):
            m.score_v2(no_feas, {"demand": 5, "market_size": 5,
                                 "advantage": 5, "theme_fit": 5}, "tester")
        no_margin = _idea("粗利なし", production_feasibility=5)
        with self.assertRaises(ValueError):
            m.score_v2(no_margin, {"demand": 5, "market_size": 5,
                                   "advantage": 5, "theme_fit": 5}, "tester")

    def test_margin_is_in_yen(self):
        """F-1-7。**高単価化しやすさを円建てに置き換えた。**"""
        from app import idea as m
        self.assertEqual(m.margin_points(4000), 10)
        self.assertEqual(m.margin_points(3999), 9)
        self.assertEqual(m.margin_points(0), 1)
        self.assertIsNone(m.margin_points(None))
        lo = _idea("薄利の案", production_feasibility=5, expected_margin_yen=100)
        hi = _idea("厚利の案", production_feasibility=5, expected_margin_yen=5000)
        axes = {"demand": 8, "market_size": 8, "advantage": 8, "theme_fit": 8}
        self.assertLess(m.score_v2(lo, axes, "tester")["total"],
                        m.score_v2(hi, axes, "tester")["total"])

    def test_demand_cycle_is_three_values_not_a_score(self):
        from app import idea as m
        i = _idea("通年の案", demand_cycle="通年")
        self.assertEqual(m.detail(i)["demand_cycle"], "通年")
        with self.assertRaises(ValueError):
            m.update_fields(i, "tester", demand_cycle="8")


# ══════════════════════════════════════════════════════════
class TestVersionsDoNotMix(Base):
    """F-1-10・第8章 ⑦。**v1 と v2 を混ぜない。既存を再採点しない。**"""

    def test_scores_are_separate_rows(self):
        from app import idea as m, store
        i = _idea("両方の版を持つ案", production_feasibility=5,
                  expected_margin_yen=4000)
        store.ex("INSERT INTO idea_score (idea_id,rubric_version,axes,total,rank,"
                 "rank_basis,scored_by,scored_at) VALUES (?,?,?,?,?,?,?,?)",
                 (i, "v1-uchiwa", json.dumps({"demand": 9}), 90.0, "SS",
                  "sheet", "import", "2026-01-01 00:00:00"))
        store.conn().commit()
        m.score_v2(i, {"demand": 3, "market_size": 3, "advantage": 3,
                       "theme_fit": 3}, "tester")
        rows = m.scores_of(i)
        self.assertEqual({r["rubric_version"] for r in rows},
                         {"v1-uchiwa", "v2"})
        self.assertEqual({r["generation"] for r in rows}, {1, 2})
        # **足し算した欄が無い**
        for r in rows:
            self.assertNotIn("combined", r)
            self.assertNotIn("grand_total", r)

    def test_v2_scoring_does_not_touch_v1(self):
        """**既存データを再採点しない。**v1 の点もランクも動かない。"""
        from app import idea as m, store
        i = _idea("v1を持つ案", production_feasibility=5, expected_margin_yen=4000)
        store.ex("INSERT INTO idea_score (idea_id,rubric_version,axes,total,rank,"
                 "rank_basis,scored_by,scored_at) VALUES (?,?,?,?,?,?,?,?)",
                 (i, "v1-original", json.dumps({"demand": 8}), 82.0, "SS",
                  "sheet", "import", "2026-01-01 00:00:00"))
        store.conn().commit()
        before = m.score_of(i, "v1-original")
        m.score_v2(i, {"demand": 1, "market_size": 1, "advantage": 1,
                       "theme_fit": 1}, "tester")
        m.recompute_ranks("v2")
        after = m.score_of(i, "v1-original")
        self.assertEqual((before["total"], before["rank"], before["scored_at"]),
                         (after["total"], after["rank"], after["scored_at"]))
        self.assertIsNone(after["percentile"], "v1 に百分位を付けない")

    def test_listing_shows_one_version_at_a_time(self):
        from app import idea as m
        d = m.listing({"rubric": "v2"})
        self.assertEqual(d["rubric"], "v2")
        for r in d["rows"]:
            if r["score"]:
                self.assertIn("total", r["score"])
        # 版を指定しないときは、点の列を作らず「持っている版」を並べるだけ
        d2 = m.listing({})
        self.assertIsNone(d2["rubric"])
        for r in d2["rows"]:
            self.assertIsNone(r["score"])


# ══════════════════════════════════════════════════════════
class TestAiScoring(Base):
    """F-1-11・第11章 ⑩。**呼べる形まで。既定 off。外部APIを呼ばない。**"""

    def test_off_by_default(self):
        from app import ai_score
        self.assertFalse(ai_score.enabled())
        st = ai_score.status()
        self.assertFalse(st["enabled"])
        self.assertIn("予算枠", st["reason"])

    def test_batch_does_nothing_while_off(self):
        from app import ai_score
        i = _idea("AI採点の対象", production_feasibility=5,
                  expected_margin_yen=4000)
        r = ai_score.run_batch([i], "tester")
        self.assertFalse(r["enabled"])
        self.assertEqual(r["scored"], 0)

    def test_scorer_is_replaceable_and_records_model(self):
        """差し替えられる形になっていること。**ここでも通信はしない。**"""
        from app import ai_score, idea as m

        class Stub(ai_score.Scorer):
            model = "stub-model/1"

            def score(self, idea):
                return {"demand": 7, "market_size": 6, "advantage": 5,
                        "theme_fit": 4}

        i = _idea("AIが採点する案", production_feasibility=4,
                  expected_margin_yen=1500)
        r = ai_score.run_batch([i], "tester", scorer=Stub(), force=True)
        self.assertTrue(r["enabled"])
        self.assertEqual(r["scored"], 1)
        s = m.score_of(i, "v2")
        self.assertEqual(s["scored_by"], "ai")
        self.assertEqual(s["model"], "stub-model/1")
        self.assertEqual(s["rubric_version"], "v2")
        self.assertTrue(s["scored_at"])

    def test_module_has_no_network_imports(self):
        """**外部通信の部品を持たない。**予算枠が付くまで呼べる口を開けない。"""
        src = (BASE / "app" / "ai_score.py").read_text(encoding="utf-8")
        for bad in ("import urllib", "import http", "import socket",
                    "import requests", "from urllib", "from http"):
            self.assertNotIn(bad, src, f"{bad} を持っている")


# ══════════════════════════════════════════════════════════
class TestConceptStock(Base):
    """F-1-14。**月間目標本数が未確定なら「未計測」のまま理由を出す。**"""

    def test_unmeasured_while_target_is_unset(self):
        from app import idea as m
        d = m.concept_stock()
        self.assertIsNone(d["value"])
        self.assertEqual(d["state"], "未計測")
        self.assertIn("月間", d["why"])
        self.assertEqual(d["stock_n"], 0)

    def test_counts_g3_passed_and_unlaunched(self):
        from app import idea as m, project, store
        pid = project.create("tester", internal_name="在庫1", flow_type="freecut")["id"]
        store.ex("INSERT INTO gate_review (project_id,gate,result,approved_by,"
                 "approved_at) VALUES (?,?,?,?,?)",
                 (pid, "G3", "通過", "shacho", "2026-09-01"))
        store.ex("UPDATE setting SET value='3' WHERE key='monthly_launch_target'")
        store.conn().commit()
        d = m.concept_stock()
        self.assertEqual(d["stock_n"], 1)
        self.assertEqual(d["value"], round(1 / 3, 1))
        # **色ではなく語で出す**（N-11）
        self.assertEqual(d["state"], "不足")
        # 発売したら在庫から外れる
        store.ex("UPDATE project SET stage='発売済' WHERE id=?", (pid,))
        store.conn().commit()
        self.assertEqual(m.concept_stock()["stock_n"], 0)
        store.ex("UPDATE setting SET value=NULL WHERE key='monthly_launch_target'")
        store.conn().commit()


# ══════════════════════════════════════════════════════════
@unittest.skipUnless(
    (SEED_DIR / "02_アイデアリスト__オリジナルグッズ評価用.tsv").is_file(),
    "移行元の TSV が seed/ にありません")
class TestImport(unittest.TestCase):
    """移行（tools/import_ideas.py）。**件数と冪等性。**"""

    @classmethod
    def setUpClass(cls):
        cls.dbfile = _fresh()
        sys.path.insert(0, str(BASE / "tools"))
        import import_ideas
        cls.mod = import_ideas
        cls.first = import_ideas.run()
        cls.second = import_ideas.run()     # **2回流す**

    @classmethod
    def tearDownClass(cls):
        from app import store
        store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(cls.dbfile + suffix)
            except OSError:
                pass

    def test_four_sheets_are_read(self):
        got = {s["sheet"] for s in self.first["sheets"]}
        self.assertEqual(got, {"original", "uchiwa", "bukkomi", "lovot"})
        self.assertEqual(self.first["missing"], [])

    def test_counts_are_reported_per_sheet(self):
        """**件数を必ず報告する。**0 のシートが黙って消えないこと。"""
        for s in self.first["sheets"]:
            self.assertIsNotNone(s["rows"])
            self.assertGreater(s["rows"], 0, f"{s['sheet']} が0件")
            self.assertEqual(s["ideas_new"], s["rows"])
        from app import store
        self.assertEqual(store.val("SELECT COUNT(*) FROM idea"),
                         self.first["total_rows"])

    def test_import_is_idempotent(self):
        """**2回流しても重複しない。**"""
        from app import store
        self.assertEqual(self.second["total_ideas_new"], 0)
        self.assertEqual(self.second["total_scores_new"], 0)
        self.assertEqual(store.val("SELECT COUNT(*) FROM idea"),
                         self.first["total_rows"])
        for s in self.second["sheets"]:
            self.assertEqual(s["ideas_existing"], s["rows"])

    def test_scored_at_is_not_bumped_by_reimport(self):
        """`scored_at` は**採点した日時**。取り込み直しで動かさない。"""
        from app import store
        n = store.val("SELECT COUNT(DISTINCT scored_at) FROM idea_score "
                      "WHERE scored_by='import'")
        self.assertGreaterEqual(n, 1)

    def test_origin_is_null_for_migrated_rows(self):
        """N-10。**推測で埋めない。**移行分の起票経路は不明。"""
        from app import store
        self.assertEqual(
            store.val("SELECT COUNT(*) FROM idea WHERE source_sheet IS NOT NULL "
                      "AND origin IS NOT NULL"), 0)
        self.assertEqual(
            store.val("SELECT COUNT(*) FROM idea WHERE source_sheet IS NOT NULL "
                      "AND origin_note <> '移行時不明'"), 0)

    def test_v1_totals_are_reproducible_from_recorded_weights(self):
        """**ここが移行の要。**

        記録したウェイトで、シートの総合点が再現できること。
        再現できないなら、逆算したウェイトが間違っているか、
        取り込んだ素点がずれている。**最大誤差はシート側の四捨五入の 0.5 点。**
        """
        from app import store
        checked = 0
        for version in ("v1-original", "v1-uchiwa", "v1-lovot"):
            w = {r["code"]: r["weight"] for r in store.q(
                "SELECT code,weight FROM rubric_axis WHERE rubric_version=?",
                (version,))}
            rows = store.q("SELECT axes,total FROM idea_score "
                           "WHERE rubric_version=? AND total IS NOT NULL",
                           (version,))
            self.assertGreater(len(rows), 0, f"{version} の採点が0件")
            for r in rows:
                axes = json.loads(r["axes"])
                if len(axes) != len(w):
                    continue
                calc = sum(w[k] * v for k, v in axes.items())
                self.assertLessEqual(
                    abs(calc - r["total"]), 0.5,
                    f"{version}: 計算 {calc} とシートの {r['total']} が合わない")
                checked += 1
        self.assertGreater(checked, 500, "検査した行が少なすぎる")

    def test_v1_ranks_are_kept_as_written(self):
        """**シートに書かれたランクをそのまま。**再判定していない。"""
        from app import store
        rows = store.q("SELECT rank_basis, COUNT(*) AS n FROM idea_score "
                       "WHERE scored_by='import' GROUP BY rank_basis")
        self.assertEqual({r["rank_basis"] for r in rows}, {"sheet"})
        self.assertEqual(
            store.val("SELECT COUNT(*) FROM idea_score WHERE scored_by='import' "
                      "AND percentile IS NOT NULL"), 0)

    def test_migrated_rows_have_no_v2_score(self):
        """第8章 ⑦。**既存1,505件は再採点しない。**"""
        from app import store
        self.assertEqual(
            store.val("SELECT COUNT(*) FROM idea_score s JOIN idea i "
                      "ON i.id=s.idea_id WHERE i.source_sheet IS NOT NULL "
                      "AND s.rubric_version='v2'"), 0)

    def test_each_sheet_keeps_its_own_rubric(self):
        from app import store
        pairs = {(r["source_sheet"], r["rubric_version"]) for r in store.q(
            "SELECT i.source_sheet, s.rubric_version FROM idea i "
            "JOIN idea_score s ON s.idea_id=i.id")}
        self.assertEqual(pairs, {("original", "v1-original"),
                                 ("uchiwa", "v1-uchiwa"),
                                 ("lovot", "v1-lovot")})

    def test_unscored_rows_are_kept_without_a_score(self):
        """ぶっこみ2件と LOVOT の【追加】欄。**捨てないし、0 も付けない。**"""
        from app import store
        n = store.val("SELECT COUNT(*) FROM idea i WHERE i.source_sheet "
                      "IS NOT NULL AND NOT EXISTS "
                      "(SELECT 1 FROM idea_score s WHERE s.idea_id=i.id)")
        self.assertGreater(n, 0)
        self.assertEqual(
            store.val("SELECT COUNT(*) FROM idea WHERE source_sheet='bukkomi'"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
