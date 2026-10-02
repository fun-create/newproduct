#!/usr/bin/env python3
"""LP依頼書（FR-118）。カルテにあることだけを並べ、無いものは「未記入」と書く。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Lp(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, lpreq
        seed.run()
        self.m, self.project, self.store = lpreq, project, store
        self.pid = project.create("u", expand=False, internal_name="推しうちわ枠", flow_type="meire",
                                  launch_date="2026-12-01")["id"]

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_missing_is_written_not_blank(self):
        r = self.m.build(self.pid)
        self.assertIn("コンセプト", r["missing"])
        self.assertIn("（未記入）", r["text"])
        self.assertIn("埋まっていない項目", r["text"])
        self.assertIn("推しうちわ枠", r["text"])

    def test_sections_price_variants_and_codes(self):
        from app import cost
        self.project.save_section(self.pid, "C.concept", "推しの色で作る", "u")
        self.project.save_section(self.pid, "C.diff", "色が選べる\n名入れ無料\n即日発送", "u")
        v = cost.new_version(self.pid, {"price_ex_tax": "1000"}, "u")
        self.project.save_variant(self.pid, {"label": "大"}, "u")
        self.project.save_variant(self.pid, {"label": "小", "state": "見送り"}, "u")
        with self.store.tx() as c:
            c.execute("INSERT INTO seisan_registration (project_id,state,product_code) VALUES (?,?,?)",
                      (self.pid, "登録済", "NP-9"))
        t = self.m.build(self.pid)["text"]
        self.assertIn("推しの色で作る", t)
        self.assertIn("・名入れ無料", t)
        self.assertIn("1,000円（税抜）／1,100円（税込）", t)
        self.assertIn("・大", t)
        self.assertNotIn("・小", t, "見送りのバリエーションは載せない")
        self.assertIn("NP-9", t)
        self.assertNotIn("コンセプト", self.m.build(self.pid)["missing"])


if __name__ == "__main__":
    unittest.main()
