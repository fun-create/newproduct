#!/usr/bin/env python3
"""売るもの・束ねる候補（段階2・ADR-147）: 同じ共通商品コードの出品が店をまたいで1つの候補になる・選択肢で作るものが
変わる出品は候補に入れない・選んだものだけ作る・二重に作らない・外すと候補に戻る・直すと履歴が残る・業務ロール。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Sellable(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, sellable
        seed.run()
        for role, u in (("president", "boss"), ("admin", "lead"), ("member", "dev")):
            store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES (?,?)", (role, u))
        self.store, self.m = store, sellable
        # 出品: グッズ本店 g1（公開）・楽天 r1（非公開）・Yahoo y1（公開）は AG0001。楽天 r2 は AG0001 と AG0002 の両方（選択肢で変わる）
        #       うちわ本店 u1（非公開）は UC0001 だけ。消えた出品 g9 も AG0001
        L = [("goods", "g1", "公開", None), ("rakuten", "r1", "非公開", None), ("yahoo_goods", "y1", "公開", None),
             ("rakuten", "r2", "公開", None), ("uchiwa", "u1", "非公開", None), ("goods", "g9", "公開", 2)]
        ids = {}
        for shop, ext, st, gone in L:
            ids[ext] = store.ex("INSERT INTO listing (shop,ext_id,title,state,raw_json,row_hash,first_import_id,last_import_id,gone_import_id) "
                                "VALUES (?,?,?,?,'{}','h',1,1,?)", (shop, ext, ext + " の掲載名", st, gone)).lastrowid
        for ext, code in (("g1", "AG0001"), ("r1", "AG0001"), ("y1", "AG0001"), ("r2", "AG0001"), ("r2", "AG0002"), ("u1", "UC0001"), ("g9", "AG0001")):
            store.ex("INSERT INTO listing_cip_link (listing_id,cip_store,store_code,product_code,matched_by,cat1,size,pack_qty,linked_at,linked_by) "
                     "VALUES (?,?,?,?,'ext_id','アクスタ','M',2,'t','boss')", (ids[ext], "x", ext, code))
        store.conn().commit()
        self.ids = ids

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_candidates_and_bundle(self):
        c = self.m.candidates({"min_shops": "1"})
        by = {g["product_code"]: g for g in c["rows"]}
        self.assertEqual(sorted(by), ["AG0001", "UC0001"])                   # AG0002 だけの出品は無い（r2 は保留）
        self.assertEqual(sorted(l["ext_id"] for l in by["AG0001"]["listings"]), ["g1", "r1", "y1"])   # 消えた g9・複数コードの r2 は入れない
        self.assertEqual((by["AG0001"]["n_shops"], c["held_multi"], c["rows"][0]["product_code"]), (3, 1, "AG0001"))
        self.assertEqual(self.m.candidates({"min_shops": "2"})["total"], 1)
        with self.assertRaises(PermissionError):
            self.m.bundle(["AG0001"], "dev")
        r = self.m.bundle(["AG0001", "UC0001", "AG0001"], "lead")              # 同じコードを2回選んでも1つ
        self.assertEqual([m["product_code"] for m in r["made"]], ["AG0001", "UC0001"])
        sid = r["made"][0]["id"]
        d = self.m.detail(sid)
        self.assertEqual((d["state"], d["label"], d["makes"]), ("販売中", "AG0001 アクスタ M 2個入", [{"combo": "", "product_code": "AG0001", "qty": 1}]))
        self.assertEqual(self.m.detail(r["made"][1]["id"])["state"], "終了")    # 公開の出品が無い
        self.assertEqual(self.m.bundle(["AG0001"], "boss")["skipped"], ["AG0001"])   # 二重に作らない
        self.assertEqual(self.m.candidates({"min_shops": "1"})["total"], 0)
        # 外すと候補に戻る
        self.m.unlink(sid, self.ids["y1"], "boss")
        back = self.m.candidates({"min_shops": "1"})["rows"]
        self.assertEqual([l["ext_id"] for g in back for l in g["listings"]], ["y1"])
        with self.assertRaises(LookupError):
            self.m.unlink(sid, self.ids["y1"], "boss")
        self.assertEqual([x["what"] for x in self.m.detail(sid)["revisions"]], ["出品を外した", "束ねて作った"])

    def test_update(self):
        sid = self.m.bundle(["UC0001"], "boss")["made"][0]["id"]
        with self.assertRaises(PermissionError):
            self.m.update(sid, {"label": "x"}, "dev")
        with self.assertRaises(ValueError):
            self.m.update(sid, {"label": "  "}, "boss")
        with self.assertRaises(ValueError):
            self.m.update(sid, {"state": "廃番"}, "boss")
        with self.assertRaises(ValueError):
            self.m.update(sid, {"base_price": "千円"}, "boss")
        r = self.m.update(sid, {"label": "名入れうちわ M", "state": "販売中", "base_price": "1,980"}, "boss")
        self.assertEqual(sorted(r["changed"]), ["base_price", "label", "state"])
        self.assertEqual(self.m.update(sid, {"label": "名入れうちわ M"}, "boss")["changed"], [])
        self.m.update(sid, {"base_price": ""}, "boss")
        self.assertIsNone(self.m.detail(sid)["base_price"])                    # 未定は空（0 にしない）
        self.assertEqual(len(self.m.detail(sid)["revisions"]), 5)
        x = self.m.listing_({"q": "UC0001"})
        self.assertEqual((x["total"], x["rows"][0]["n_listings"], x["counts"]), (1, 1, {"販売中": 1}))
        with self.assertRaises(LookupError):
            self.m.detail("nope")


    def test_options_copy_and_override(self):
        from app import optdef
        sid = self.m.bundle(["AG0001"], "boss")["made"][0]["id"]        # g1・r1・y1
        ins = lambda ext, v, pr: self.store.ex("INSERT INTO listing_option (listing_id,src,item_name,value,price,raw_json) VALUES (?,?,?,?,?,'{}')",
                                               (self.ids[ext], "opt_select", "台座の色", v, pr))
        ins("g1", "白", 0); ins("g1", "黒", 200); ins("r1", "白", 0); ins("r1", "黒", 300); ins("y1", "黒", 300)
        ins("u1", "赤", 500)                                                  # 別の売るものの出品（写さない）
        self.store.conn().commit()
        self.assertEqual(self.m.copy_from_listings(sid, "boss"), {"defs": 0, "values": 0})   # まだ定義に寄せていない
        members = ["goods|opt_select|台座の色", "rakuten|opt_select|台座の色", "yahoo_goods|opt_select|台座の色", "uchiwa|opt_select|台座の色"]
        did = optdef.assign({"name": "台座の色", "kind": "追加料金"}, members, "boss")["def_id"]
        self.assertEqual([x["name"] for x in self.m.detail(sid)["in_listings_not_attached"]], ["台座の色"])
        with self.assertRaises(PermissionError):
            self.m.copy_from_listings(sid, "dev")
        self.assertEqual(self.m.copy_from_listings(sid, "boss"), {"defs": 1, "values": 2})    # 白・黒（赤は別の売るもの）
        self.assertEqual(self.m.copy_from_listings(sid, "boss"), {"defs": 0, "values": 0})    # 二重に写さない
        o = self.m.detail(sid)["options"][0]
        v = {x["label"]: x for x in o["values"]}
        # 定義の「黒」は 300 円（いちばん多い）。この売るものの出品も 300 が多いので空（定義に従う）
        self.assertEqual((v["黒"]["def_price"], v["黒"]["price"], v["黒"]["effective"], v["黒"]["enabled"]), (300, None, 300, True))
        self.assertEqual((v["赤"]["enabled"], v["白"]["listings"]), (False, 2))
        with self.assertRaises(ValueError):
            self.m.save_values(sid, did, [{"value_id": v["黒"]["id"], "enabled": True, "price": "三百"}], "boss")
        r = self.m.save_values(sid, did, [{"value_id": v["黒"]["id"], "enabled": True, "price": "250"},
                                          {"value_id": v["赤"]["id"], "enabled": False, "price": ""}], "boss")
        self.assertEqual(r["changed"], 1)                                   # 赤はもともと使わない（行が無い）ので変わらない
        v = {x["label"]: x for x in self.m.detail(sid)["options"][0]["values"]}
        self.assertEqual((v["黒"]["price"], v["黒"]["effective"]), (250, 250))
        with self.assertRaises(ValueError):
            self.m.attach_def(sid, did, "boss")
        self.m.detach_def(sid, did, "boss")
        self.assertEqual(self.m.detail(sid)["options"], [])
        self.m.attach_def(sid, did, "lead")                                 # 付けると値は全部使う・価格は定義
        v = {x["label"]: x for x in self.m.detail(sid)["options"][0]["values"]}
        self.assertTrue(all(x["enabled"] and x["price"] is None for x in v.values()))
        self.assertIn("選択肢を付けた", [x["what"] for x in self.m.detail(sid)["revisions"]])



if __name__ == "__main__":
    unittest.main()
