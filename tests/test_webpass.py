#!/usr/bin/env python3
"""WEBチームへ渡すページ（段階 W・ADR-143）: ①〜⑨・ページの材料・渡すときだけ必須を確かめる・版を固める・
同じ中身で版を上げない・前の版との違い・状態は前へだけ・やり取り・業務ロール。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class WebPass(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, webpass
        seed.run()
        for role, u in (("member", "dev"), ("webmkt", "web"), ("president", "boss")):
            store.ex("INSERT OR IGNORE INTO role_member (role_code, user_id) VALUES (?,?)", (role, u))
        store.conn().commit()
        self.store, self.m, self.project = store, webpass, project
        self.pid = project.create("dev", expand=False, internal_name="名入れアクスタ", flow_type="freecut",
                                  launch_date="2026-12-10")["id"]

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def fill(self):
        for k, v in (("C.target", "推し活の学生・会場で掲げる"), ("C.needs", "名前を大きく見せたい"),
                     ("C.diff", "台座の色を選べる\nフリーカット"), ("C.concept", "推しの名前が主役のアクスタ")):
            self.project.save_section(self.pid, k, v, "dev")
        for k, v in (("appeal", "台座の色\n名入れの大きさ"), ("catch", "名前が主役"), ("deadline", "2026-11-30"), ("contact", "石原")):
            self.m.save_material(self.pid, k, v, "dev")

    def test_steps_and_missing_then_hand(self):
        st = {x["n"]: x for x in self.m.steps(self.pid)}
        self.assertFalse(st[2]["done"])
        self.assertTrue(st[8]["pending"])                                   # 商品ページ案は AI の枠が入ってから
        self.assertFalse(st[9]["done"])
        with self.assertRaises(ValueError) as e:                            # 渡すときだけ必須を確かめる
            self.m.hand(self.pid, "dev")
        self.assertIn("コンセプト", str(e.exception))
        self.assertIn("WEBチームの締切", str(e.exception))
        self.fill()
        st = {x["n"]: x for x in self.m.steps(self.pid)}
        self.assertTrue(all(st[n]["done"] for n in (3, 4, 5, 6, 7)))
        r = self.m.hand(self.pid, "dev")
        self.assertEqual((r["ver"], r["changed"]), (1, None))
        self.assertTrue({x["n"]: x for x in self.m.steps(self.pid)}[9]["done"])
        with self.assertRaises(ValueError):                                # 同じ中身で版を上げない
            self.m.hand(self.pid, "dev")
        self.m.save_material(self.pid, "catch", "名前が主役\n会場で目立つ", "dev")
        ov = self.m.overview(self.pid, "dev")
        self.assertEqual(ov["changed_since_last"], ["なぜ選ばれるか"])
        r2 = self.m.hand(self.pid, "dev")
        self.assertEqual((r2["ver"], r2["changed"]), (2, ["なぜ選ばれるか"]))
        v1 = self.m.version(self.pid, 1)                                    # 前の版は固まったまま
        why = [s for s in v1["page"]["sections"] if s["key"] == "why"][0]
        self.assertEqual([i for i in why["items"] if i["label"] == "キャッチコピーの案"][0]["lines"], ["名前が主役"])
        self.assertEqual([v["ver"] for v in self.m.overview(self.pid, "dev")["versions"]], [2, 1])
        with self.assertRaises(LookupError):
            self.m.version(self.pid, 9)

    def test_roles_and_validation(self):
        with self.assertRaises(PermissionError):
            self.m.save_material(self.pid, "catch", "x", "web")              # WEBチームは材料を書かない
        with self.assertRaises(PermissionError):
            self.m.save_material(self.pid, "catch", "x", "nobody")
        with self.assertRaises(ValueError):
            self.m.save_material(self.pid, "nope", "x", "dev")
        with self.assertRaises(ValueError):
            self.m.save_material(self.pid, "deadline", "11月末", "dev")
        self.fill()
        with self.assertRaises(PermissionError):
            self.m.hand(self.pid, "web")
        self.m.hand(self.pid, "dev")
        can = self.m.overview(self.pid, "web")["can"]
        self.assertEqual((can["edit"], can["hand"], can["comment"]), (False, False, True))
        self.assertEqual(can["states"], ["WEBチーム確認中", "制作中"])
        self.assertEqual(self.m.overview(self.pid, "nobody")["can"]["comment"], False)

    def test_state_forward_only(self):
        with self.assertRaises(ValueError):
            self.m.set_state(self.pid, "WEBチーム確認中", "web")             # まだ渡していない
        self.fill()
        self.m.hand(self.pid, "dev")
        with self.assertRaises(PermissionError):
            self.m.set_state(self.pid, "WEBチーム確認中", "dev")             # 確認中にするのは WEBチーム
        self.m.set_state(self.pid, "制作中", "web")                          # 飛ばして前へは良い
        with self.assertRaises(ValueError):
            self.m.set_state(self.pid, "WEBチーム確認中", "web")             # 戻す向きには変えない
        with self.assertRaises(ValueError):
            self.m.set_state(self.pid, "渡した", "boss")
        self.m.set_state(self.pid, "仕上がり受領", "dev")
        self.assertEqual(self.m.overview(self.pid, "dev")["versions"][0]["state"], "仕上がり受領")

    def test_comments(self):
        self.fill()
        vid = self.m.hand(self.pid, "dev")["version_id"]
        q = self.m.comment(self.pid, vid, "質問", "台座の色は何色？", None, "web")["id"]
        with self.assertRaises(ValueError):
            self.m.comment(self.pid, vid, "答え", "5色", None, "dev")          # 答えは質問に付ける
        self.m.comment(self.pid, vid, "答え", "5色です", str(q), "dev")
        with self.assertRaises(PermissionError):
            self.m.comment(self.pid, vid, "質問", "x", None, "nobody")
        with self.assertRaises(ValueError):
            self.m.comment(self.pid, vid, "雑談", "x", None, "web")
        with self.assertRaises(ValueError):
            self.m.comment(self.pid, vid, "質問", "  ", None, "web")
        with self.assertRaises(LookupError):
            self.m.comment(self.pid, 999, "質問", "x", None, "web")
        cs = self.m.overview(self.pid, "web")["versions"][0]["comments"]
        self.assertEqual([(c["kind"], c["reply_to"]) for c in cs], [("質問", None), ("答え", q)])


if __name__ == "__main__":
    unittest.main()
