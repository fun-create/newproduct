#!/usr/bin/env python3
"""
採点の校正（FR-111・ADR-101）の検査。

見ているのは:
  - 突き合わせられない理由（アイデアにつながっていない・点がない・売上が未計測・12か月に満たない）を数えて出す
  - 件数が MIN_N に満たない版は相関を出さず「未計測」
  - そろえば軸ごとの順位相関が出て、同点ばかりの軸は「並べられない」
  - 当たった・外したの記録は業務ロールの人だけ。理由は必須。rubric の版と軸を検査する
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


class Calibrate(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-17"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, calibrate, sales
        seed.run()
        self.store, self.m, self.sales = store, calibrate, sales
        self._orig_ps = sales.project_sales
        self.revenue = {}
        def fake_ps(pid):
            if pid not in self.revenue:
                return {"why": "CIP への登録（共通商品コード）がまだありません"}
            p = store.one("SELECT launch_date FROM project WHERE id=?", (pid,))
            ym = p["launch_date"][:7]
            return {"why": None, "sites": [{"months": [{"month": ym, "revenue": self.revenue[pid], "qty": 1}]}]}
        sales.project_sales = fake_ps
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('devdept','dev',?)", (store.now_s(),))

    def tearDown(self):
        self.sales.project_sales = self._orig_ps
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass
        os.environ.pop("NEWPRODUCT_TODAY", None)

    def _launched(self, name, launch, idea_id=None, score=None, version="v2"):
        from app import project
        pid = project.create("u", expand=False, internal_name=name, flow_type="meire", launch_date=launch,
                             idea_id=idea_id)["id"]
        self.store.ex("UPDATE project SET stage='発売済' WHERE id=?", (pid,))
        if idea_id and score is not None:
            import json
            self.store.ex("INSERT INTO idea_score (idea_id,rubric_version,axes,total,rank,rank_basis,scored_by,scored_at) "
                          "VALUES (?,?,?,?,?,?,?,?)", (idea_id, version, json.dumps(score["axes"]), score["total"], "A",
                                                       "percentile", "human", self.store.now_s()))
        self.store.conn().commit()
        return pid

    def _idea(self, title):
        from tests.test_ideas import _idea
        return _idea(title)

    def test_reasons_are_counted_and_nothing_is_measured_with_few_pairs(self):
        self._launched("移行分", "2025-01-10")                                   # アイデア無し
        i1 = self._idea("点なし")
        self._launched("点なし", "2025-02-10", i1)
        i2 = self._idea("売上なし")
        self._launched("売上なし", "2025-03-10", i2, {"axes": {"demand": 8}, "total": 70})
        i3 = self._idea("途中")
        p3 = self._launched("途中", "2026-06-01", i3, {"axes": {"demand": 5}, "total": 50})
        self.revenue[p3] = 1000
        v = self.m.view("dev")
        self.store.ex("UPDATE project SET launch_date=NULL WHERE internal_name='移行分'")
        self.store.conn().commit()
        v = self.m.view("dev")
        self.assertEqual(v["pairs"]["skipped"], {"発売日なし": 1, "アイデアにつながっていない": 0, "点がない": 1, "売上が未計測": 1, "12か月に満たない": 1})
        self.assertEqual(v["pairs"]["usable"], 0)
        self.assertEqual(v["check"]["versions"], [], "そろっていなければ相関を出さない")
        row = [r for r in v["pairs"]["rows"] if r["project_id"] == p3][0]
        self.assertFalse(row["actual"]["complete"])
        self.assertIn("12か月そろうのは", row["why"])

    def test_correlation_once_min_n_pairs_exist(self):
        n = self.m.MIN_N
        for k in range(n):
            i = self._idea(f"案{k}")
            pid = self._launched(f"案{k}", "2025-01-10", i, {"axes": {"demand": k + 1, "advantage": 5}, "total": 40 + k})
            self.revenue[pid] = 10000 * (k + 1)
        v = self.m.view("dev")
        self.assertEqual(v["pairs"]["usable"], n)
        ver = v["check"]["versions"][0]
        self.assertEqual((ver["version"], ver["n"], ver["state"]), ("v2", n, "計測"))
        ax = {a["code"]: a for a in ver["axes"]}
        self.assertEqual(ax["demand"]["rho"], 1.0)
        self.assertIn("当たっている", ax["demand"]["word"])
        self.assertIsNone(ax["advantage"]["rho"], "全部同点の軸は並べられない")
        self.assertEqual(ax["advantage"]["word"], "並べられない")
        self.assertEqual(ax["total"]["rho"], 1.0)
        # 1件足りないと未計測
        self.store.ex("UPDATE project SET stage='中止' WHERE internal_name='案0'")
        self.store.conn().commit()
        ver = self.m.view("dev")["check"]["versions"][0]
        self.assertEqual(ver["state"], "未計測")
        ax = {a["code"]: a for a in ver["axes"]}
        self.assertIsNone(ax["demand"]["rho"], "件数が足りないあいだは、並べられる軸でも相関を出さない")
        self.assertEqual(ax["demand"]["word"], "未計測")

    def test_spearman_ties_and_reverse(self):
        self.assertEqual(self.m.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertEqual(self.m.spearman([1, 1, 2, 2], [1, 1, 2, 2]), 1.0)
        self.assertIsNone(self.m.spearman([1], [1]))
        self.assertEqual(self.m.word(-0.6), "逆（点が高いほど売れていない）")

    def test_note_needs_role_reason_and_known_axis(self):
        with self.assertRaises(PermissionError):
            self.m.add_note({"rubric_version": "v2", "axis": "demand", "verdict": "当たった", "reason": "x"}, "nobody")
        with self.assertRaises(ValueError):
            self.m.add_note({"rubric_version": "v2", "axis": "demand", "verdict": "当たった", "reason": ""}, "dev")
        with self.assertRaises(ValueError):
            self.m.add_note({"rubric_version": "v2", "axis": "no-such", "verdict": "当たった", "reason": "x"}, "dev")
        with self.assertRaises(ValueError):
            self.m.add_note({"rubric_version": "v9", "axis": "demand", "verdict": "当たった", "reason": "x"}, "dev")
        r = self.m.add_note({"rubric_version": "v2", "axis": "total", "verdict": "保留", "reason": "件数が足りない", "n_pairs": "3", "rho": ""}, "dev")
        self.assertTrue(r["ok"])
        n = self.m.notes()[0]
        self.assertEqual((n["axis"], n["verdict"], n["n_pairs"], n["rho"]), ("total", "保留", 3, None))


if __name__ == "__main__":
    unittest.main()
