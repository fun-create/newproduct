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
        self.assertEqual(cols, FS_GOODS)                     # 見出しは店の出力と同じ（落とした列も並べ、中身は空・ADR-154）
        r = self.m.roundtrip("goods", [self.dir])
        self.assertTrue(r["ok"], r)
        self.assertEqual({k: v["orig"] for k, v in r["kinds"].items()}, {"goods": 2, "selection": 2, "opt_basic": 1, "opt_select": 1})
        data = self.m.to_csv("goods", "goods", self.m.rows("goods", "goods"))
        self.assertTrue(data.startswith("コントロールカラム,".encode("cp932")))
        self.assertIn(b"\r\n", data)
        back = list(csv.DictReader(io.StringIO(data.decode("cp932"), newline="")))
        self.assertEqual((back[0]["商品説明（大）"], back[0]["現在在庫数"]), ("", ""))   # 取り込まない列は空（更新で空＝変えない）
        u = list(csv.DictReader(io.StringIO(self.m.to_csv("goods", "goods", self.m.rows("goods", "goods"), "u").decode("cp932"), newline="")))
        self.assertEqual({r["コントロールカラム"] for r in u}, {"u"})
        with self.assertRaises(ValueError):
            self.m.to_csv("goods", "selection", self.m.rows("goods", "selection"), "u")   # 項目選択肢は更新できない
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

    def test_manual_rules(self):
        # マニュアルの決まり（072）: 商品URLコードの形・商品番号の ~・商品名の HTML・項目選択肢の / と ~
        self.store.ex("UPDATE listing SET raw_json=json_set(raw_json, '$.商品番号', 'A~1', '$.商品名', '<b>太字</b>') WHERE ext_id='a001'")
        self.store.ex("UPDATE listing_option SET raw_json=json_set(raw_json, '$.\"セレクト／ラジオボタン用項目名\"', '台座/色') WHERE src='selection' AND item_name='台座'")
        self.store.conn().commit()
        errs = " ".join(self.m.check("goods")["errors"])
        for w in ("商品番号 に使えない文字", "<br> 以外の HTML", "セレクト／ラジオボタン用項目名 に使えない文字"):
            self.assertIn(w, errs)
        self.store.ex("UPDATE listing SET raw_json=json_set(raw_json, '$.商品番号', 'A-1', '$.商品名', '名前<br>二行') WHERE ext_id='a001'")
        self.store.ex("UPDATE listing SET raw_json=json_set(raw_json, '$.商品URLコード', 'x') WHERE ext_id='a002'")   # 1文字は形に合わない
        self.store.conn().commit()
        errs = self.m.check("goods")["errors"]
        self.assertFalse(any("<br> 以外" in e for e in errs))                    # <br> は通る
        self.assertTrue(any("商品URLコード の形" in e for e in errs))
        self.assertTrue(any("項目選択肢は更新できません" in w for w in self.m.check("goods")["warnings"]))

    def test_check(self):
        c = self.m.check("goods")
        self.assertEqual((c["listings"], c["errors"]), (2, []))
        self.assertIn(self.m.UNVERIFIED, c["warnings"])
        self.store.ex("UPDATE listing SET raw_json=json_set(raw_json, '$.ステータス', '2', '$.本体価格', '千円', '$.商品名', '寿司🍣の名入れ') WHERE ext_id='a001'")
        self.store.ex("DELETE FROM cart_rule WHERE rule_key='max_chars:goods:商品名'")
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
