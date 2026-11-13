#!/usr/bin/env python3
"""WEBチームへ渡す素材（ADR-144）: 中身で種類を見分ける（SVG・知らないものは断る）・大きさ・数の上限・業務ロール・
置き場に人の付けた名前を使わない・外しても渡した版からは見られる・同じ中身はファイル1つ。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

JPG = b"\xff\xd8\xff\xe0" + b"0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
PDF = b"%PDF-1.7\n" + b"0" * 100
HEIC = b"\x00\x00\x00\x18ftypheic" + b"0" * 100
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


class WebAsset(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.media = tempfile.TemporaryDirectory()
        os.environ["NEWPRODUCT_MEDIA"] = self.media.name
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, webasset, webpass
        seed.run()
        for role, u in (("member", "dev"), ("webmkt", "web"), ("president", "boss")):
            store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES (?,?)", (role, u))
        store.conn().commit()
        self.store, self.m, self.wp, self.project = store, webasset, webpass, project
        self.pid = project.create("dev", expand=False, internal_name="名入れアクスタ", flow_type="freecut",
                                  launch_date="2026-12-10")["id"]

    def tearDown(self):
        self.store.close()
        os.environ.pop("NEWPRODUCT_MEDIA", None)
        self.media.cleanup()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_types_by_content(self):
        self.assertEqual(self.m.sniff(JPG)[0], "image/jpeg")
        self.assertEqual(self.m.sniff(PNG)[0], "image/png")
        self.assertEqual(self.m.sniff(PDF)[0], "application/pdf")
        self.assertEqual(self.m.sniff(HEIC)[0], "image/heic")
        self.assertIsNone(self.m.sniff(SVG))
        with self.assertRaises(ValueError):                              # 拡張子が .jpg でも中身が SVG なら断る
            self.m.save(self.pid, "x.jpg", SVG, "写真", "", "dev")
        with self.assertRaises(ValueError):
            self.m.save(self.pid, "x.png", b"hello", "写真", "", "dev")
        with self.assertRaises(ValueError):
            self.m.save(self.pid, "x.jpg", b"", "写真", "", "dev")
        with self.assertRaises(ValueError):
            self.m.save(self.pid, "x.jpg", JPG, "動画", "", "dev")
        with self.assertRaises(ValueError):
            self.m.save(self.pid, "big.jpg", b"\xff\xd8\xff" + b"0" * self.m.MAX_BYTES, "写真", "", "dev")

    def test_roles_and_storage_name(self):
        with self.assertRaises(PermissionError):
            self.m.save(self.pid, "a.jpg", JPG, "写真", "", "web")
        r = self.m.save(self.pid, "../../../etc/passwd.jpg", JPG, "写真", "台座の色", "dev")
        row = self.store.one("SELECT * FROM web_asset WHERE id=?", (r["id"],))
        self.assertEqual(row["name"], "passwd.jpg")                     # 画面に出す名前もパスを落とす
        self.assertNotIn("..", row["stored"])
        self.assertTrue(row["stored"].startswith(self.pid + "/") and row["stored"].endswith(".jpg"))
        path, mime, name = self.m.get(self.pid, r["id"])
        self.assertEqual((path.read_bytes(), mime), (JPG, "image/jpeg"))
        r2 = self.m.save(self.pid, "same.jpg", JPG, "図", "", "dev")      # 同じ中身はファイル1つ・行は2つ
        self.assertEqual(len(list((Path(self.media.name) / self.pid).iterdir())), 1)
        self.assertEqual(len(self.m.listing(self.pid)), 2)
        with self.assertRaises(LookupError):                             # 別の案件の番号では出さない
            other = self.project.create("dev", expand=False, internal_name="別", flow_type="freecut")["id"]
            self.m.get(other, r2["id"])

    def test_delete_keeps_for_frozen_versions(self):
        a = self.m.save(self.pid, "台座.png", PNG, "写真", "", "dev")["id"]
        for k, v in (("C.target", "学生"), ("C.needs", "目立つ"), ("C.diff", "色"), ("C.concept", "主役")):
            self.project.save_section(self.pid, k, v, "dev")
        for k, v in (("appeal", "色"), ("catch", "主役"), ("deadline", "2026-11-30"), ("contact", "石原")):
            self.wp.save_material(self.pid, k, v, "dev")
        page = self.wp.build(self.pid)
        assets = [i for s in page["sections"] if s["key"] == "assets" for i in s["items"] if i.get("assets")]
        self.assertEqual([x["id"] for x in assets[0]["assets"]], [a])
        self.wp.hand(self.pid, "dev")
        with self.assertRaises(PermissionError):
            self.m.delete(self.pid, a, "web")
        self.m.delete(self.pid, a, "dev")
        self.assertEqual(self.m.listing(self.pid), [])
        self.assertTrue(self.m.get(self.pid, a)[0].exists())             # 渡した版からは見られる
        v1 = self.wp.version(self.pid, 1)["page"]
        self.assertTrue(any(i.get("assets") for s in v1["sections"] for i in s["items"]))
        self.assertEqual(self.wp.overview(self.pid, "dev")["changed_since_last"], ["素材"])   # 外すと中身が変わる
        with self.assertRaises(LookupError):
            self.m.delete(self.pid, a, "dev")                             # 2度は外せない

    def test_count_limit(self):
        self.m.MAX_PER_PROJECT, keep = 2, self.m.MAX_PER_PROJECT
        try:
            self.m.save(self.pid, "1.jpg", JPG, "写真", "", "dev")
            self.m.save(self.pid, "2.png", PNG, "写真", "", "dev")
            with self.assertRaises(ValueError):
                self.m.save(self.pid, "3.pdf", PDF, "そのほか", "", "dev")
        finally:
            self.m.MAX_PER_PROJECT = keep


if __name__ == "__main__":
    unittest.main()
