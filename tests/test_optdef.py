#!/usr/bin/env python3
"""選択肢の整理（段階3・ADR-148）: 似た項目名をまとめる・印を付けたものだけ定義に寄せる・値はいちばん多い価格・
台帳の行が定義の値と結ばれる・既存の定義に寄せる・二重に寄せない・外す・業務ロール。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class OptDef(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, optdef
        seed.run()
        for role, u in (("president", "boss"), ("admin", "lead"), ("member", "dev")):
            store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES (?,?)", (role, u))
        self.store, self.m = store, optdef

        def lst(shop, ext):
            return store.ex("INSERT INTO listing (shop,ext_id,title,state,raw_json,row_hash,first_import_id,last_import_id) "
                            "VALUES (?,?,?,'公開','{}','h',1,1)", (shop, ext, ext)).lastrowid

        def opt(lid, src, item, value, price=None, code=None):
            store.ex("INSERT INTO listing_option (listing_id,src,item_name,value,code,price,raw_json) VALUES (?,?,?,?,?,?,'{}')",
                     (lid, src, item, value, code, price))
        u1, u2, g1, r1 = lst("uchiwa", "u1"), lst("uchiwa", "u2"), lst("goods", "g1"), lst("rakuten", "r1")
        for lid in (u1, u2):
            opt(lid, "opt_select", "表面：ホログラム加工", "なし", 0, "h0")
            opt(lid, "opt_select", "表面：ホログラム加工", "あり", 600, "h1")
            opt(lid, "opt_select", "裏面：ホログラム加工", "なし", 0, "h0")
        opt(u2, "opt_select", "裏面：ホログラム加工", "あり", 500, "h1")       # この出品だけ 500 円
        opt(u1, "opt_select", "裏面：ホログラム加工", "あり", 600, "h1")
        opt(g1, "opt_select", "□ ホログラム 加工", "あり", 600, "x9")
        opt(g1, "selection", "お名前", None)                                  # 自由入力（値なし）
        opt(r1, "rakuten_option", "ホログラム加工", "あり")
        store.conn().commit()
        self.ids = {"u1": u1, "u2": u2, "g1": g1, "r1": r1}

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_norm_and_clusters(self):
        self.assertEqual(self.m.norm("表面：ホログラム加工"), self.m.norm("裏面：ホログラム加工"))
        self.assertEqual(self.m.norm("【2個目】追加料金"), "追加料金")
        self.assertEqual(self.m.norm("□ ホログラム 加工"), "ホログラム加工")
        c = self.m.clusters({"group": "own"})
        self.assertEqual([x["key"] for x in c["rows"]], ["ホログラム加工"])          # お名前は価格が無いので出ない
        names = sorted(m["item_name"] for m in c["rows"][0]["members"])
        self.assertEqual(names, ["□ ホログラム 加工", "表面：ホログラム加工", "裏面：ホログラム加工"])   # 楽天は別の店のまとまり
        self.assertEqual((c["rows"][0]["suggest_kind"], c["rows"][0]["suggest_name"]), ("追加料金", "ホログラム加工"))
        c0 = self.m.clusters({"group": "own", "priced": "0"})
        name = [x for x in c0["rows"] if x["key"] == "お名前"][0]
        self.assertEqual((name["suggest_kind"], name["suggest_input"]), ("お客さまの入力", "自由入力"))

    def test_assign_values_and_links(self):
        ms = ["uchiwa|opt_select|表面：ホログラム加工", "uchiwa|opt_select|裏面：ホログラム加工"]
        with self.assertRaises(PermissionError):
            self.m.assign({"name": "ホログラム加工", "kind": "追加料金"}, ms, "dev")
        with self.assertRaises(ValueError):
            self.m.assign({"name": "ホログラム加工", "kind": "おまけ"}, ms, "lead")
        r = self.m.assign({"name": "ホログラム加工", "kind": "追加料金", "input_type": "選ぶ"}, ms, "lead")
        self.assertEqual((r["values"], r["linked"]), (2, 8))          # うちわ2出品 × 表裏 × あり・なし
        d = self.m.detail(r["def_id"])
        vals = {v["label"]: (v["code"], v["add_price"], v["n"]) for v in d["values"]}
        self.assertEqual(vals, {"なし": ("h0", None, 4), "あり": ("h1", 600, 4)})     # 600 が多い。500 の出品は台帳に残る
        self.assertEqual(self.store.val("SELECT price FROM listing_option WHERE listing_id=? AND item_name='裏面：ホログラム加工' AND value='あり'",
                                        (self.ids["u2"],)), 500)
        # 同じ名前で新しく作れない・既存へ寄せる（グッズ本店の □ 付き。コード x9 は既存の値と同じ「あり」に寄る）
        with self.assertRaises(ValueError):
            self.m.assign({"name": "ホログラム加工", "kind": "追加料金"}, ["goods|opt_select|□ ホログラム 加工"], "boss")
        r2 = self.m.assign({"def_id": str(r["def_id"])}, ["goods|opt_select|□ ホログラム 加工"], "boss")
        self.assertEqual((r2["values"], r2["linked"]), (2, 9))
        self.assertEqual(self.m.clusters({"group": "own"})["total"], 0)                 # 全部寄せた
        # 別の定義に二重に寄せない
        other = self.m.assign({"name": "加工", "kind": "作るもの"}, ["rakuten|rakuten_option|ホログラム加工"], "boss")["def_id"]
        with self.assertRaises(ValueError):
            self.m.assign({"def_id": str(other)}, ["uchiwa|opt_select|表面：ホログラム加工"], "boss")
        with self.assertRaises(LookupError):
            self.m.assign({"def_id": str(other)}, ["uchiwa|opt_select|無い項目"], "boss")
        # 外すと台帳の結びも外れる（値は残す）
        self.m.detach(r["def_id"], "goods", "opt_select", "□ ホログラム 加工", "lead")
        self.assertIsNone(self.store.val("SELECT def_value_id FROM listing_option WHERE listing_id=?", (self.ids["g1"],)))
        self.assertEqual(len(self.m.detail(r["def_id"])["values"]), 2)
        with self.assertRaises(LookupError):
            self.m.detach(r["def_id"], "goods", "opt_select", "□ ホログラム 加工", "lead")
        self.assertEqual([x["what"] for x in self.m.detail(r["def_id"])["revisions"]], ["項目名を外した", "項目名を寄せた", "定義を作った"])
        dl = {x["name"]: x for x in self.m.defs()}
        self.assertEqual((dl["ホログラム加工"]["n_alias"], dl["ホログラム加工"]["n_listings"]), (2, 2))

    def test_price_rule(self):
        from app import pricerule
        self.assertEqual(pricerule.parse_tier("25%引き （5～19個購入）"), {"min": 5, "max": 19, "rate": 25})
        self.assertEqual(pricerule.parse_tier("基本単価 （1個購入）"), {"min": 1, "max": 1, "rate": 0})
        self.assertEqual(pricerule.parse_tier("300個以上 45%引き"), {"min": 300, "max": None, "rate": 45})
        self.assertIsNone(pricerule.parse_tier("【ハイグレード ブラック】 5～19個"))     # 率が無い
        ins = lambda lid, item, v, pr: self.store.ex("INSERT INTO listing_option (listing_id,src,item_name,value,price,raw_json) VALUES (?,?,?,?,?,'{}')",
                                                     (lid, "opt_select", item, v, pr))
        mk = lambda ext: self.store.ex("INSERT INTO listing (shop,ext_id,title,state,raw_json,row_hash,first_import_id,last_import_id) "
                                       "VALUES ('goods',?,'x','公開','{}','h',1,1)", (ext,)).lastrowid
        g2, g3, g4, g5, g6 = mk("g2"), mk("g3"), mk("g4"), mk("g5"), mk("g6")
        A = ((1, 0), (2, 10), (3, 15))
        for lid, table_ in ((self.ids["g1"], A), (g2, A), (g5, A), (g6, A), (g3, ((1, 0), (2, 5), (3, 10)))):
            for n, rate in table_:
                ins(lid, "単価割引率", ("基本単価" if not rate else f"{rate}%引き") + f" （{n}個購入）", -rate * 10)
        ins(g4, "単価割引率", "基本単価 （1個購入）", 0)               # 段が1つしか無い出品は結ばない
        self.store.conn().commit()
        c = [x for x in self.m.clusters({"group": "own"})["rows"] if x["key"] == "単価割引率"][0]
        self.assertEqual([t["n_listings"] for t in c["tier_tables"]], [4, 1])           # 出品によって表が2種類
        self.assertEqual(c["tier_tables"][1]["tiers"][1], {"min": 2, "max": 2, "rate": 5})
        self.assertIsNone([x for x in self.m.clusters({"group": "own"})["rows"] if x["key"] == "ホログラム加工"][0]["tier_tables"])
        m = [("goods", "opt_select", "単価割引率")]
        with self.assertRaises(PermissionError):
            pricerule.create("数量割引", m, "dev")
        with self.assertRaises(ValueError):                              # 段が読めないものは価格の決まりにしない
            pricerule.create("ホロ", [("uchiwa", "opt_select", "表面：ホログラム加工")], "boss")
        r = pricerule.create("数量割引", m, "boss")
        self.assertEqual([(x["name"], x["listings"]) for x in r["rules"]], [("数量割引 A", 4), ("数量割引 B", 1)])
        self.assertEqual(r["bad_listings"], 1)
        self.assertEqual(self.store.val("SELECT rule_id FROM price_rule_listing WHERE listing_id=?", (g3,)), r["rules"][1]["id"])
        self.assertNotIn("単価割引率", [x["key"] for x in self.m.clusters({"group": "own"})["rows"]])   # 寄せたので消える
        with self.assertRaises(ValueError):                              # 選択肢の定義に二重に寄せない
            self.m.assign({"name": "割引", "kind": "追加料金"}, ["goods|opt_select|単価割引率"], "boss")
        with self.assertRaises(ValueError):
            pricerule.create("数量割引", m, "boss")
        rs = {x["name"]: x for x in pricerule.rules()}
        self.assertEqual((rs["数量割引 A"]["n_listings"], rs["数量割引 B"]["n_listings"]), (4, 1))
        pricerule.detach(r["rules"][0]["id"], "goods", "opt_select", "単価割引率", "boss")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM price_rule_listing"), 0)
        self.assertIn("単価割引率", [x["key"] for x in self.m.clusters({"group": "own"})["rows"]])

    def test_free_input_and_update(self):
        r = self.m.assign({"name": "お名前", "kind": "お客さまの入力", "input_type": "自由入力"}, ["goods|selection|お名前"], "boss")
        self.assertEqual((r["values"], r["linked"]), (0, 0))
        with self.assertRaises(ValueError):
            self.m.update(r["def_id"], {"kind": "x"}, "boss")
        self.assertEqual(self.m.update(r["def_id"], {"name": "お名前（名入れ）"}, "boss")["changed"], ["name"])
        with self.assertRaises(PermissionError):
            self.m.update(r["def_id"], {"name": "y"}, "dev")


if __name__ == "__main__":
    unittest.main()
