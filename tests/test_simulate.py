#!/usr/bin/env python3
"""
販売計画シミュレーション（F-14・ADR-072）の検査。

  - 取り込み: 表が先の月に入れている 0 を「見た月」に数えない（12か月そろわない商品は分布に入れない）
  - 分布: 中央値・0円・上位8割の商品数
  - 試算: 同じ入力なら同じ答え（種を固定）。本数が増えると見込みも増える。目標に届く割合・中央値の何倍が要るか
  - 工数を制約にする: 月の枠を超える本数は「入りきらない」、工数の上限を超える月は語で返す
  - 確定は社長だけ・理由が必須・同じ年度の前の確定は取消になる
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Simulate(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-09"
        os.environ["NEWPRODUCT_CALFC_TOKEN"] = "/nonexistent/calfc_token"      # 連休月は分からない＝通常月で数える
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, simulate
        seed.run()
        self.m, self.store = simulate, store
        vals = [0, 50000, 80000, 100000, 120000, 150000, 200000, 300000, 600000, 2000000]
        with store.tx() as c:
            for i, v in enumerate(vals):
                c.execute("INSERT INTO past_product (code,name,launch_date,fy,first12_yen,months_seen,complete,source,imported_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?)", (f"P{i:03d}", f"商品{i}", "2023-06-01", 2023, v, 12, 1, "検査", "x"))
            c.execute("INSERT INTO past_product (code,name,launch_date,fy,first12_yen,months_seen,complete,source,imported_at) "
                      "VALUES ('P999','最近の商品','2026-01-10',2025,5000,3,0,'検査','x')")
            c.execute("INSERT INTO role_member (role_code,user_id) VALUES ('president','boss')")

    def tearDown(self):
        self.store.close()
        os.environ.pop("NEWPRODUCT_CALFC_TOKEN", None)
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_import_ignores_prefilled_future_zeros(self):
        sys.path.insert(0, str(BASE / "tools"))
        import import_past_products as ip
        raw = {"P1": {"name": "a", "launch": "2025/12/01", "months": {
            "2025-12": {"total": 100.0, **{c: 0.0 for c in ip.CHANNELS}},
            "2026-01": {"total": 50.0, **{c: 0.0 for c in ip.CHANNELS}},
            "2026-02": {"total": 0.0, **{c: 0.0 for c in ip.CHANNELS}},       # 表が先回りして入れた 0
        }}, "P2": {"name": "b", "launch": "2024/01/05", "months": {
            f"{2024 + (k // 12)}-{k % 12 + 1:02d}": {"total": 10.0, **{c: 0.0 for c in ip.CHANNELS}} for k in range(12)}}}
        rows = {r["code"]: r for r in ip.build(raw)}
        self.assertEqual(ip.data_end(raw), "2026-01")
        self.assertEqual((rows["P1"]["months_seen"], rows["P1"]["complete"]), (2, 0))
        self.assertEqual((rows["P2"]["first12_yen"], rows["P2"]["complete"]), (120, 1))

    def test_distribution_and_deterministic_scenarios(self):
        d = self.m.distribution()
        self.assertEqual((d["n"], d["zero"], d["incomplete"]), (10, 1, 1), "12か月そろわない商品は入れない")
        a = self.m.compare(2026, 3000000, [12, 24])
        b = self.m.compare(2026, 3000000, [12, 24])
        self.assertEqual([x["p50"] for x in a["scenarios"]], [x["p50"] for x in b["scenarios"]], "同じ入力なら同じ答え")
        s12, s24 = a["scenarios"]
        self.assertLess(s12["p50"], s24["p50"])
        self.assertLessEqual(s12["p10"], s12["p50"])
        self.assertLessEqual(s12["p50"], s12["p90"])
        self.assertLess(s12["reach_rate"], s24["reach_rate"])
        self.assertEqual(s12["need_each"], 250000)
        self.assertEqual(s12["need_x_median"], round(250000 / d["median"], 1))

    def test_capacity_and_effort_limits(self):
        # 月の枠は既定3本 × 12 = 36本。工数の上限は既定15
        r = self.m.scenario(40, None, 2026, 5.0)
        self.assertEqual(r["placement"]["unplaced"], 4)
        self.assertFalse(r["feasible"])
        r = self.m.scenario(36, None, 2026, 6.0)                 # 月3本 × 6 = 18 > 15
        self.assertEqual(len(r["placement"]["over_months"]), 12)
        self.assertIn("工数の上限を超える", r["why_not"][0])
        r = self.m.scenario(24, None, 2026, 6.0)                 # 月2本 × 6 = 12
        self.assertTrue(r["feasible"])
        self.assertEqual(sum(m["n"] for m in r["placement"]["months"]), 24)

    def test_confirm_president_only_with_reason(self):
        with self.assertRaises(PermissionError):
            self.m.confirm(2026, 24, 3000000, 5.0, "理由", "someone")
        with self.assertRaises(ValueError):
            self.m.confirm(2026, 24, 3000000, 5.0, " ", "boss")
        self.m.confirm(2026, 24, 3000000, 5.0, "工数に収まる最大", "boss")
        self.m.confirm(2026, 36, 3000000, 4.0, "やはり36本", "boss")
        v = self.m.versions(2026)
        self.assertEqual([(x["params"]["n_releases"], x["state"]) for x in v], [(36, "確定"), (24, "取消")])
        self.assertIn("10 商品", v[0]["data_range"])

    def test_mix_follows_ratio_setting(self):
        self.assertEqual({k: v for k, v in self.m.mix(36).items() if k in ("uchiwa", "other")}, {"uchiwa": 9, "other": 27})
        self.assertEqual(self.m.mix(10)["uchiwa"], 2, "端数は うちわ以外 へ")

    def test_plan_ref_for_project_targets(self):
        """FR-177。発売日の年度で確定した計画の1本あたり（目標があれば 目標÷本数、無ければ 見込みの真ん中÷本数）。"""
        self.assertTrue(self.m.plan_ref("2026-12-01")["none"])
        self.assertIsNone(self.m.plan_ref(None))
        self.m.confirm(2026, 24, 2400000, 5.0, "検査", "boss")
        r = self.m.plan_ref("2027-03-10")                      # 2027年3月は 2026年度
        self.assertEqual((r["fy"], r["n"], r["each"]), (2026, 24, 100000))
        self.assertIn("目標 ÷ 本数", r["basis"])
        self.m.confirm(2027, 12, None, 5.0, "目標なし", "boss")
        r = self.m.plan_ref("2027-06-01")
        self.assertEqual(r["each"], round(self.m.versions(2027)[0]["result"]["p50"] / 12))
        self.assertIn("見込みの真ん中", r["basis"])


if __name__ == "__main__":
    unittest.main()
