#!/usr/bin/env python3
"""
年間・ライフイベントの登録（ADR-060）の検査。

  - 登録・修正は 商品開発部・管理者・社長 だけ。年間イベントは月が必須
  - 同じ名前（表記ゆれ込み）は保存しない。似た名前は確かめてから
  - 名前を直すと、**策定中の版の枠だけ**名前を付け替える（承認済みは記録として残す）
  - 「使わない」にすると機会カレンダーから外れる。消さない
  - ライフイベントの総合点はサーバーで計算（3つそろわなければ未設定）
  - 取り込みツールを流し直しても、画面で直した行は上書きしない
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Events(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-06"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, events, opportunity, plan
        seed.run()
        self.m, self.store, self.opp, self.plan = events, store, opportunity, plan
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id) VALUES ('devdept','dev')")
            c.execute("INSERT INTO role_member (role_code,user_id) VALUES ('president','boss')")

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_permission_and_required_month(self):
        with self.assertRaises(PermissionError):
            self.m.save({"kind": "年間イベント", "label": "推しの日", "month": "5"}, "nobody")
        with self.assertRaises(ValueError):
            self.m.save({"kind": "年間イベント", "label": "推しの日"}, "dev")
        r = self.m.save({"kind": "年間イベント", "label": "推しの日", "month": "5", "day": "5月の第2日曜"}, "dev")
        t = self.store.one("SELECT * FROM theme WHERE id=?", (r["id"],))
        self.assertEqual((t["month"], t["day"], t["edited_by"]), (5, "5月の第2日曜", "dev"))

    def test_duplicate_and_similar(self):
        self.m.save({"kind": "年間イベント", "label": "推しの誕生日", "month": "1"}, "dev")
        with self.assertRaises(ValueError):
            self.m.save({"kind": "年間イベント", "label": "推しの 誕生日", "month": "2"}, "dev")
        r = self.m.save({"kind": "年間イベント", "label": "推しの誕生日会", "month": "2"}, "dev")
        self.assertFalse(r["ok"])
        self.assertEqual([x["label"] for x in r["similar"]], ["推しの誕生日"])
        self.assertTrue(self.m.save({"kind": "年間イベント", "label": "推しの誕生日会", "month": "2", "confirm": "1"}, "dev")["ok"])

    def test_rename_moves_only_draft_slots_and_inactive_hides(self):
        eid = self.m.save({"kind": "年間イベント", "label": "敬老の日", "month": "12"}, "dev")["id"]
        draft = self.plan.create_version("boss", 2027)["id"]
        self.plan.create_slot("boss", version_id=draft, occasion="敬老の日", launch_month="2027-10")
        old = self.plan.create_version("boss", 2026)["id"]
        self.plan.create_slot("boss", version_id=old, occasion="敬老の日", launch_month="2026-10")
        self.plan.approve(old, "boss")
        self.m.save({"id": eid, "label": "敬老の日（祖父母）", "month": "12", "confirm": "1"}, "dev")
        occ = {r["version_id"]: r["occasion"] for r in self.store.q("SELECT version_id, occasion FROM plan_slot")}
        self.assertEqual(occ, {draft: "敬老の日（祖父母）", old: "敬老の日"}, "承認済みの版は書き換えない")
        self.assertIn("敬老の日（祖父母）", [a["label"] for a in self.opp.calendar()["annual"]])
        self.m.set_active(eid, False, "dev")
        self.assertNotIn("敬老の日（祖父母）", [a["label"] for a in self.opp.calendar()["annual"]])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM theme WHERE id=?", (eid,)), 1, "消さない")

    def test_life_total_computed_and_partial_is_unset(self):
        r = self.m.save({"kind": "ライフイベント", "label": "初めての推し活", "gift_intent": "8", "photo_fit": "9", "frequency": "4"}, "dev")
        self.assertEqual(self.store.val("SELECT total FROM theme_score WHERE theme_id=?", (r["id"],)), 8 * 2 + 9 + 4 * 2)
        r2 = self.m.save({"kind": "ライフイベント", "label": "推し卒業", "gift_intent": "8"}, "dev")
        self.assertIsNone(self.store.val("SELECT total FROM theme_score WHERE theme_id=?", (r2["id"],)), "0 にしない")
        with self.assertRaises(ValueError):
            self.m.save({"kind": "ライフイベント", "label": "結婚", "frequency": "6"}, "dev")   # 頻度は0〜5

    def test_import_does_not_overwrite_edited_rows(self):
        sys.path.insert(0, str(BASE / "tools"))
        import import_events as ie
        d = ie.parse(ie.read_rows())
        ie.apply(d)
        first = d["annual"][0]["name"]
        eid = ie.theme_id("年間イベント", first)
        self.m.save({"id": eid, "label": first, "month": "7", "day": "画面で直した"}, "dev")
        ie.apply(d)
        t = self.store.one("SELECT month, day FROM theme WHERE id=?", (eid,))
        self.assertEqual((t["month"], t["day"]), (7, "画面で直した"))


if __name__ == "__main__":
    unittest.main()
