#!/usr/bin/env python3
"""発売済みにしたときの CIP の確認（2026-11-10 依頼書 4-5・ADR-137）。
CIP には書かない・在る／無い／未登録（不足を出す）／問い合わせられない・発売済みは戻さない・済はやり直さない。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

VOC = {"source": "seisan", "sales_types": ["名入れ"], "cat1": None, "cat2": None, "cat3": None}


class Link(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, seisan, launchlink
        seed.run()
        self.store, self.s, self.m = store, seisan, launchlink
        self.pid = project.create("u", expand=False, internal_name="x", flow_type="meire", launch_date="2026-11-13")["id"]
        self.in_cip = {"A1"}
        self.ps = [mock.patch.object(seisan, "configured", return_value=(True, None)),
                   mock.patch.object(seisan, "vocab", return_value=VOC),
                   mock.patch.object(seisan, "exists", side_effect=lambda c: c in self.in_cip)]
        for p_ in self.ps:
            p_.start()

    def tearDown(self):
        for p_ in self.ps:
            p_.stop()
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def _variant(self, label, code=None, draft=None):
        vid = self.store.ex("INSERT INTO project_variant (project_id,label) VALUES (?,?)", (self.pid, label)).lastrowid
        if code:
            self.store.ex("INSERT INTO seisan_registration (project_id,variant_id,state,product_code) VALUES (?,?,?,?)",
                          (self.pid, vid, "登録済", code))
        elif draft is not None:
            import json
            self.store.ex("INSERT INTO seisan_registration (project_id,variant_id,state,draft_json) VALUES (?,?,?,?)",
                          (self.pid, vid, "下書き", json.dumps(draft)))
        self.store.conn().commit()
        return vid

    def test_all_in_cip_is_done_and_not_redone(self):
        self._variant("赤", "A1")
        r = self.m.check_cip(self.pid, "u")
        self.assertEqual(r["state"], "済")
        with self.assertRaises(ValueError):
            self.m.retry(self.pid, "cip", "u")                  # 済はやり直さない

    def test_missing_and_unregistered(self):
        self._variant("赤", "A1")
        self._variant("緑", "ZZ9")                               # CIP に無い
        self._variant("黄", draft={"name": "x"})                 # 未登録・下書きに不足
        r = self.m.check_cip(self.pid, "u")
        self.assertEqual(r["state"], "要確認")
        by = {i["label"]: i for i in r["detail"]["items"]}
        self.assertEqual(by["赤"]["state"], "CIP に在る")
        self.assertEqual(by["緑"]["state"], "CIP に無い")
        self.assertEqual(by["黄"]["state"], "未登録")
        self.assertIn("共通商品コードを入れてください", by["黄"]["why"])   # 不足は出す・推測で埋めない
        self.assertIsNone(by["黄"]["code"])

    def test_cip_down_is_failure_and_launch_stays(self):
        from app import gate
        self._variant("赤", "A1")
        with mock.patch.object(self.s, "exists", side_effect=RuntimeError("down")):
            self.store.ex("UPDATE project SET stage='生産確定' WHERE id=?", (self.pid,))
            self.store.conn().commit()
            out = gate._launched(dict(self.store.one("SELECT * FROM project WHERE id=?", (self.pid,))), "u")
        self.assertEqual(out["links"]["cip"]["state"], "失敗")
        self.assertEqual(self.store.val("SELECT stage FROM project WHERE id=?", (self.pid,)), "発売済")  # 戻さない
        r = self.m.retry(self.pid, "cip", "u")                   # 口が戻ったらもう一度
        self.assertEqual(r["state"], "済")
        self.assertEqual(self.m.of_project(self.pid)[0]["tries"], 2)


if __name__ == "__main__":
    unittest.main()
