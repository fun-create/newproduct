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

    def test_dept_shares_from_past_channels(self):
        """FR-133。部門ごとの割合＝過去のチャネル別売上の割合。Amazon は自社発送と FBA を分けない。"""
        import json as _j
        with self.store.tx() as c:
            c.execute("UPDATE past_product SET channels_json=? WHERE code='P001'",
                      (_j.dumps({"グッズ": 30000, "amazon": 20000, "楽天": 0}),))
        sh = {x["dept"]: x for x in self.m.dept_shares()}
        self.assertEqual((sh["funcreate_goods"]["share"], sh["amazon"]["share"]), (0.6, 0.4))
        self.assertNotIn("rakuten", sh, "0 のチャネルは出さない")
        self.assertIn("FBA", sh["amazon"]["note"])
        sc = self.m.scenario(12, None, 2026, 4.0)
        self.assertEqual(sum(x["yen"] for x in sc["by_dept"]), sc["p50"])

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

    def test_target_from_keiei_plan(self):
        """FR-125・126。目標＝経営管理の承認済みの計画（選んだ部門の合計）× 割合。複製しない（読むだけ）。"""
        from app import keiei
        doc = {"fy": 2026, "version": "FY2026-v02", "approved_at": "2026-10-09",
               "departments": [{"key": "funcreate_goods", "label": "ファンクリ（グッズ）"}, {"key": "rakuten", "label": "楽天市場"}],
               "plan": [{"dept_key": "funcreate_goods", "metric": "revenue", "month": "2026-09", "value": 60000000},
                        {"dept_key": "funcreate_goods", "metric": "revenue", "month": "2026-10", "value": 40000000},
                        {"dept_key": "rakuten", "metric": "revenue", "month": "2026-09", "value": 20000000},
                        {"dept_key": "rakuten", "metric": "margin", "month": "2026-09", "value": 999}]}
        orig = keiei.plan
        keiei.plan = lambda fy, opener=None: doc if fy == 2026 else {"fy": fy, "version": None, "plan": [], "reason": "未承認"}
        self.addCleanup(setattr, keiei, "plan", orig)
        r = self.m.compare(2026, None, [12], 5.0, 6, None)
        self.assertEqual(r["target_yen"], 7200000, "1.2億 × 6%（売上だけを足す・粗利の行は足さない）")
        self.assertIn("FY2026-v02", r["target_from_plan"]["basis"])
        r = self.m.compare(2026, None, [12], 5.0, 10, ["funcreate_goods"])
        self.assertEqual(r["target_yen"], 10000000)
        with self.assertRaises(ValueError):
            self.m.compare(2027, None, [12], 5.0, 6, None)            # 承認済みの版が無い年度
        self.assertIsNone(self.m.compare(2027, None, [12], 5.0)["keiei"]["version"])
        self.m.confirm(2026, 12, 6000000, 5.0, "計画の6%", "boss", "", "経営管理の計画の6%")
        self.assertEqual(self.m.versions(2026)[0]["params"]["target_basis"], "経営管理の計画の6%")

    def test_revision_of_keiei_plan_is_warned(self):
        """FR-132。確定した計画の元の版が改訂されたら、目標の差と未発売の案件（逆算法）の差を出す。"""
        from app import keiei, project, target
        def doc(ver, goods):
            return {"fy": 2026, "version": ver, "departments": [{"key": "funcreate_goods", "label": "グッズ"}],
                    "plan": [{"dept_key": "funcreate_goods", "metric": "revenue", "month": "2026-09", "value": goods}]}
        cur = {"d": doc("v01", 100000000)}
        orig = keiei.plan
        keiei.plan = lambda fy, opener=None: cur["d"]
        self.addCleanup(setattr, keiei, "plan", orig)
        r = self.m.compare(2026, None, [10], 5.0, 5, None)
        pf = r["target_from_plan"]
        self.m.confirm(2026, 10, pf["yen"], 5.0, "計画の5%", "boss", "", pf["basis"], pf)
        self.assertEqual(self.m.revision_check(2026), {"changed": False, "version": "v01"})
        pid = project.create("u", expand=False, internal_name="未発売の案件", flow_type="meire", launch_date="2026-12-01")["id"]
        target.save(pid, {"method": "逆算法", "annual_yen": "500000", "basis": "販売計画の1本あたり"}, "u")
        cur["d"] = doc("v02", 120000000)
        rv = self.m.revision_check(2026)
        self.assertEqual((rv["was"], rv["now"], rv["old_target"], rv["new_target"]), ("v01", "v02", 5000000, 6000000))
        self.assertEqual((rv["old_each"], rv["new_each"]), (500000, 600000))
        self.assertEqual([(x["name"], x["diff"]) for x in rv["projects"]], [("未発売の案件", 100000)])

    def test_handoff_spreads_target_over_months_and_depts(self):
        """FR-124。目標を発売月×売れ方の形×部門の割合で配る。受け口が無ければ中身を残して「未送信」。"""
        import json as _j
        from app import handoff
        with self.store.tx() as c:
            c.execute("UPDATE past_product SET months_json=?, channels_json=?",
                      (_j.dumps([1] * 12), _j.dumps({"グッズ": 3, "amazon": 1})))
        r = self.m.confirm(2026, 12, 1200000, 4.0, "検査", "boss")
        self.assertTrue(r["handoff"]["queued"])
        self.assertEqual(r["handoff"]["state"], "未送信", "受け口が無いので送らない（中身は残す）")
        pl = handoff.build(r["id"])
        self.assertAlmostEqual(pl["total"], 1200000, delta=len(pl["rows"]))     # 丸めの誤差だけ
        goods = sum(x["value"] for x in pl["rows"] if x["dept_key"] == "funcreate_goods")
        self.assertAlmostEqual(goods / pl["total"], 0.75, places=2)
        self.assertIn(2027, {x["fy"] for x in pl["rows"]}, "年度の終わりに出した商品の売上は翌年度に入る")
        self.assertEqual(pl["basis_json"]["target_yen"], 1200000)
        sent = []

        class Res:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{}"
        handoff.INTAKE = "http://127.0.0.1:1/x"
        self.addCleanup(setattr, handoff, "INTAKE", "")
        self.addCleanup(setattr, handoff, "TOKEN_FILE", handoff.TOKEN_FILE)
        handoff.TOKEN_FILE = __file__
        oid = handoff.latest(2026)["id"]
        self.assertEqual(handoff.send(oid, opener=lambda req, timeout: (sent.append(req), Res())[1])["state"], "送信済")
        self.assertEqual(_j.loads(sent[0].data)["basis_json"]["sim_plan_id"], r["id"])
        with self.assertRaises(ValueError):
            handoff.build(self.m.confirm(2027, 12, None, 4.0, "目標なし", "boss")["id"])

    def test_keiei_not_connected_is_said(self):
        os.environ["NEWPRODUCT_KEIEI_TOKEN"] = "/nonexistent/keiei_token"
        self.addCleanup(os.environ.pop, "NEWPRODUCT_KEIEI_TOKEN", None)
        import importlib
        from app import keiei
        importlib.reload(keiei)
        self.addCleanup(importlib.reload, keiei)
        r = self.m.compare(2030, 1000000, [12], 5.0)
        self.assertIn("つながっていません", r["keiei"]["why"])


if __name__ == "__main__":
    unittest.main()
