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

    def test_board_puts_late_first_and_hides_launched(self):
        from app import project, store
        late = project.create("u", expand=False, internal_name="遅い", flow_type="meire",
                              launch_date="2026-10-10")["id"]
        c = self.m.save_candidate(late, {"kind": "外注", "supplier": "X", "lead_days": "30"}, "u")
        self.m.adopt(late, c["id"], True, "", "u")
        done = project.create("u", expand=False, internal_name="発売済", flow_type="meire",
                              launch_date="2026-01-01")["id"]
        with store.tx() as cx:
            cx.execute("UPDATE project SET stage='発売済' WHERE id=?", (done,))
        rows = self.m.board()
        self.assertEqual(rows[0]["id"], late)
        self.assertEqual(rows[0]["deadline"]["state"], "間に合わない")
        self.assertNotIn(done, [r["id"] for r in rows])

    def test_warning_before_g3_does_not_block(self):
        from app import project, gate
        d = project.detail(self.pid, "u")
        # 次の関門が G3／G4 でなければ出さない（いまは G0）
        self.assertEqual(d["warnings"], [])
        self.assertEqual(project._gate_warnings({"id": self.pid, "launch_date": "2026-12-01"},
                                                {"gate": "G3"})[0]["state"], "未確定")
        # **止めない**：G3 の欠落に締切の項目は増えない
        self.assertNotIn("deadline", {m["key"] for m in gate.missing(self.pid, "G3")})

    def test_writes_are_committed(self):
        """**別の接続から見える。**確定しない書き込みは他の書き込みを待たせる。"""
        import sqlite3
        c = self.m.save_candidate(self.pid, {"kind": "外注", "supplier": "B"}, "u")
        self.m.adopt(self.pid, c["id"], True, "", "u")
        other = sqlite3.connect(self.dbfile)
        self.assertEqual(other.execute("SELECT adopted FROM sourcing_candidate").fetchone()[0], 1)
        other.close()



class RegisterToSeisan(Cost):
    """採用した候補を seisan に登録する（FR-184）。seisan の口は偽物（外へ出さない）。"""

    def setUp(self):
        super().setUp()
        from app import seisan, store
        self.s = seisan
        self.calls = []
        self.refuse = {}
        self._orig = {k: getattr(seisan, k) for k in
                      ("material_create", "material_order_params", "outsourcer_save",
                       "outsource_price_save")}

        def fake(name, ret):
            def f(*a, **k):
                self.calls.append((name, a))
                if name in self.refuse:
                    raise seisan.Refused(self.refuse[name])
                return ret
            return f
        seisan.material_create = fake("material_create", {"ok": True})
        seisan.material_order_params = fake("material_order_params", {"ok": True})
        seisan.outsourcer_save = fake("outsourcer_save", {"ok": True, "id": 7})
        seisan.outsource_price_save = fake("outsource_price_save", {"ok": True})
        store.ex("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('admin','kanri',?)",
                 (store.now_s(),))
        store.conn().commit()

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(self.s, k, v)
        super().tearDown()

    def adopted(self, **f):
        c = self.m.save_candidate(self.pid, f, "u")
        self.m.adopt(self.pid, c["id"], True, "", "u")
        return c["id"]

    def test_material_is_created_then_conditions(self):
        cid = self.adopted(kind="資材", part="本体", supplier="A社", unit_price="80",
                           lead_days="20", min_lot="100")
        r = self.m.register_candidate(self.pid, cid, {"code": "NP-1", "material_kind": "原材料",
                                                      "category": "アクリル", "name": "板"}, "kanri")
        self.assertEqual(r["seisan_ref"], "material:NP-1/原材料")
        self.assertEqual([c[0] for c in self.calls], ["material_create", "material_order_params"])
        create = self.calls[0][1]
        self.assertEqual(create[1]["unit_price"], 80.0, "単価は候補から")
        self.assertEqual(create[2], "kanri", "押した人を actor で渡す（seisan 側で管理者か確かめる）")
        params = self.calls[1][1][2]
        self.assertEqual((params["supplier"], params["lead_days"], params["lot_size"]),
                         ("A社", 20.0, 100.0))
        with self.assertRaises(ValueError):
            self.m.register_candidate(self.pid, cid, {"code": "NP-1"}, "kanri")   # 二度は登録しない

    def test_unknown_values_are_sent_blank_not_zero(self):
        cid = self.adopted(kind="資材", part="本体", supplier="A社")
        self.m.register_candidate(self.pid, cid, {"code": "NP-2", "material_kind": "原材料",
                                                  "category": "x", "name": "x"}, "kanri")
        params = self.calls[1][1][2]
        self.assertEqual((params["lead_days"], params["lot_size"]), ("", ""))
        self.assertIsNone(self.calls[0][1][1]["unit_price"])

    def test_refused_create_records_nothing(self):
        self.refuse["material_create"] = "材料コード「NP-1」は既に存在します"
        cid = self.adopted(kind="資材", part="本体", supplier="A社")
        with self.assertRaises(ValueError):
            self.m.register_candidate(self.pid, cid, {"code": "NP-1", "material_kind": "原材料",
                                                      "category": "x", "name": "x"}, "kanri")
        self.assertIsNone(self.m.candidates(self.pid)[0]["seisan_ref"])

    def test_outsourcer_new_and_price_partial(self):
        self.refuse["outsource_price_save"] = "商品コード「X」は商品マスタにありません"
        cid = self.adopted(kind="外注", supplier="B社", unit_price="150", lead_days="14")
        r = self.m.register_candidate(self.pid, cid, {"target_kind": "商品", "target_key": "X"}, "kanri")
        self.assertEqual(r["seisan_ref"], "outsourcer:7")
        self.assertIn("途中まで登録", r["note"])
        self.assertEqual(self.calls[0][1][0]["name"], "B社")

    def test_only_adopted_and_only_g5_role(self):
        c = self.m.save_candidate(self.pid, {"kind": "外注", "supplier": "B"}, "u")
        with self.assertRaises(ValueError):
            self.m.register_candidate(self.pid, c["id"], {}, "kanri")
        cid = self.adopted(kind="外注", supplier="C")
        with self.assertRaises(PermissionError):
            self.m.register_candidate(self.pid, cid, {}, "u")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
