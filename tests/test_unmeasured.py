#!/usr/bin/env python3
"""
合成データ・mock を本番の出力経路に流さない／実データが無い指標は「未計測」（FR-144・F-9-10・ADR-096）。

出力経路ごとに、**元のデータが無い状態**で 0 や仮の値が出ないことを見る。
  - HUB への指標（hubmetrics.build）: 表が無ければ点を作らない（0 の列を送らない）
  - 月次レポート（report.build）: 売上フィードが無ければ「未計測」「作れませんでした」。新商品の合計は「対象なし」
  - ダッシュボードの2段目: G5 を通した案件が無ければ「対象なし」、承認済みプランが無ければ「未計測」
  - 採点の百分位: 点の無い行は母数に入れない（0 点として最下位にしない）
  - 販売計画の枠: 上限の分からない月があれば年間の上限は None（0 で足さない）
  - 原価の突き合わせ: tests/test_costcheck.py（比べられないときは理由）
また、アプリのコードに検査用の偽物（FAKE／mock）の切替が無いことを見る（偽物は tests/ とプレビューの写しだけ）。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Unmeasured(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-13"
        os.environ["NEWPRODUCT_SALESFEED"] = "/nonexistent/sales_v1.sqlite"
        os.environ["NEWPRODUCT_FCTR"] = "/nonexistent/fctr"
        os.environ["NEWPRODUCT_CALFC_TOKEN"] = "/nonexistent/calfc_token"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed
        seed.run()
        self.store = store

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass
        for k in ("NEWPRODUCT_TODAY", "NEWPRODUCT_SALESFEED", "NEWPRODUCT_FCTR", "NEWPRODUCT_CALFC_TOKEN"):
            os.environ.pop(k, None)

    def _no_past_products(self):
        self.store.ex("DELETE FROM past_product")
        self.store.conn().commit()

    def test_hub_metrics_without_table_sends_nothing(self):
        from app import hubmetrics
        self._no_past_products()
        b = hubmetrics.build()
        self.assertEqual([r for r in b["rows"] if r["metric"] in ("revenue_newproduct", "launches")], [])
        self.assertTrue(any("取り込まれていない" in n for n in b["notes"]), b["notes"])
        self.assertTrue(any("コンセプト在庫" in n and "書きません" in n for n in b["notes"]), b["notes"])

    def test_monthly_report_without_sources_says_unmeasured(self):
        from app import report
        body, problems = report.build("2026-09")
        self.assertNotIn("| 0円 |", body, "売上が取れない月を 0円 と書かない")
        self.assertTrue(any(p.startswith("売上") for p in problems) or "未計測" in body, (problems, body[:400]))
        self.assertIn("対象なし", body, "数える案件が無いときの新商品の合計は「対象なし」")
        self.assertIn("作れなかった節", body)

    def test_dashboard_second_row_distinguishes_none_from_zero(self):
        from app import task, plan, idea
        pl = task.post_launch_pending()
        self.assertIsNone(pl["value"])
        self.assertEqual(pl["state"], "対象なし")
        sc = plan.slot_consumption()
        self.assertIsNone(sc["value"])
        self.assertEqual(sc["state"], "未計測")
        cs = idea.concept_stock()
        self.assertIsNone(cs.get("value"))

    def test_new_product_totals_are_none_without_counted_projects(self):
        from app import sales
        n = sales.new_product_summary()
        self.assertIsNone(n["totals"]["全額"])
        self.assertIsNone(n["totals"]["増分"])

    def test_percentile_skips_rows_without_score(self):
        from app import idea as m
        from tests.test_ideas import _idea
        a = _idea("点あり", production_feasibility=5, expected_margin_yen=4000)
        b = _idea("点なし")
        m.score_v2(a, {"demand": 3, "market_size": 3, "advantage": 3, "theme_fit": 3}, "tester")
        now = self.store.now_s()
        self.store.ex("INSERT INTO idea_score (idea_id,rubric_version,axes,common_score,theme_fit,raw_total,total,"
                      "rank_basis,scored_by,scored_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (b, m.V2_VERSION, "{}", None, None, None, None, "percentile", "tester", now))
        self.store.conn().commit()
        m.recompute_ranks(m.V2_VERSION)
        sa, sb = m.score_of(a, "v2"), m.score_of(b, "v2")
        self.assertEqual(sa["percentile"], 0.0, "点のある方が首位（母数は点のある行だけ）")
        self.assertEqual(sa["common_percentile"], 0.0)
        self.assertIsNone(sb["percentile"], "点の無い行を 0 点の最下位にしない")
        self.assertIsNone(sb["common_percentile"])
        self.assertIsNone(sb["rank"])

    def test_capacity_is_none_when_monthly_slots_unknown(self):
        from app import simulate
        r = simulate.scenario(12, None, 2026, 5.0)
        self.assertIsInstance(r["placement"]["capacity"], int)
        self.store.ex("UPDATE setting SET value='' WHERE key='plan.monthly_launch_slots'")
        self.store.conn().commit()
        r = simulate.scenario(12, None, 2026, 5.0)
        self.assertIsNone(r["placement"]["capacity"], "上限の分からない月を 0 で足さない")
        self.assertEqual(r["placement"]["capacity_unknown_months"], 12)

    def test_app_code_has_no_fake_switch(self):
        """偽物の口は tests/ と、プレビューの写し（リポジトリの外）だけ。本番のコードに切替を持たない。"""
        bad = []
        for p in list((BASE / "app").glob("*.py")) + [BASE / "server.py"] + list((BASE / "tools").glob("*.py")):
            src = p.read_text(encoding="utf-8")
            for m in re.finditer(r"(?i)\b(fake|mock|dummy)\w*\b", src):
                line = src[:m.start()].count("\n") + 1
                bad.append(f"{p.relative_to(BASE)}:{line}")
        self.assertEqual(bad, [], "本番のコードに偽物の切替・名前がある")
        from app import seisan
        self.assertIs(seisan.transport, seisan._http, "CIP への経路の既定は本物（環境変数で差し替えない）")


if __name__ == "__main__":
    unittest.main()
