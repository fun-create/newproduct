#!/usr/bin/env python3
"""NEW PRODUCT で新しい出品を作る（段階4・ADR-155）: 雛形を写す・写さない列・決まりの検査・
取り込みで「消えた」にしない・店のファイルに出てきたら店の出品になる・書き出しは新規（n・項目選択肢も出す）・直せるのは店に無い出品だけ。"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.test_listing import fs_files, write, FS_GOODS  # noqa: E402


class ListingNew(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, listing, listing_new, exportreq
        seed.run()
        for role, u in (("president", "masateru"), ("member", "dev"), ("prod", "tsubasa")):
            store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES (?,?)", (role, u))
        store.conn().commit()
        self.store, self.listing, self.m, self.ex = store, listing, listing_new, exportreq
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "goods"
        fs_files(self.dir)
        listing.apply("goods", [self.dir], "masateru")
        self.tid = store.val("SELECT id FROM listing WHERE ext_id='a001'")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def make(self, **kw):
        f = {"url_code": "np001", "item_no": "NP-1", "title": "新しいアクスタ", "price": "2,200", "status": "1"} | kw
        return self.m.create_from_template(self.tid, f, "dev")

    def test_create_and_validate(self):
        with self.assertRaises(PermissionError):
            self.m.create_from_template(self.tid, {}, "tsubasa")
        for bad in ({"url_code": "a"}, {"url_code": "a001"}, {"item_no": "A~1"}, {"title": ""}, {"price": "千円"}, {"status": "2"},
                    {"catch": "x" * 201}, {"sellable_id": "nope"}):
            with self.assertRaises((ValueError, LookupError), msg=str(bad)):
                self.make(**bad)
        r = self.make(catch="推しの名前が主役")
        self.assertEqual(r["options"], 4)                                     # 雛形の選択肢を写す
        row = self.store.one("SELECT * FROM listing WHERE id=?", (r["id"],))
        raw = json.loads(row["raw_json"])
        self.assertEqual((row["first_import_id"], row["state"], row["price"], raw["キャッチコピー"]), (0, "非公開", 2200, "推しの名前が主役"))
        self.assertNotIn("最終更新日時", raw)
        self.assertEqual(raw["レイアウト割当名"], "商品詳細(SP)ウェブデコ")       # 設定は写す
        opts = [json.loads(o["raw_json"]) for o in self.store.q("SELECT raw_json FROM listing_option WHERE listing_id=?", (r["id"],))]
        self.assertTrue(all(o["商品URLコード"] == "np001" for o in opts))
        r2 = self.m.create_from_template(self.tid, {"url_code": "np002", "title": "B", "price": "100"}, "dev")
        self.assertNotIn("キャッチコピー", json.loads(self.store.val("SELECT raw_json FROM listing WHERE id=?", (r2["id"],))))  # 入れなければ空

    def test_import_keeps_and_adopts(self):
        lid = self.make()["id"]
        # 店のファイルにまだ無い → 取り込んでも「消えた」にしない
        c = self.listing.preview("goods", [self.dir])["counts"]
        self.assertEqual((c["gone"], c["np_arrived"]), (0, 0))
        self.listing.apply("goods", [self.dir], "masateru")
        self.assertIsNone(self.store.val("SELECT gone_import_id FROM listing WHERE id=?", (lid,)))
        # 書き出しは新規（n）で、項目選択肢も出す
        rid = self.ex.create("goods", ["np001"], "新商品", "dev")["id"]
        self.assertEqual(self.ex.detail(rid, "dev")["kind"], "新規")
        out = self.ex.decide(rid, True, "", "masateru")
        self.assertEqual(len(out["files"]), 4)
        f = [x for x in self.ex.detail(rid, "dev")["files"] if x["kind"] == "goods"][0]
        rows = list(csv.DictReader(io.StringIO(self.ex.file_bytes(rid, f["id"])[1].decode("cp932"), newline="")))
        self.assertEqual((rows[0]["コントロールカラム"], rows[0]["商品URLコード"], rows[0]["本体価格"]), ("n", "np001", "2200"))
        # 店に入って、店の出力に出てきた → 店の出品になる（以後は更新）。中身が同じなら「同じ」
        new_raw = json.loads(self.store.val("SELECT raw_json FROM listing WHERE id=?", (lid,)))
        goods = [json.loads(r["raw_json"]) for r in self.store.q("SELECT raw_json FROM listing WHERE shop='goods' AND id<>?", (lid,))] + [new_raw]
        write(self.dir / "ccGoods_20261102143549.csv", FS_GOODS, goods)
        sel = [json.loads(o["raw_json"]) for o in self.store.q("SELECT raw_json FROM listing_option WHERE src='selection' ORDER BY id")]
        write(self.dir / "goodsSelection_20261102143649.csv", list(dict.fromkeys(k for r in sel for k in r)) or ["商品URLコード"], sel)
        ob = [json.loads(o["raw_json"]) for o in self.store.q("SELECT raw_json FROM listing_option WHERE src='opt_basic' ORDER BY id")]
        write(self.dir / "goodsOptionPriceBasic_20261102143655.csv", list(dict.fromkeys(k for r in ob for k in r)), ob)
        os_ = [json.loads(o["raw_json"]) for o in self.store.q("SELECT raw_json FROM listing_option WHERE src='opt_select' ORDER BY id")]
        write(self.dir / "goodsOptionPriceSelection_20261102143703.csv", list(dict.fromkeys(k for r in os_ for k in r)), os_)
        c = self.listing.preview("goods", [self.dir])["counts"]
        self.assertEqual((c["np_arrived"], c["added"], c["same"]), (1, 0, 3))
        self.listing.apply("goods", [self.dir], "masateru")
        self.assertGreater(self.store.val("SELECT first_import_id FROM listing WHERE id=?", (lid,)), 0)
        with self.assertRaises(PermissionError):
            self.m.update_new(lid, {"url_code": "np001", "title": "x", "price": "1"}, "dev")   # 店にある出品はここでは直さない
        self.assertEqual(self.ex.create("goods", ["np001"], "", "dev")["kind"], "更新")

    def test_update_new(self):
        lid = self.make()["id"]
        self.m.create_from_template(self.tid, {"url_code": "np002", "title": "B", "price": "100"}, "dev")
        with self.assertRaises(ValueError):
            self.m.update_new(lid, {"url_code": "np002", "title": "x", "price": "1"}, "dev")    # 重なる
        self.m.update_new(lid, {"url_code": "np010", "item_no": "", "title": "名前を直した", "price": "2500", "status": "0"}, "dev")
        row = self.store.one("SELECT * FROM listing WHERE id=?", (lid,))
        raw = json.loads(row["raw_json"])
        self.assertEqual((row["ext_id"], row["title"], row["state"], "商品番号" in raw), ("np010", "名前を直した", "公開", False))
        self.assertTrue(all(json.loads(o["raw_json"]).get("商品URLコード") == "np010"
                            for o in self.store.q("SELECT raw_json FROM listing_option WHERE listing_id=?", (lid,))))
        with self.assertRaises(PermissionError):
            self.m.update_new(self.tid, {"url_code": "a001", "title": "x", "price": "1"}, "dev")   # 店から取り込んだ出品


if __name__ == "__main__":
    unittest.main()
