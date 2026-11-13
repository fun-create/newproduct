#!/usr/bin/env python3
"""店の出品の台帳（段階1・ADR-141）: 取り込みの件数・説明と画像の列を落とす・2度入れても変わらない・
変わった／消えた／戻った・取り消し・管理者だけ・お客さまの情報らしい値で止める・一覧の絞り込み。

**本物の店のデータは使わない。**各カートの列の形だけをまねた小さなファイルを作る。"""
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


def write(path: Path, head, rows, enc="cp932", delim=","):
    b = io.StringIO()
    w = csv.writer(b, delimiter=delim, lineterminator="\r\n")
    w.writerow(head)
    for r in rows:
        w.writerow([r.get(h, "") for h in head])
    path.write_bytes(b.getvalue().encode(enc))


FS_GOODS = ["コントロールカラム", "商品URLコード", "ステータス", "商品番号", "商品名", "メイングループ", "本体価格", "販売期間(From)",
            "販売期間(to)", "現在在庫数", "キャッチコピー", "レイアウト割当名", "商品説明（大）", "独自コメント（1）", "最終更新日時"]
FS_SEL = ["コントロールカラム", "商品URLコード", "選択肢タイプ", "セレクト／ラジオボタン用項目名", "セレクト／ラジオボタン用選択肢", "最終更新日時"]
FS_OPB = ["コントロールカラム", "商品URLコード", "項目名【基本情報】", "説明文（PC）【説明文挿入】", "表示順", "最終更新日時"]
FS_OPS = ["コントロールカラム", "商品URLコード", "項目名【基本情報】", "選択肢コード【選択肢・価格設定／価格設定】", "選択肢【選択肢・価格設定】",
          "価格【選択肢・価格設定】", "表示順【選択肢・価格設定】", "価格【価格設定】", "最終更新日時"]


def fs_files(d: Path, goods=None, sel=None):
    d.mkdir(parents=True, exist_ok=True)
    goods = goods if goods is not None else [
        {"商品URLコード": "a001", "ステータス": "0", "商品番号": "A-1", "商品名": "アクスタ 丸", "本体価格": "1,980",
         "現在在庫数": "12", "キャッチコピー": "推しを飾る", "レイアウト割当名": "商品詳細(SP)ウェブデコ",
         "商品説明（大）": "<p>長い説明</p>", "独自コメント（1）": "<img src=x>", "最終更新日時": "2026/11/01 10:00:00"},
        {"商品URLコード": "a002", "ステータス": "1", "商品名": "うちわ", "本体価格": "880", "現在在庫数": "3"},
    ]
    write(d / "ccGoods_20261102143549.csv", FS_GOODS, goods)
    write(d / "goodsSelection_20261102143649.csv", FS_SEL, sel if sel is not None else [
        {"商品URLコード": "a001", "選択肢タイプ": "s", "セレクト／ラジオボタン用項目名": "台座", "セレクト／ラジオボタン用選択肢": "丸"},
        {"商品URLコード": "a001", "選択肢タイプ": "t", "セレクト／ラジオボタン用項目名": "名入れ"}])
    write(d / "goodsOptionPriceBasic_20261102143655.csv", FS_OPB,
          [{"商品URLコード": "a001", "項目名【基本情報】": "ラミネート", "説明文（PC）【説明文挿入】": "説明", "表示順": "1"}])
    write(d / "goodsOptionPriceSelection_20261102143703.csv", FS_OPS,
          [{"商品URLコード": "a001", "項目名【基本情報】": "ラミネート", "選択肢コード【選択肢・価格設定／価格設定】": "L1",
            "選択肢【選択肢・価格設定】": "あり", "価格【選択肢・価格設定】": "300", "表示順【選択肢・価格設定】": "1"}])


