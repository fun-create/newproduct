#!/usr/bin/env python3
"""店への書き出しの承認と反映の記録（段階4・ADR-152）: 検査で止まれば頼めない・承認できるのは設定の人だけ・
差し戻しは理由が要る・承認でファイルができ中身が残る・承認の時点でも検査する・結果の記録・状態の順。"""
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
from tests.test_listing import fs_files  # noqa: E402


class ExportReq(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, listing, exportreq
        seed.run()
        for role, u in (("president", "masateru"), ("member", "dev"), ("admin", "lead"), ("prod", "tsubasa")):
            store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES (?,?)", (role, u))
        store.conn().commit()
        self.store, self.m = store, exportreq
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name) / "goods"
        fs_files(d)
        listing.apply("goods", [d], "masateru")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_flow(self):
        self.assertEqual(self.m.approvers("更新"), ["masateru", "tsubasa"])
        with self.assertRaises(PermissionError):
            self.m.create("goods", ["a001"], "", "tsubasa")                 # 渡邊さんは承認者だが頼む業務ロールではない
        with self.assertRaises(LookupError):
            self.m.create("goods", ["a001", "nope"], "", "dev")
        r = self.m.create("goods", ["a001"], "試しの1件", "dev")
        self.assertEqual(r["kind"], "更新")
        rid = r["id"]
        with self.assertRaises(PermissionError):
            self.m.decide(rid, True, "", "lead")                              # 商品開発部管理者は更新を承認できない
        with self.assertRaises(PermissionError):
            self.m.record_result(rid, True, "", "nobody")
        with self.assertRaises(ValueError):
            self.m.record_result(rid, True, "", "dev")                        # まだ書き出していない（頼んだ人でも）
        d = self.m.detail(rid, "tsubasa")
        self.assertTrue(d["can_decide"])
        self.assertEqual([x["商品URLコード"] for x in d["preview"]], ["a001"])
        out = self.m.decide(rid, True, "中身を見た", "tsubasa")
        self.assertEqual(out["state"], "書き出し済み")
        self.assertEqual(len(out["files"]), 4)                                  # 商品・項目選択肢・オプション価格2種
        with self.assertRaises(ValueError):
            self.m.decide(rid, True, "", "masateru")                           # 2度は承認しない
        d = self.m.detail(rid, "dev")
        f = [x for x in d["files"] if x["kind"] == "goods"][0]
        name, data = self.m.file_bytes(rid, f["id"])
        rows = list(csv.DictReader(io.StringIO(data.decode("cp932"), newline="")))
        self.assertEqual((name.startswith("ccGoods_NP"), len(rows), rows[0]["商品URLコード"]), (True, 1, "a001"))
        self.assertTrue(d["can_record"])
        with self.assertRaises(ValueError):
            self.m.record_result(rid, False, "", "dev")                         # 失敗は内容が要る
        self.assertEqual(self.m.record_result(rid, False, "列が足りないと言われた", "dev")["state"], "反映失敗")
        self.assertEqual(self.m.record_result(rid, True, "", "dev")["state"], "反映済み")
        with self.assertRaises(ValueError):
            self.m.record_result(rid, True, "", "dev")
        self.assertEqual([x["state"] for x in self.m.listing_({})["rows"]], ["反映済み"])

    def test_reject_and_checks(self):
        rid = self.m.create("goods", ["a002"], "", "dev")["id"]
        with self.assertRaises(ValueError):
            self.m.decide(rid, False, "", "masateru")                          # 差し戻しは理由が要る
        self.assertEqual(self.m.decide(rid, False, "価格を直してから", "masateru")["state"], "差し戻し")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM export_file"), 0)
        # 検査で止まるものは頼めない
        self.store.ex("UPDATE listing SET raw_json=json_set(raw_json, '$.ステータス', '9') WHERE ext_id='a002'")
        self.store.conn().commit()
        with self.assertRaises(ValueError):
            self.m.create("goods", ["a002"], "", "dev")
        # 頼んだ後に壊れたら、承認の時点で止める
        rid2 = self.m.create("goods", ["a001"], "", "dev")["id"]
        self.store.ex("UPDATE listing SET raw_json=json_set(raw_json, '$.商品名', '') WHERE ext_id='a001'")
        self.store.conn().commit()
        with self.assertRaises(ValueError):
            self.m.decide(rid2, True, "", "masateru")
        self.assertEqual(self.m.detail(rid2, "masateru")["state"], "承認待ち")


if __name__ == "__main__":
    unittest.main()
