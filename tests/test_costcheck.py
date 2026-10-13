#!/usr/bin/env python3
"""
発売後の「試算 vs 確定」（FR-93・ADR-095）の検査。

見ているのは:
  - 発売前・コード未登録・試算なし・CIP の口なし を、それぞれ理由つきで出す（0 で比べない）
  - 1個あたりは「原価が出た行の数量」で割る（原価の出ない行の数量で薄めない）
  - 受注が無いときだけマスタの材料費と比べ、そう書く
  - 60日が締まるまでは途中経過で、残さない。締まったら1回だけ残し、後で seisan の値が変わっても残した値は変わらない
  - seisan から名前が返ってきても持たない
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


class FakeSeisan:
    def __init__(self):
        self.calls = []
        self.items = {}
        self.missing = False

    def __call__(self, method, path, params, body):
        self.calls.append((method, path, params))
        if self.missing:
            return 404, {"error": "ありません"}          # 本物の seisan が知らない口に返す形
        if path == "/api/svc/cost/actuals":
            codes = params["codes"].split(",")
            return 200, {"as_of": "2026-12-31 08:00", "yield_pct_now": 100,
                         "items": [{"code": c, "name": "個人名が混ざる列", **self.items[c]}
                                   for c in codes if c in self.items]}
        return 404, {}


class CostCheck(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-01"
        self.dbfile = p
        self.envdir = tempfile.mkdtemp()
        self.envfile = Path(self.envdir) / "seisan.env"
        self.envfile.write_text("SEISAN_SVC_TOKEN=test-token\n", encoding="utf-8")
        os.environ["NEWPRODUCT_SEISAN_ENV"] = str(self.envfile)
        from app import store
        store.close()
        from app import seed, project, cost, seisan, costcheck
        seed.run()
        self.store, self.cost, self.m = store, cost, costcheck
        self.fake = FakeSeisan()
        self._orig = seisan.transport
        seisan.transport = self.fake
        self.seisan = seisan
        self.pid = project.create("u", expand=False, internal_name="突合検査", flow_type="meire",
                                  launch_date="2026-10-01")["id"]

    def tearDown(self):
        self.seisan.transport = self._orig
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass
        os.environ.pop("NEWPRODUCT_SEISAN_ENV", None)
        os.environ.pop("NEWPRODUCT_TODAY", None)

    # ── 下ごしらえ ──
    def launched(self):
        self.store.ex("UPDATE project SET stage='発売済' WHERE id=?", (self.pid,))
        self.store.conn().commit()

    def code(self, code="NEW-1"):
        self.store.ex("INSERT INTO seisan_registration (project_id,state,product_code,via,registered_at) "
                      "VALUES (?,'登録済',?,'seisan',?)", (self.pid, code, self.store.now_s()))
        self.store.conn().commit()

    def estimate(self, material="100", outsource=None, unknown=False):
        v = self.cost.new_version(self.pid, {"price_ex_tax": "1000", "confidence": "中"}, "u")
        self.cost.save_line(self.pid, v["id"], {"part": "本体", "name": "本体", "qty": "1",
                                                "unit_price": material}, "u")
        if outsource:
            self.cost.save_line(self.pid, v["id"], {"part": "外注", "name": "印刷", "qty": "1",
                                                    "unit_price": outsource}, "u")
        if unknown:
            self.cost.save_line(self.pid, v["id"], {"part": "付属品", "name": "袋", "qty": "1"}, "u")
        return v

    def orders(self, code="NEW-1", **o):
        base = {"lines": 10, "qty": 20, "sales": 20000, "costed_lines": 8, "qty_costed": 16,
                "sales_costed": 16000, "material_costed": 1920, "outsource_costed": 0,
                "no_material_lines": 2, "no_oz_price_lines": 0, "zero_material_lines": 0,
                "outsourced_lines": 0, "labor_lines": 0, "qty_labor": 0, "labor_yen": 0}
        base.update(o)
        self.fake.items[code] = {"found": True, "orders": base,
                                 "master": {"material_cost": 130, "labor_cost": None, "labor_minutes": None}}

    def at(self, ymd):
        return dt.date.fromisoformat(ymd)

    # ── 検査 ──
    def test_states_before_comparing(self):
        self.assertEqual(self.m.check(self.pid, "u", self.at("2026-10-05"))["state"], "発売前")
        self.launched()
        self.assertEqual(self.m.check(self.pid, "u", self.at("2026-10-05"))["state"], "コード未登録")
        self.code()
        self.assertEqual(self.m.check(self.pid, "u", self.at("2026-10-05"))["state"], "試算なし")
        self.assertEqual(self.fake.calls, [], "比べる相手が無いうちは CIP を呼ばない")

    def test_missing_endpoint_says_so(self):
        self.launched(); self.code(); self.estimate()
        self.fake.missing = True
        r = self.m.check(self.pid, "u", self.at("2026-10-05"))
        self.assertEqual(r["state"], "CIP の口なし")
        self.assertIn("吉田さん", r["why"])
        self.assertIn("まだありません", r["why"], "「ありません」だけでは何が無いか分からない")

    def test_per_unit_uses_costed_qty_and_direct_cost(self):
        self.launched(); self.code(); self.estimate(material="100", outsource="20")
        self.orders(material_costed=1920, outsource_costed=320)     # (1920+320)/16 = 140
        r = self.m.check(self.pid, "u", self.at("2026-10-20"))
        i = r["items"][0]
        self.assertEqual(r["state"], "途中経過")
        self.assertEqual(i["actual"]["basis"], "受注")
        self.assertEqual(i["actual"]["direct"], 140.0, "原価が出た行の数量（16）で割る。20 で割ると薄まる")
        self.assertEqual(i["diff"]["yen"], 20.0)
        self.assertEqual(i["diff"]["pct"], 16.7)
        self.assertEqual(i["diff"]["word"], "試算より高い")
        self.assertEqual(i["actual"]["no_material_lines"], 2)
        self.assertEqual(self.fake.calls[0][2], {"codes": "NEW-1", "from": "2026-10-01", "to": "2026-10-19"},
                         "今日の分は締まっていないので前日まで")
        self.assertNotIn("recorded", i, "途中経過は残さない")
        self.assertNotIn("name", str(i), "seisan の名前を持たない")

    def test_master_only_when_no_costed_orders(self):
        self.launched(); self.code(); self.estimate(material="100")
        self.orders(costed_lines=0, qty_costed=0, material_costed=0)
        i = self.m.check(self.pid, "u", self.at("2026-10-20"))["items"][0]
        self.assertEqual(i["actual"]["basis"], "マスタ")
        self.assertEqual(i["actual"]["direct"], 130.0)

    def test_unknown_estimate_lines_are_reported(self):
        self.launched(); self.code(); self.estimate(unknown=True)
        r = self.m.check(self.pid, "u", self.at("2026-10-20"))
        self.assertEqual(r["estimate"]["unknown"], 1)

    def test_records_once_after_60_days_and_keeps_it(self):
        self.launched(); self.code(); self.estimate(material="100")
        self.orders()                                              # 1920/16 = 120
        r = self.m.check(self.pid, "u", self.at("2026-12-15"))
        self.assertEqual(r["state"], "60日分")
        self.assertEqual(self.fake.calls[-1][2]["to"], "2026-11-29", "発売日を含めて60日")
        i = r["items"][0]
        self.assertEqual(i["recorded"]["diff"]["yen"], 20.0)
        # seisan が後で計算し直しても、残した値は変わらない
        self.orders(material_costed=2400)                          # 150
        r2 = self.m.check(self.pid, "u", self.at("2026-12-20"))
        self.assertEqual(r2["items"][0]["recorded"]["actual"]["direct"], 120.0)
        self.assertEqual(r2["items"][0]["actual"]["direct"], 150.0, "いまの値は並べて出す")
        n = self.store.one("SELECT COUNT(*) FROM cost_check")[0]
        self.assertEqual(n, 1)

    def test_recorded_value_survives_missing_endpoint(self):
        self.launched(); self.code(); self.estimate(material="100")
        self.orders()
        self.m.check(self.pid, "u", self.at("2026-12-15"))
        self.fake.missing = True
        r = self.m.check(self.pid, "u", self.at("2026-12-20"))
        self.assertEqual(r["state"], "60日分")
        self.assertEqual(r["items"][0]["recorded"]["actual"]["direct"], 120.0)

    def test_board_and_record_due(self):
        self.launched(); self.code(); self.estimate(material="100")
        self.orders()
        d = self.m.record_due(self.at("2026-12-15"))
        self.assertEqual(d["recorded_now"], 1)
        self.assertEqual(self.m.record_due(self.at("2026-12-16"))["recorded_now"], 0, "二度残さない")
        b = self.m.board("u", self.at("2026-12-16"))
        self.assertEqual(b["rows"][0]["max_abs_pct"], 20.0)
        self.assertEqual(b["counts"], {"60日分": 1})


if __name__ == "__main__":
    unittest.main()
