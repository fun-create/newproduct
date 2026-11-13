#!/usr/bin/env python3
"""楽天・Yahoo・ギフトモールの往復の検査（ADR-153）: 楽天の商品オプションを値ごとの行から「選択肢N」の列へ戻す・
SKU の行・Yahoo の options・ギフトモールの4種。台帳を壊すと違いが出る。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.test_listing import write  # noqa: E402


class MallExport(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, listing, mall_export
        seed.run()
        store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES ('president','boss')")
        store.conn().commit()
        self.store, self.listing, self.m = store, listing, mall_export
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_rakuten(self):
        r = self.dir / "rakuten"
        r.mkdir()
        head = ["商品管理番号（商品URL）", "商品番号", "商品名", "倉庫指定", "PC用商品説明文", "選択肢タイプ", "商品オプション項目名",
                "商品オプション選択肢1", "商品オプション選択肢2", "商品オプション選択肢3", "商品オプション選択必須", "SKU管理番号",
                "バリエーション項目選択肢1", "通常購入販売価格"]
        write(r / "dl-normal-item_1.csv", head, [
            {"商品管理番号（商品URL）": "r1", "商品名": "うちわ", "倉庫指定": "0", "PC用商品説明文": "落とす列"},
            {"商品管理番号（商品URL）": "r1", "選択肢タイプ": "s", "商品オプション項目名": "色", "商品オプション選択肢1": "赤",
             "商品オプション選択肢3": "青", "商品オプション選択必須": "1"},                        # 2 番目が空でも番号は保つ
            {"商品管理番号（商品URL）": "r1", "選択肢タイプ": "f", "商品オプション項目名": "お名前"},   # 自由入力（値なし）
            {"商品管理番号（商品URL）": "r1", "SKU管理番号": "r1-m", "バリエーション項目選択肢1": "M", "通常購入販売価格": "800"},
            {"商品管理番号（商品URL）": "r2", "商品名": "アクスタ", "倉庫指定": "1"}])
        self.listing.apply("rakuten", [r], "boss")
        out = self.m.roundtrip("rakuten", [r])
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["orig"], 5)
        self.store.ex("UPDATE listing_option SET sort=2 WHERE value='青'")             # 番号を壊すと違いが出る
        self.store.conn().commit()
        self.assertFalse(self.m.roundtrip("rakuten", [r])["ok"])

    def test_yahoo_and_giftmall(self):
        y = self.dir / "yahoo"
        y.mkdir()
        write(y / "data1.csv", ["path", "name", "code", "price", "options", "display", "caption"],
              [{"name": "名入れ", "code": "y1", "price": "1200", "options": "色 赤 青\n\n名前 inscription 10", "display": "1", "caption": "x"},
               {"name": "B", "code": "y2", "display": "0"}])
        self.listing.apply("yahoo_goods", [y], "boss")
        self.assertTrue(self.m.roundtrip("yahoo_goods", [y])["ok"])
        g = self.dir / "gm"
        g.mkdir()
        write(g / "item_20261102_1.csv", ["コントロールカラム", "商品ID", "商品名", "公開・非公開", "説明文"],
              [{"商品ID": "9", "商品名": "ギフト", "公開・非公開": "公開", "説明文": "落とす"}, {"商品ID": "8", "商品名": "田中様専用", "公開・非公開": "非公開"}])
        write(g / "item_class_20261102_1.csv", ["コントロールカラム", "商品ID", "商品コード", "販売価格", "在庫数"],
              [{"商品ID": "9", "商品コード": "g9", "販売価格": "3300", "在庫数": "4"}, {"商品ID": "8", "商品コード": "g8"}])
        write(g / "item_option_20261102_1.csv", ["コントロールカラム", "商品ID", "オプションID", "オプション名 (フロント用)"],
              [{"商品ID": "9", "オプションID": "o1", "オプション名 (フロント用)": "ラッピング"}])
        write(g / "option_master_20261102_1.csv", ["コントロールカラム", "オプションID", "オプション名 (フロント用)", "選択肢1(名称)", "選択肢1(価格)", "選択肢2(名称)"],
              [{"オプションID": "o1", "オプション名 (フロント用)": "ラッピング", "選択肢1(名称)": "なし", "選択肢1(価格)": "0", "選択肢2(名称)": "あり"}])
        self.listing.apply("giftmall", [g], "boss")
        out = self.m.roundtrip("giftmall", [g])
        self.assertTrue(out["ok"], out)
        self.assertEqual((out["excluded"], out["kinds"]["option"]["orig"], out["kinds"]["class"]["orig"]), (1, 1, 1))   # 専用ページの規格は除く
        with self.assertRaises(ValueError):
            self.m.rows("goods")                                                    # FutureShop は fs_export


if __name__ == "__main__":
    unittest.main()
