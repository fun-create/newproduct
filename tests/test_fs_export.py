#!/usr/bin/env python3
"""FutureShop の書き出し（段階4・ADR-151）: 往復の検査（取り込んだ元と同じ中身）・壊すと違いが出る・
書き出す前の検査（必須・ステータス・価格・Shift_JIS で書けない文字・文字数の決まり）・列の並びは取り込んだファイルのまま。"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.test_listing import fs_files, FS_GOODS  # noqa: E402


class FsExport(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, listing, fs_export
        seed.run()
        store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES ('president','boss')")
        store.conn().commit()
        self.store, self.listing, self.m = store, listing, fs_export
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "goods"
        fs_files(self.dir)
        listing.apply("goods", [self.dir], "boss")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_layout_and_roundtrip(self):
        cols = self.m.layout("goods", "goods")
        self.assertEqual(cols, [c for c in FS_GOODS if c not in ("商品説明（大）", "独自コメント（1）", "現在在庫数")])   # 落とした列は並びにも無い
        r = self.m.roundtrip("goods", [self.dir])
        self.assertTrue(r["ok"], r)
        self.assertEqual({k: v["orig"] for k, v in r["kinds"].items()}, {"goods": 2, "selection": 2, "opt_basic": 1, "opt_select": 1})
        data = self.m.to_csv("goods", "goods", self.m.rows("goods", "goods"))
        self.assertTrue(data.startswith("コントロールカラム,".encode("cp932")))
        self.assertIn(b"\r\n", data)
        # 壊すと違いが出る（台帳の掲載名を直す）
        self.store.ex("UPDATE listing SET raw_json=json_set(raw_json, '$.商品名', '別の名前') WHERE ext_id='a001'")
        self.store.conn().commit()
        r = self.m.roundtrip("goods", [self.dir])
        self.assertFalse(r["ok"])
        self.assertEqual((r["kinds"]["goods"]["only_in_file"], r["kinds"]["goods"]["only_in_ledger"]), (1, 1))
        with self.assertRaises(ValueError):
            self.m.rows("rakuten", "goods")
        with self.assertRaises(LookupError):
            self.m.layout("uchiwa", "goods")                              # まだ取り込んでいない店

    def test_check(self):
        c = self.m.check("goods")
        self.assertEqual((c["listings"], c["errors"]), (2, []))
        self.assertIn(self.m.UNVERIFIED, c["warnings"])
        self.store.ex("UPDATE listing SET raw_json=json_set(raw_json, '$.ステータス', '2', '$.本体価格', '千円', '$.商品名', '寿司🍣の名入れ') WHERE ext_id='a001'")
        self.store.ex("INSERT INTO cart_rule (cart, rule_key, value, source, checked_on) VALUES ('futureshop','max_chars:商品名','5','試し','2026-11-13')")
        self.store.conn().commit()
        errs = " ".join(self.m.check("goods")["errors"])
        for w in ("ステータスは 0", "本体価格が数ではありません", "Shift_JIS で書けない", "5 文字を超えて"):
            self.assertIn(w, errs)
        aid = self.store.val("SELECT id FROM listing WHERE ext_id='a002'")
        self.assertEqual(self.m.check("goods", [aid])["errors"], [])        # 選んだ出品だけ検査する
        with self.assertRaises(UnicodeEncodeError):
            self.m.to_csv("goods", "goods", self.m.rows("goods", "goods"))
        rs = self.m.rows("goods", "selection", [aid])
        self.assertEqual(rs, [])                                             # a002 に選択肢は無い
        back = list(csv.DictReader(io.StringIO(self.m.to_csv("goods", "goods", self.m.rows("goods", "goods", [aid])).decode("cp932"), newline="")))
        self.assertEqual((len(back), back[0]["商品URLコード"], back[0]["キャッチコピー"]), (1, "a002", ""))


if __name__ == "__main__":
    unittest.main()
