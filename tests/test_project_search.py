#!/usr/bin/env python3
"""案件の検索窓（2026-11-12 石原さん）。社内呼称・ID に語が全部含まれるもの。% や _ は文字として探す。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class Search(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project
        seed.run()
        self.store, self.p = store, project
        self.a = project.create("u", expand=False, internal_name="ビールジョッキ再販", flow_type="meire")["id"]
        self.b = project.create("u", expand=False, internal_name="iPhone DUO スキンシール", flow_type="newmodel")["id"]
        self.c = project.create("u", expand=False, internal_name="割引100%_テスト", flow_type="meire")["id"]
        store.ex("UPDATE project SET stage='発売済' WHERE id=?", (self.a,))
        store.conn().commit()

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def ids(self, **a):
        return {r["id"] for r in self.p.listing({"stage": "_all", **a})["rows"]}

    def test_words_all_must_match(self):
        self.assertEqual(self.ids(q="ジョッキ"), {self.a})
        self.assertEqual(self.ids(q="iPhone スキン"), {self.b})
        self.assertEqual(self.ids(q="iPhone ジョッキ"), set())
        self.assertEqual(self.ids(q=self.b), {self.b})                    # ID でも
        self.assertEqual(self.ids(q="ジョッキ", stage="開発中"), set())     # ステージと重ねられる

    def test_wildcards_are_literal(self):
        self.assertEqual(self.ids(q="%"), {self.c})
        self.assertEqual(self.ids(q="_"), {self.c})
        self.assertEqual(self.ids(q=""), {self.a, self.b, self.c})


if __name__ == "__main__":
    unittest.main()