class Listing(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, listing
        seed.run()
        store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES ('president','boss')")
        store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES ('admin','devlead')")
        store.conn().commit()
        self.store, self.m = store, listing
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

    def test_futureshop_counts_drop_and_idempotent(self):
        d = self.dir / "goods"
        fs_files(d)
        pv = self.m.preview("goods", [d])
        self.assertEqual(pv["errors"], [])
        self.assertEqual(pv["counts"]["listings"], 2)
        self.assertEqual(pv["counts"]["added"], 2)
        self.assertEqual(pv["counts"]["options"], 4)               # 選択肢2・基本1・価格つき1
        for f in pv["files"]:
            self.assertEqual(f["rows"], f["accounted"])            # 読んだ行 = 数えた行
        r = self.m.apply("goods", [d], "boss")
        row = self.store.one("SELECT * FROM listing WHERE ext_id='a001'")
        raw = json.loads(row["raw_json"])
        self.assertNotIn("商品説明（大）", raw)                     # 説明文は取り込まない（11-12 決定）
        self.assertNotIn("独自コメント（1）", raw)
        self.assertNotIn("現在在庫数", raw)                         # 在庫は CIP の範囲
        self.assertEqual(raw["キャッチコピー"], "推しを飾る")
        self.assertEqual((row["state"], row["price"], row["layout"]), ("公開", 1980, "商品詳細(SP)ウェブデコ"))
        self.assertEqual(self.store.val("SELECT state FROM listing WHERE ext_id='a002'"), "非公開")
        opb = self.store.one("SELECT raw_json FROM listing_option WHERE src='opt_basic'")
        self.assertNotIn("説明文（PC）【説明文挿入】", json.loads(opb["raw_json"]))
        ops = self.store.one("SELECT * FROM listing_option WHERE src='opt_select'")
        self.assertEqual((ops["code"], ops["value"], ops["price"]), ("L1", "あり", 300))
        # 2度目: 何も変わらない（在庫の数だけが変わっても「同じ」）
        fs_files(d, goods=[
            {"商品URLコード": "a001", "ステータス": "0", "商品番号": "A-1", "商品名": "アクスタ 丸", "本体価格": "1,980",
             "現在在庫数": "999", "キャッチコピー": "推しを飾る", "レイアウト割当名": "商品詳細(SP)ウェブデコ",
             "商品説明（大）": "<p>別の説明</p>", "最終更新日時": "2026/11/01 10:00:00"},
            {"商品URLコード": "a002", "ステータス": "1", "商品名": "うちわ", "本体価格": "880", "現在在庫数": "0"}])
        c = self.m.preview("goods", [d])["counts"]
        self.assertEqual((c["added"], c["changed"], c["same"], c["gone"]), (0, 0, 2, 0))
        r2 = self.m.apply("goods", [d], "boss")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing"), 2)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing_option"), 4)
        self.assertGreater(r2["import_id"], r["import_id"])

    def test_changed_gone_returned_and_undo(self):
        d = self.dir / "goods"
        fs_files(d)
        i1 = self.m.apply("goods", [d], "boss")["import_id"]
        # 価格を変え、a002 を消す
        fs_files(d, goods=[{"商品URLコード": "a001", "ステータス": "0", "商品名": "アクスタ 丸", "本体価格": "2,200"}])
        c = self.m.preview("goods", [d])["counts"]
        self.assertEqual((c["changed"], c["gone"]), (1, 1))
        i2 = self.m.apply("goods", [d], "boss")["import_id"]
        self.assertEqual(self.store.val("SELECT price FROM listing WHERE ext_id='a001'"), 2200)
        self.assertEqual(self.store.val("SELECT gone_import_id FROM listing WHERE ext_id='a002'"), i2)   # 消さない
        self.assertEqual(self.m.summary()["shops"][0]["gone"], 1)
        # 前の取り込みを飛ばして戻すことはできない
        with self.assertRaises(ValueError):
            self.m.undo(i1, "boss")
        self.m.undo(i2, "boss")
        self.assertEqual(self.store.val("SELECT price FROM listing WHERE ext_id='a001'"), 1980)
        self.assertIsNone(self.store.val("SELECT gone_import_id FROM listing WHERE ext_id='a002'"))
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing_option l JOIN listing x ON x.id=l.listing_id "
                                        "WHERE x.ext_id='a001'"), 4)                                # 選択肢も戻る
        with self.assertRaises(ValueError):
            self.m.undo(i2, "boss")                                                                 # 2度は戻さない
        self.m.undo(i1, "boss")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing"), 0)
        # 消えたものがまた出てきたら戻す（変わった扱い）
        fs_files(d)
        self.m.apply("goods", [d], "boss")
        fs_files(d, goods=[{"商品URLコード": "a001", "ステータス": "0", "商品番号": "A-1", "商品名": "アクスタ 丸", "本体価格": "1,980",
                            "キャッチコピー": "推しを飾る", "レイアウト割当名": "商品詳細(SP)ウェブデコ", "最終更新日時": "2026/11/01 10:00:00"}])
        self.m.apply("goods", [d], "boss")
        fs_files(d)
        c = self.m.preview("goods", [d])["counts"]
        self.assertEqual((c["returned"], c["changed"], c["added"]), (1, 1, 0))

    def test_admin_only_and_pii_stops(self):
        d = self.dir / "goods"
        fs_files(d)
        with self.assertRaises(PermissionError):
            self.m.apply("goods", [d], "someone")
        with self.assertRaises(PermissionError):                 # 商品開発部管理者（admin）は取り込めない
            self.m.apply("goods", [d], "devlead")
        # 検査を壊してから1度動かす: お客さまの情報らしい値（メールアドレス・電話番号）が1件でもあれば止める
        fs_files(d, sel=[{"商品URLコード": "a001", "選択肢タイプ": "t", "セレクト／ラジオボタン用項目名": "連絡先",
                          "セレクト／ラジオボタン用選択肢": "taro@example.com"}])
        pv = self.m.preview("goods", [d])
        self.assertEqual(pv["pii"]["email"], 1)
        with self.assertRaises(ValueError):
            self.m.apply("goods", [d], "boss")
        fs_files(d, sel=[{"商品URLコード": "a001", "選択肢タイプ": "t", "セレクト／ラジオボタン用項目名": "090-1234-5678"}])
        self.assertEqual(self.m.preview("goods", [d])["pii"]["phone"], 1)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing"), 0)

    def test_private_pages_not_imported(self):
        # 「○○様専用」「専用ページ」は取り込まない（PL-64）。機種専用などの仕様の「専用」は取り込む
        d = self.dir / "goods"
        fs_files(d, goods=[{"商品URLコード": "p1", "ステータス": "1", "商品名": "山田様専用 アクスタ"},
                           {"商品URLコード": "p2", "ステータス": "1", "商品名": "追加料金 専用ページ"},
                           {"商品URLコード": "p3", "ステータス": "0", "商品名": "スマホ 専用 ケース"},
                           {"商品URLコード": "p4", "ステータス": "0", "商品名": "アクスタ"}],
                 sel=[{"商品URLコード": "p1", "選択肢タイプ": "t", "セレクト／ラジオボタン用項目名": "x"}])
        pv = self.m.preview("goods", [d])
        self.assertEqual((pv["counts"]["listings"], pv["counts"]["excluded"], pv["errors"]), (2, 2, []))
        self.m.apply("goods", [d], "boss")
        self.assertEqual(sorted(r["ext_id"] for r in self.store.q("SELECT ext_id FROM listing")), ["p3", "p4"])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing_option"), 0)     # p1 の選択肢も入らない
        # 先に入ってしまった分は消せる（台帳から行ごと・取り消し用の記録からも）
        self.store.ex("INSERT INTO listing (shop,ext_id,title,state,raw_json,row_hash,first_import_id,last_import_id) "
                      "VALUES ('goods','old1','佐藤様専用','非公開','{}','h',1,1)")
        self.store.ex("INSERT INTO listing_import_prev (import_id,tbl,ext_id,action) VALUES (1,'listing','old1','added')")
        self.store.conn().commit()
        self.assertEqual(self.m.purge_private(False, "")["total"], 1)                     # 既定は数えるだけ
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing WHERE ext_id='old1'"), 1)
        with self.assertRaises(PermissionError):
            self.m.purge_private(True, "devlead")
        r = self.m.purge_private(True, "boss")
        self.assertEqual((r["total"], r["per_shop"]), (1, {"goods": 1}))
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing WHERE ext_id='old1'"), 0)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing_import_prev WHERE ext_id='old1'"), 0)
        # 取り込み直しで専用ページが台帳にあれば消す（gone にしない）
        self.store.ex("INSERT INTO listing (shop,ext_id,title,state,raw_json,row_hash,first_import_id,last_import_id) "
                      "VALUES ('goods','p1','x','非公開','{}','h',1,1)")
        self.store.conn().commit()
        c = self.m.preview("goods", [d])["counts"]
        self.assertEqual((c["purge"], c["gone"]), (1, 0))
        self.m.apply("goods", [d], "boss")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing WHERE ext_id='p1'"), 0)

    def test_cip_link(self):
        from app import listing_cip
        d = self.dir / "goods"
        fs_files(d)                                                   # a001（商品番号 A-1）・a002（商品番号なし）
        self.m.apply("goods", [d], "boss")
        r = self.dir / "rakuten"
        r.mkdir()
        write(r / "dl-normal-item_1.csv", ["商品管理番号（商品URL）", "商品番号", "商品名", "倉庫指定", "SKU管理番号", "バリエーション項目選択肢1", "通常購入販売価格"], [
            {"商品管理番号（商品URL）": "r1", "商品番号": "R-1", "商品名": "うちわ", "倉庫指定": "0"},
            {"商品管理番号（商品URL）": "r1", "SKU管理番号": "r1-red", "バリエーション項目選択肢1": "赤", "通常購入販売価格": "800"}])
        self.m.apply("rakuten", [r], "boss")
        items = [
            {"store": "グッズ本店", "store_code": "A-1", "product_code": "AG0001", "cat1": "アクスタ", "pack_qty": 1},
            {"store": "グッズ本店", "store_code": "a002", "product_code": "AG0002"},        # 商品番号が無いので URLコードで当たる
            {"store": "グッズ本店", "store_code": "old-9", "product_code": "AG0009"},       # 台帳に無い
            {"store": "楽天", "store_code": "r1-red", "product_code": "UC0001"},            # SKU で当たる
            {"store": "楽天", "store_code": "R-1", "product_code": "UC0002"},              # 商品番号で当たる
            {"store": "楽天", "store_code": "R-1", "product_code": "UC0003"},              # 1つの店コードが2つに割れる
            {"store": "謎の店", "store_code": "x", "product_code": "ZZ0001"},
        ]
        fetch = lambda: {"as_of": "2026-11-13", "items": items}
        out = listing_cip.run(False, "", fetch)                        # 既定は数えるだけ
        self.assertEqual((out["link_rows"], out["listings"], out["product_codes"]), (5, 3, 5))
        self.assertEqual(out["per_store"]["グッズ本店"], {"codes": 3, "linked": 2, "unmatched": 1})
        self.assertTrue(out["per_store"]["謎の店"]["unknown_store"])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing_cip_link"), 0)
        with self.assertRaises(PermissionError):
            listing_cip.run(True, "devlead", fetch)
        listing_cip.run(True, "boss", fetch)
        listing_cip.run(True, "boss", fetch)                           # 作り直しても増えない
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing_cip_link"), 5)
        by = {r["product_code"]: r["matched_by"] for r in self.store.q("SELECT product_code, matched_by FROM listing_cip_link")}
        self.assertEqual(by, {"AG0001": "ext_sub", "AG0002": "ext_id", "UC0001": "option", "UC0002": "ext_sub", "UC0003": "ext_sub"})
        s = {x["code"]: x for x in self.m.summary()["shops"]}
        self.assertEqual((s["goods"]["cip_linked"], s["rakuten"]["cip_linked"]), (2, 1))
        self.assertEqual(self.m.listings({"cip": "no"})["total"], 0)
        self.assertEqual(self.m.listings({"code": "UC0003"})["total"], 1)
        det = self.m.detail(self.store.val("SELECT id FROM listing WHERE ext_id='r1'"))
        self.assertEqual(sorted(k["product_code"] for k in det["cip_links"]), ["UC0001", "UC0002", "UC0003"])
        self.assertEqual([k["option_value"] for k in det["cip_links"] if k["matched_by"] == "option"], [":赤"])
        # 出品を消すと結び付けも消える（専用ページを消すとき）
        self.store.ex("UPDATE listing SET title='田中様専用' WHERE ext_id='a002'")
        self.store.conn().commit()
        self.m.purge_private(True, "boss")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM listing_cip_link WHERE product_code='AG0002'"), 0)

    def test_yahoo_and_giftmall_options(self):
        from app import listing_parse as lp
        rows = lp.yahoo_options("胴着/袴カラー 選択して下さい！ 紺/紺 白/白\n\n垂上※不要な場合は「なし」を入力 inscription 4\n\nカラー|10011| シルバー")
        self.assertEqual([(o["item_name"], o["value"], o["sort"]) for o in rows],
                         [("胴着/袴カラー", "選択して下さい！", 1), ("胴着/袴カラー", "紺/紺", 2), ("胴着/袴カラー", "白/白", 3),
                          ("垂上※不要な場合は「なし」を入力", None, 0), ("カラー", "シルバー", 1)])
        self.assertEqual((rows[3]["raw"]["最大文字数"], rows[4]["raw"]["仕様ID"]), ("4", "10011"))
        self.assertEqual(lp.yahoo_options(""), [])
        self.assertEqual(lp.gm_values({"選択肢2(名称)": "青", "選択肢2(価格)": "", "選択肢1(名称)": "赤", "選択肢1(価格)": "300", "選択肢3(名称)": ""}),
                         [("赤", 300), ("青", None)])
        g = self.dir / "gm"
        g.mkdir()
        write(g / "item_20261102_1.csv", ["コントロールカラム", "商品ID", "商品名", "公開・非公開"], [{"商品ID": "9", "商品名": "ギフト", "公開・非公開": "公開"}])
        write(g / "item_option_20261102_1.csv", ["コントロールカラム", "商品ID", "オプションID", "オプション名 (フロント用)"],
              [{"商品ID": "9", "オプションID": "o1", "オプション名 (フロント用)": "ラッピング"}, {"商品ID": "9", "オプションID": "o2", "オプション名 (フロント用)": "メッセージ"}])
        write(g / "option_master_20261102_1.csv", ["コントロールカラム", "オプションID", "オプション名 (フロント用)", "タイプ", "選択肢1(名称)", "選択肢1(価格)", "選択肢2(名称)", "選択肢2(価格)"],
              [{"オプションID": "o1", "オプション名 (フロント用)": "ラッピング", "タイプ": "select", "選択肢1(名称)": "なし", "選択肢1(価格)": "0",
                "選択肢2(名称)": "あり", "選択肢2(価格)": "330"},
               {"オプションID": "o2", "オプション名 (フロント用)": "メッセージ", "タイプ": "text"}])
        p = lp.parse("giftmall", [x for x in g.iterdir()])
        opts = [(o["item_name"], o["value"], o["price"]) for o in p["recs"]["9"]["options"]]
        self.assertEqual(opts, [("ラッピング", "なし", 0), ("ラッピング", "あり", 330), ("メッセージ", None, None)])
        self.assertEqual(p["errors"], [])

    def test_broken_inputs(self):
        from app import listing_parse as lp
        d = self.dir / "goods"
        d.mkdir()
        write(d / "ccGoods_1.csv", ["商品URLコード", "商品名"], [{"商品URLコード": "a", "商品名": "x"}])
        with self.assertRaises(lp.ParseError):                       # 要る列（ステータス）が無い
            self.m.preview("goods", [d])
        e = self.dir / "e"
        fs_files(e, sel=[{"商品URLコード": "zzz", "選択肢タイプ": "s", "セレクト／ラジオボタン用項目名": "台座"}])
        pv = self.m.preview("goods", [e])
        self.assertEqual(pv["orphans"], {"selection": 1})            # 親の無い行は数えて出す
        dup = self.dir / "dup"
        fs_files(dup, goods=[{"商品URLコード": "a", "ステータス": "0", "商品名": "x"}, {"商品URLコード": "a", "ステータス": "0", "商品名": "y"}])
        self.assertTrue(any("2回" in x for x in self.m.preview("goods", [dup])["errors"]))
        (e / "memo.txt").write_text("x")
        self.assertTrue(any(f["name"] == "memo.txt" and not f["used"] for f in self.m.preview("goods", [e])["files"]))

    def test_other_carts(self):
        r = self.dir / "rakuten"
        r.mkdir()
        head = ["商品管理番号（商品URL）", "商品番号", "商品名", "倉庫指定", "ジャンルID", "PC用商品説明文", "商品画像パス1",
                "選択肢タイプ", "商品オプション項目名", "商品オプション選択肢1", "商品オプション選択肢2",
                "SKU管理番号", "バリエーション項目キー1", "バリエーション項目選択肢1", "通常購入販売価格", "在庫数"]
        write(r / "dl-normal-item_1.csv", head, [
            {"商品管理番号（商品URL）": "r1", "商品名": "名入れうちわ", "倉庫指定": "0", "ジャンルID": "123",
             "PC用商品説明文": "<b>説明</b>", "商品画像パス1": "/a.jpg"},
            {"商品管理番号（商品URL）": "r1", "選択肢タイプ": "s", "商品オプション項目名": "色", "商品オプション選択肢1": "赤", "商品オプション選択肢2": "青"},
            {"商品管理番号（商品URL）": "r1", "SKU管理番号": "r1-a", "バリエーション項目キー1": "size", "バリエーション項目選択肢1": "M",
             "通常購入販売価格": "1500", "在庫数": "5"},
            {"商品管理番号（商品URL）": "r1", "SKU管理番号": "r1-b", "バリエーション項目選択肢1": "L", "通常購入販売価格": "1800"}])
        pv = self.m.preview("rakuten", [r])
        self.assertEqual((pv["counts"]["listings"], pv["counts"]["options"], pv["errors"]), (1, 4, []))
        self.m.apply("rakuten", [r], "boss")
        row = self.store.one("SELECT * FROM listing WHERE shop='rakuten'")
        self.assertEqual((row["price"], row["state"], row["category"]), (1500, "公開", "123"))
        raw = json.loads(row["raw_json"])
        self.assertNotIn("PC用商品説明文", raw)
        self.assertNotIn("商品画像パス1", raw)
        sku = self.store.one("SELECT raw_json FROM listing_option WHERE src='rakuten_sku' AND code='r1-a'")
        self.assertNotIn("在庫数", json.loads(sku["raw_json"]))

        y = self.dir / "yahoo"
        y.mkdir()
        write(y / "data202611021418.csv", ["path", "name", "code", "price", "options", "display", "caption", "item-image-urls"],
              [{"name": "名入れ", "code": "y1", "price": "1200", "options": "#色 赤 青", "display": "1", "caption": "説明"},
               {"name": "非公開の", "code": "y2", "display": "0"}])
        self.m.apply("yahoo_goods", [y], "boss")
        self.assertEqual(self.store.val("SELECT state FROM listing WHERE ext_id='y1'"), "公開")
        self.assertEqual(self.store.val("SELECT state FROM listing WHERE ext_id='y2'"), "非公開")
        self.assertNotIn("caption", json.loads(self.store.val("SELECT raw_json FROM listing WHERE ext_id='y1'")))

        g = self.dir / "gm"
        g.mkdir()
        write(g / "item_20261102_143102.csv", ["コントロールカラム", "商品ID", "商品スラグ", "商品名", "公開・非公開", "説明文", "店舗メモ欄"],
              [{"商品ID": "9", "商品名": "ギフト", "公開・非公開": "公開", "説明文": "x", "店舗メモ欄": "メモ"}])
        write(g / "item_class_20261102_143125.csv", ["コントロールカラム", "商品ID", "商品コード", "在庫数", "販売価格", "規格名1", "規格分類名1"],
              [{"商品ID": "9", "商品コード": "g9", "在庫数": "1", "販売価格": "3,300", "規格名1": "S", "規格分類名1": "サイズ"}])
        write(g / "item_option_20261102_143129.csv", ["コントロールカラム", "商品ID", "商品名", "オプションID", "オプション名 (フロント用)"],
              [{"商品ID": "9", "オプションID": "o1", "オプション名 (フロント用)": "ラッピング"}])
        write(g / "option_master_20261102_143135.csv", ["コントロールカラム", "オプションID", "オプション名 (フロント用)", "タイプ"],
              [{"オプションID": "o1", "オプション名 (フロント用)": "ラッピング", "タイプ": "select"}])
        write(g / "class_category_master_20261102_143132.csv", ["a"], [{"a": "1"}])
        pv = self.m.preview("giftmall", [g])
        self.assertEqual((pv["counts"]["listings"], pv["counts"]["options"], pv["counts"]["masters"]), (1, 2, 1))
        self.m.apply("giftmall", [g], "boss")
        self.assertEqual(self.store.val("SELECT price FROM listing WHERE shop='giftmall'"), 3300)
        self.assertNotIn("店舗メモ欄", json.loads(self.store.val("SELECT raw_json FROM listing WHERE shop='giftmall'")))
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM shop_option_master"), 1)

        a = self.dir / "amazon"
        a.mkdir()
        write(a / "amazon_出品商品レポート_全件_1.txt", ["item-name", "listing-id", "seller-sku", "price", "quantity", "product-id", "status", "item-note"],
              [{"item-name": "A", "seller-sku": "s1", "price": "990", "quantity": "4", "product-id": "B0X", "status": "Active", "item-note": "n"},
               {"item-name": "B", "seller-sku": "s2", "status": "Incomplete"}], enc="utf-8-sig", delim="\t")
        write(a / "amazon_FBA在庫レポート_全件_1.txt", ["sku"], [{"sku": "s1"}], delim="\t")
        pv = self.m.preview("amazon", [a])
        self.assertEqual(pv["states"], {"公開": 1, "そのほか": 1})
        self.assertTrue(any(not f["used"] and "CIP" in f["reason"] for f in pv["files"]))

        s = self.dir / "shopify"
        s.mkdir()
        write(s / "products_export_1.csv", ["Handle", "Title", "Body (HTML)", "Status", "Option1 Name", "Option1 Value", "Variant SKU",
                                            "Variant Price", "Variant Inventory Qty", "Image Src"],
              [{"Handle": "h1", "Title": "Acrylic", "Body (HTML)": "<p>x</p>", "Status": "active", "Option1 Name": "Size", "Option1 Value": "S",
                "Variant Price": "10", "Image Src": "https://x/1.jpg"},
               {"Handle": "h1", "Option1 Value": "M", "Variant Price": "12"},
               {"Handle": "h1", "Image Src": "https://x/2.jpg"}], enc="utf-8-sig")
        pv = self.m.preview("overseas", [s])
        self.assertEqual((pv["counts"]["listings"], pv["counts"]["options"], pv["errors"]), (1, 2, []))
        self.m.apply("overseas", [s], "boss")
        self.assertEqual(self.store.val("SELECT price FROM listing WHERE shop='overseas'"), 10)
        self.assertNotIn("Image Src", json.loads(self.store.val("SELECT raw_json FROM listing WHERE shop='overseas'")))

    def test_list_filter_paging_and_detail(self):
        d = self.dir / "goods"
        fs_files(d, goods=[{"商品URLコード": f"c{i:03d}", "ステータス": "0" if i % 2 else "1", "商品名": f"商品 {i}"} for i in range(120)],
                 sel=[])
        self.m.apply("goods", [d], "boss")
        x = self.m.listings({"shop": "goods"})
        self.assertEqual((x["total"], x["pages"], len(x["rows"])), (120, 3, 50))
        self.assertEqual(len(self.m.listings({"shop": "goods", "page": "3"})["rows"]), 20)
        self.assertEqual(self.m.listings({"state": "公開"})["total"], 60)
        self.assertEqual(self.m.listings({"q": "商品 11"})["total"], 11)        # 11・110〜119
        self.assertEqual(self.m.listings({"linked": "yes"})["total"], 0)
        lid = x["rows"][0]["id"]
        det = self.m.detail(lid)
        self.assertEqual(det["shop_label"], "グッズ本店")
        with self.assertRaises(LookupError):
            self.m.detail(99999)
        with self.assertRaises(LookupError):
            self.m.listings({"shop": "nope"})
        s = {r["code"]: r for r in self.m.summary()["shops"]}
        self.assertEqual((s["goods"]["counts"], s["goods"]["state_checked"], s["rakuten"]["total"]), ({"公開": 60, "非公開": 60}, True, 0))


if __name__ == "__main__":
    unittest.main()
