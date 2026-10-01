#!/usr/bin/env python3
"""
第3段 原価・調達（ADR-047）の検査。

見ているのは:
  - **0 で埋めない。**分からない単価・使用量は未確定のまま、合計に「未確定を含む」と出る
  - 粗利は直接費（材料＋外注）だけを引き、**工数は引かない**
  - **前の版は書き換えられない。**新しい版は前の行を写す
  - 候補の単価は**写し**（候補を後で直しても試算は変わらない）
  - **不採用には理由が要る**
  - 発注の締切は、採用候補の最長リードタイムから。リードタイム不明なら「未確定」
  - G3「試算原価 v1」が版と行の有無で自動で埋まる
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Cost(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-01"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, cost
        seed.run()
        self.m = cost
        self.pid = project.create("u", expand=False, internal_name="原価検査", flow_type="meire",
                                  launch_date="2026-12-01")["id"]

    def tearDown(self):
        from app import store
        store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def line(self, vid, **f):
        f.setdefault("part", "本体")
        f.setdefault("name", "本体")
        return self.m.save_line(self.pid, vid, f, "u")

    def test_unknown_is_not_zero_and_labor_is_not_subtracted(self):
        v = self.m.new_version(self.pid, {"price_ex_tax": "1000", "confidence": "中"}, "u")
        self.line(v["id"], qty="2", unit_price="100")                 # 200
        self.line(v["id"], part="付属品", name="袋", qty="1")          # 単価が未確定
        self.line(v["id"], part="外注", name="印刷", qty="1", unit_price="150")
        self.line(v["id"], part="工数", name="組立", qty="1", unit_price="300")
        t = self.m.overview(self.pid)["versions"][0]["totals"]
        self.assertEqual(t["material"]["yen"], 200.0)
        self.assertEqual(t["material"]["unknown"], 1)
        self.assertEqual(t["direct"]["yen"], 350.0)
        self.assertEqual(t["direct"]["unknown"], 1)
        self.assertEqual(t["labor"]["yen"], 300.0)
        self.assertEqual(t["gross"]["yen"], 650.0, "工数は粗利から引かない")
        self.assertTrue(t["gross"]["overstated"], "未確定の行があると粗利は大きく出る")
        self.assertEqual(t["price_in_tax"], 1100)

    def test_all_unknown_material_is_none_not_zero(self):
        v = self.m.new_version(self.pid, {}, "u")
        self.line(v["id"], qty="1")
        t = self.m.overview(self.pid)["versions"][0]["totals"]
        self.assertIsNone(t["material"]["yen"])
        self.assertIsNone(t["gross"]["yen"])

    def test_previous_version_is_frozen_and_copied(self):
        v1 = self.m.new_version(self.pid, {"price_ex_tax": "900"}, "u")
        self.line(v1["id"], qty="1", unit_price="100")
        v2 = self.m.new_version(self.pid, {}, "u")
        self.assertEqual(v2["version"], 2)
        o = self.m.overview(self.pid)
        self.assertEqual(len(o["versions"][0]["lines"]), 1, "前の版の行を写す")
        self.assertEqual(o["versions"][0]["price_ex_tax"], 900.0)
        with self.assertRaises(ValueError):
            self.line(v1["id"], qty="1", unit_price="999")               # 前の版は直せない
        self.line(v2["id"], id=o["versions"][0]["lines"][0]["id"], qty="1", unit_price="120")
        o = self.m.overview(self.pid)
        self.assertEqual(o["versions"][1]["lines"][0]["unit_price"], 100.0, "v1 は変わらない")

    def test_candidate_price_is_copied_not_linked(self):
        c = self.m.save_candidate(self.pid, {"kind": "資材", "part": "本体", "supplier": "A社",
                                             "unit_price": "80", "lead_days": "20"}, "u")
        v = self.m.new_version(self.pid, {}, "u")
        self.line(v["id"], qty="1", candidate_id=str(c["id"]))
        self.m.save_candidate(self.pid, {"id": c["id"], "kind": "資材", "part": "本体",
                                         "supplier": "A社", "unit_price": "999"}, "u")
        line = self.m.overview(self.pid)["versions"][0]["lines"][0]
        self.assertEqual(line["unit_price"], 80.0)

    def test_not_adopted_needs_reason(self):
        c = self.m.save_candidate(self.pid, {"kind": "外注", "supplier": "B社"}, "u")
        with self.assertRaises(ValueError):
            self.m.adopt(self.pid, c["id"], False, "", "u")
        self.m.adopt(self.pid, c["id"], False, "最低ロットが多すぎる", "u")
        cand = self.m.candidates(self.pid)[0]
        self.assertEqual(cand["not_adopted_reason"], "最低ロットが多すぎる")

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.m.save_candidate(self.pid, {"kind": "資材", "supplier": "A"}, "u")   # 区分なし
        with self.assertRaises(ValueError):
            self.m.save_candidate(self.pid, {"kind": "外注", "supplier": "A", "unit_price": "-1"}, "u")
        with self.assertRaises(ValueError):
            self.m.save_candidate(self.pid, {"kind": "外注", "supplier": "A", "min_lot": "0"}, "u")
        with self.assertRaises(ValueError):
            self.m.save_candidate(self.pid, {"kind": "外注", "supplier": "A",
                                             "url": "javascript:alert(1)"}, "u")
        v = self.m.new_version(self.pid, {}, "u")
        with self.assertRaises(ValueError):
            self.line(v["id"], part="送料")

    def test_deadline(self):
        d = self.m.overview(self.pid)["deadline"]
        self.assertEqual(d["state"], "未確定")
        a = self.m.save_candidate(self.pid, {"kind": "資材", "part": "本体", "supplier": "A",
                                             "lead_days": "30"}, "u")
        b = self.m.save_candidate(self.pid, {"kind": "外注", "supplier": "B", "lead_days": "45"}, "u")
        self.m.adopt(self.pid, a["id"], True, "", "u")
        self.m.adopt(self.pid, b["id"], True, "", "u")
        d = self.m.overview(self.pid)["deadline"]
        self.assertEqual(d["due"], "2026-10-17")                     # 12/01 − 45日
        self.assertEqual(d["state"], "間に合う")
        self.assertEqual(d["by"], "B")
        c = self.m.save_candidate(self.pid, {"kind": "外注", "supplier": "C"}, "u")
        self.m.adopt(self.pid, c["id"], True, "", "u")
        self.assertEqual(self.m.overview(self.pid)["deadline"]["state"], "未確定")
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-20"
        self.m.adopt(self.pid, c["id"], False, "外す", "u")
        self.assertEqual(self.m.overview(self.pid)["deadline"]["state"], "間に合わない")

    def test_g3_cost_item(self):
        from app import gate
        miss = {m["key"] for m in gate.missing(self.pid, "G3")}
        self.assertIn("cost_v1", miss)
        v = self.m.new_version(self.pid, {}, "u")
        self.line(v["id"], part="工数", name="作業", qty="1", unit_price="10")
        self.assertIn("cost_v1", {m["key"] for m in gate.missing(self.pid, "G3")},
                      "工数だけでは試算原価にならない")
        self.line(v["id"], qty="1")
        self.assertNotIn("cost_v1", {m["key"] for m in gate.missing(self.pid, "G3")})

    def test_writes_are_committed(self):
        """**別の接続から見える。**確定しない書き込みは他の書き込みを待たせる。"""
        import sqlite3
        c = self.m.save_candidate(self.pid, {"kind": "外注", "supplier": "B"}, "u")
        self.m.adopt(self.pid, c["id"], True, "", "u")
        other = sqlite3.connect(self.dbfile)
        self.assertEqual(other.execute("SELECT adopted FROM sourcing_candidate").fetchone()[0], 1)
        other.close()


if __name__ == "__main__":
    unittest.main()
