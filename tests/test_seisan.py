#!/usr/bin/env python3
"""
seisan への商品登録（第3段 ／ FR-103・FR-60 ／ ADR-043）の検査。**外部通信をしない。**

2026-09-28 十文字さん「NEW PRODUCT 側で seisan のルールに沿って登録し、
マスタ管理は全て seisan 側で持つ」。見ているのは:

  - **マスタを持たない**こと（登録できたら下書きが消え、コードだけが残る）
  - **seisan が断るものを先に止める**こと（必須4項目・販売タイプ10種・既存の分類）
  - **seisan が受け付けたときだけ**登録済にすること
  - seisan の口が無いあいだも、**seisan で登録したコードを記録**でき、「未確認」と出ること
  - **seisan から名前が返ってきても持たない**こと
  - 押せるのは G5 を判定できる業務ロールだけ
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

VOCAB = {"sales_types": ["Webdeco", "名入れ", "定型"],
         "cat1": ["うちわ", "スマホケース"], "cat2": ["ハード"], "cat3": ["iPhone"]}


class FakeSeisan:
    """seisan の口の偽物。依頼書（contracts/2026-09-28_seisan_商品登録API.md）の形で返す。"""

    def __init__(self):
        self.products = {"OLD-1"}
        self.calls = []
        self.missing_api = False

    def __call__(self, method, path, params, body):
        self.calls.append((method, path, params, body))
        if self.missing_api:
            return 404, {}
        if path == "/api/svc/master/vocab":
            return 200, VOCAB
        if path == "/api/svc/master/product":
            return 200, {"exists": params["code"] in self.products}
        if path == "/api/svc/master/products":
            # **名前を混ぜて返す**（seisan 側の実装がずれた場合）。持たないことを見る
            return 200, {"items": [{"code": "OLD-1", "cat1": "うちわ", "name": "山田 花子様用",
                                    "has_recipe": True}]}
        if path == "/api/svc/master/store_codes":
            return 200, {"as_of": "x", "items": [
                {"store": "グッズ本店", "store_code": "gd1", "product_code": "P1", "cat1": "布製品",
                 "product_name": "山田 花子様用", "name": "x", "pack_qty": 1}],
                "fba": [{"sku": "F1", "product_code": "P1", "cat1": "布製品", "name": "y"}]}
        if path == "/api/svc/master/product/create":
            if body["code"] in self.products:
                return 400, {"error": f"商品コード「{body['code']}」は既に存在します"}
            if body["actor"] == "not-seisan-admin":
                return 403, {"error": "seisan の管理者ではありません"}
            self.products.add(body["code"])
            return 200, {"ok": True, "code": body["code"]}
        return 404, {}


class Base(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-09-28"
        self.dbfile = p
        self.envdir = tempfile.mkdtemp()
        self.envfile = Path(self.envdir) / "seisan.env"
        os.environ["NEWPRODUCT_SEISAN_ENV"] = str(self.envfile)   # 既定は「無い」
        from app import store
        store.close()
        from app import seed
        seed.run()
        from app import project, seisan
        self.m = seisan
        self.fake = FakeSeisan()
        self._orig = seisan.transport
        seisan.transport = self.fake
        store.ex("INSERT INTO role_member (role_code,user_id,granted_at) "
                 "VALUES ('admin','kanri',?)", (store.now_s(),))
        store.conn().commit()
        self.pid = project.create("kanri", internal_name="検査ケース", flow_type="meire",
                                  launch_date="2026-11-27", cat1="スマホケース")["id"]

    def tearDown(self):
        from app import store
        self.m.transport = self._orig
        store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass
        os.environ.pop("NEWPRODUCT_SEISAN_ENV", None)

    def configure(self):
        self.envfile.write_text("SEISAN_SVC_TOKEN=test-token\n", encoding="utf-8")

    def good(self, **kw):
        d = {"code": "NEW-1", "name": "検査用ケース", "sales_type": "定型",
             "cat1": "スマホケース", "cat2": "ハード", "copy_recipe_from": "OLD-1"}
        d.update(kw)
        return d


class NotConfiguredYet(Base):
    """**いまの本番の状態**（seisan の口が無い）。"""

    def test_overview_says_why_and_uses_sales_type_copy(self):
        o = self.m.overview(self.pid, "kanri")
        self.assertFalse(o["configured"])
        self.assertIn("接続設定", o["why"])
        self.assertEqual(o["vocab"]["source"], "copy")
        self.assertEqual(len(o["vocab"]["sales_types"]), 10)
        self.assertIsNone(o["vocab"]["cat1"])       # 分類は出せない。**空リストにしない**
        self.assertEqual(o["targets"][0]["state"], "未着手")
        # 案件から写せるものは写す
        self.assertEqual(o["targets"][0]["draft"]["cat1"], "スマホケース")
        self.assertEqual(self.fake.calls, [])       # 設定が無ければ一度も呼ばない

    def test_register_is_refused_with_reason(self):
        self.m.save_draft(self.pid, None, self.good(), "kanri")
        with self.assertRaises(self.m.NotConfigured):
            self.m.register(self.pid, None, "kanri")
        self.assertEqual(self.m.overview(self.pid, "kanri")["targets"][0]["state"], "下書き")

    def test_record_code_registered_in_seisan_is_unverified(self):
        r = self.m.record_code(self.pid, None, "S-100", "kanri")
        self.assertFalse(r["verified"])
        ok, why = self.m.gate_state(self.pid)
        self.assertTrue(ok)
        self.assertIn("未確認", why)

    def test_category_unknown_is_warning_not_error(self):
        errs, warns = self.m.validate(self.good(), self.m.vocab())
        self.assertEqual(errs, [])
        self.assertTrue(any("確かめられません" in w for w in warns))


class Rules(Base):
    """seisan が断るものを、ここで先に止める。"""

    def setUp(self):
        super().setUp()
        self.configure()

    def errs(self, **kw):
        return self.m.validate(self.good(**kw), self.m.vocab())[0]

    def test_required_four(self):
        for k in ("code", "name", "sales_type", "cat1"):
            self.assertTrue(self.errs(**{k: ""}), k)
        self.assertEqual(self.errs(cat2="", cat3=""), [])

    def test_sales_type_and_categories_from_seisan(self):
        self.assertTrue(self.errs(sales_type="オリジナル"))
        self.assertTrue(self.errs(cat1="新しい分類"))     # 自由入力で共有マスタを汚さない
        self.assertTrue(self.errs(cat2="ソフト"))

    def test_code_shape(self):
        self.assertTrue(self.errs(code="NEW 1"))
        self.assertTrue(self.errs(copy_recipe_from="NEW-1"))

    def test_no_recipe_source_is_a_warning(self):
        errs, warns = self.m.validate(self.good(copy_recipe_from=""), self.m.vocab())
        self.assertEqual(errs, [])
        self.assertTrue(any("未確定" in w for w in warns))

    def test_register_does_not_send_when_invalid(self):
        self.m.save_draft(self.pid, None, self.good(sales_type="オリジナル"), "kanri")
        with self.assertRaises(ValueError):
            self.m.register(self.pid, None, "kanri")
        self.assertFalse([c for c in self.fake.calls if c[0] == "POST"])


class Register(Base):
    def setUp(self):
        super().setUp()
        self.configure()

    def test_success_keeps_only_the_code(self):
        from app import store
        self.m.save_draft(self.pid, None, self.good(), "kanri")
        r = self.m.register(self.pid, None, "kanri")
        self.assertEqual(r["product_code"], "NEW-1")
        row = store.one("SELECT * FROM seisan_registration WHERE project_id=?", (self.pid,))
        self.assertEqual(row["state"], "登録済")
        self.assertIsNone(row["draft_json"])       # **マスタを持たない**
        self.assertIsNotNone(row["verified_at"])
        self.assertEqual(row["via"], "newproduct")
        post = [c for c in self.fake.calls if c[0] == "POST"][0][3]
        self.assertEqual(post["actor"], "kanri")    # seisan 側で管理者かを確かめるため
        self.assertEqual(post["copy_recipe_from"], "OLD-1")
        self.assertNotIn("code", post["fields"])
        self.assertTrue(self.m.gate_state(self.pid)[0])
        # 登録後は直させない（正は seisan）
        with self.assertRaises(ValueError):
            self.m.save_draft(self.pid, None, self.good(name="変更"), "kanri")

    def test_seisan_refusal_keeps_draft_and_reason(self):
        from app import store
        self.m.save_draft(self.pid, None, self.good(code="OLD-1", copy_recipe_from=""),
                          "kanri")
        with self.assertRaises(ValueError) as cm:
            self.m.register(self.pid, None, "kanri")
        self.assertIn("seisan で登録した", str(cm.exception))
        row = store.one("SELECT * FROM seisan_registration WHERE project_id=?", (self.pid,))
        self.assertEqual(row["state"], "下書き")
        self.assertIsNotNone(row["draft_json"])
        self.assertIn("既に存在", row["last_error"])
        self.assertFalse(self.m.gate_state(self.pid)[0])

    def test_seisan_admin_check_is_seisans(self):
        from app import store
        store.ex("INSERT INTO role_member (role_code,user_id,granted_at) "
                 "VALUES ('admin','not-seisan-admin',?)", (store.now_s(),))
        store.conn().commit()
        self.m.save_draft(self.pid, None, self.good(), "kanri")
        with self.assertRaises(ValueError):
            self.m.register(self.pid, None, "not-seisan-admin")
        self.assertFalse(self.m.gate_state(self.pid)[0])

    def test_record_code_checks_existence(self):
        with self.assertRaises(ValueError):
            self.m.record_code(self.pid, None, "NOPE-9", "kanri")
        r = self.m.record_code(self.pid, None, "OLD-1", "kanri")
        self.assertTrue(r["verified"])

    def test_api_not_built_yet_behaves_as_not_configured(self):
        self.fake.missing_api = True
        self.assertEqual(self.m.vocab()["source"], "copy")
        r = self.m.record_code(self.pid, None, "S-1", "kanri")
        self.assertFalse(r["verified"])
        # 口ができたら、見たときに確かめ直す
        self.fake.missing_api = False
        self.fake.products.add("S-1")
        self.assertTrue(self.m.overview(self.pid, "kanri")["targets"][0]["verified"])
        # **画面を開く経路の書き込みも確定している**（別の接続から見える）
        import sqlite3
        other = sqlite3.connect(self.dbfile)
        self.assertIsNotNone(other.execute(
            "SELECT verified_at FROM seisan_registration").fetchone()[0])
        other.close()

    def test_similar_never_keeps_names(self):
        rows = self.m.similar("うちわ")
        self.assertEqual(rows[0]["code"], "OLD-1")
        self.assertNotIn("name", rows[0])

    def test_store_codes_never_keep_names(self):
        j = self.m.store_codes()
        self.assertEqual(j["items"][0]["store_code"], "gd1")
        self.assertNotIn("product_name", j["items"][0])
        self.assertNotIn("name", j["items"][0])
        self.assertNotIn("name", j["fba"][0])

    def test_same_code_twice_is_refused(self):
        from app import project
        other = project.create("kanri", internal_name="別案件", flow_type="meire",
                               launch_date="2026-12-01")["id"]
        self.m.record_code(self.pid, None, "OLD-1", "kanri")
        with self.assertRaises(ValueError):
            self.m.record_code(other, None, "OLD-1", "kanri")


class Permission(Base):
    def test_only_g5_role_can_register_or_record(self):
        self.configure()
        self.m.save_draft(self.pid, None, self.good(), "someone")     # 下書きは誰でも
        with self.assertRaises(PermissionError):
            self.m.register(self.pid, None, "someone")
        with self.assertRaises(PermissionError):
            self.m.record_code(self.pid, None, "OLD-1", "someone")
        self.assertFalse(self.m.overview(self.pid, "someone")["can_register"])

    def test_drive_owned_project_is_read_only(self):
        from app import store
        store.ex("UPDATE project SET source_of_truth='drive' WHERE id=?", (self.pid,))
        store.conn().commit()
        with self.assertRaises(PermissionError):
            self.m.save_draft(self.pid, None, self.good(), "kanri")


class Variants(Base):
    def setUp(self):
        super().setUp()
        from app import store
        for lb in ("iPhone 17", "iPhone 17 Pro"):
            store.ex("INSERT INTO project_variant (project_id,label) VALUES (?,?)",
                     (self.pid, lb))
        store.conn().commit()
        self.vids = [r["id"] for r in store.q(
            "SELECT id FROM project_variant WHERE project_id=? ORDER BY label", (self.pid,))]

    def test_each_variant_is_one_product(self):
        from app import store
        with self.assertRaises(ValueError):
            self.m.save_draft(self.pid, None, self.good(), "kanri")   # どれかを選ばせる
        self.m.record_code(self.pid, self.vids[0], "V-1", "kanri")
        ok, why = self.m.gate_state(self.pid)
        self.assertFalse(ok)
        self.assertIn("1/2", why)
        self.m.record_code(self.pid, self.vids[1], "V-2", "kanri")
        self.assertTrue(self.m.gate_state(self.pid)[0])
        self.assertEqual(store.val("SELECT product_code FROM project_variant WHERE id=?",
                                   (self.vids[1],)), "V-2")
        self.m.reset(self.pid, self.vids[1], "kanri")
        self.assertIsNone(store.val("SELECT product_code FROM project_variant WHERE id=?",
                                    (self.vids[1],)))
        self.assertFalse(self.m.gate_state(self.pid)[0])

    def test_variant_of_another_project_is_refused(self):
        from app import project
        other = project.create("kanri", internal_name="別案件", flow_type="meire",
                               launch_date="2026-12-01")["id"]
        with self.assertRaises(LookupError):
            self.m.record_code(other, self.vids[0], "V-9", "kanri")


class Gate(Base):
    def test_g5_item_uses_seisan_registration(self):
        from app import gate
        gd = next(g for g in gate.defs() if g["gate"] == "G5")
        it = next(i for i in gd["required_items"] if i["key"] == "product_code")
        self.assertEqual(it["check"], "seisan")
        miss = [m["key"] for m in gate.missing(self.pid, "G5")]
        self.assertIn("product_code", miss)
        self.m.record_code(self.pid, None, "S-7", "kanri")
        miss = [m["key"] for m in gate.missing(self.pid, "G5")]
        self.assertNotIn("product_code", miss)


if __name__ == "__main__":
    unittest.main()
