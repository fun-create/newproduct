#!/usr/bin/env python3
"""FCTR の受け取り（FR-135〜137）。仮の週次ファイルで見る。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def weekly(week, themes, version=1):
    return {"contract_version": version, "generated_at": "2026-09-28T02:11:20+09:00", "week_id": week,
            "scope": {"not_produced": ["自社側70点は新商品開発アプリの担当"]},
            "sources_ok": ["competitors"], "sources_disabled": [], "sources_unavailable": [], "notes": [],
            "segments": [{"segment_id": "funcreate_btoc", "site_name": "fun-create.jp",
                          "segment_name": "ライフイベント", "themes": [
                {"theme": t, "theme_key": t, "market_score": sc, "market_score_max": 25.0,
                 "consecutive_weeks": 1, "novelty": "new", "cycle": 1,
                 "weeks_present": [obs or week], "observed_this_week": (obs or week) == week,
                 "evidence": [{"signal": "競合3社"}] if (obs or week) == week else [],
                 "components": {}} for t, sc, obs in [(x + (None,))[:3] for x in themes]]}]}


class Fctr(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        self.json = Path(tempfile.mkdtemp()) / "fctr_weekly.json"
        os.environ["NEWPRODUCT_FCTR"] = str(self.json)
        os.environ["NEWPRODUCT_TODAY"] = "2026-09-28"           # 2026-W40
        from app import store
        store.close()
        from app import seed, fctr
        seed.run()
        self.m, self.store = fctr, store

    def tearDown(self):
        self.store.close()
        for k in ("NEWPRODUCT_FCTR", "NEWPRODUCT_TODAY"):
            os.environ.pop(k, None)
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def put(self, *a, **k):
        self.json.write_text(json.dumps(weekly(*a, **k), ensure_ascii=False), encoding="utf-8")

    def test_ingest_once_and_top3(self):
        self.put("2026-W40", [("結婚式記念", 16.17), ("出産", 12.0), ("卒業", 10.0), ("成人式", 5.0)])
        b = self.m.board()
        self.assertEqual(self.m.ingest()["added"], 0, "同じ週を二度入れない")
        ts = b["segments"][0]["themes"]
        self.assertEqual([t["label"] for t in ts if t["top"]], ["結婚式記念", "出産", "卒業"])
        self.assertEqual(ts[0]["decayed"], 16.17)
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM theme WHERE kind='FCTRテーマ'"), 4)

    def test_upstream_decayed_score_used_as_is(self):
        """**上流が減衰をかけ済み。**今週観測されなかった行の点をこちらで重ねて下げない（ADR-055）。"""
        self.put("2026-W40", [("痛バ", 7.30)])
        self.m.board()
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-05"          # W41
        self.put("2026-W41", [("痛バ", 6.69, "2026-W40"), ("ハロウィン", 8.0)])
        ts = {t["label"]: t for t in self.m.board()["segments"][0]["themes"]}
        self.assertEqual(ts["痛バ"]["decayed"], 6.69, "上流の値をそのまま")
        self.assertEqual((ts["痛バ"]["observed_week"], ts["痛バ"]["age_weeks"]), ("2026-W40", 1))
        self.assertEqual(ts["ハロウィン"]["age_weeks"], 0)

    def test_theme_dropped_upstream_disappears(self):
        self.put("2026-W40", [("結婚式記念", 16.0)])
        self.m.board()
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-26"          # W44
        self.put("2026-W44", [("ハロウィン", 8.0)])
        labels = [t["label"] for t in self.m.board()["segments"][0]["themes"]]
        self.assertEqual(labels, ["ハロウィン"], "上流の一覧から消えたテーマは出さない（古い週の行を引きずらない）")

    def test_stale_upstream_is_said(self):
        self.put("2026-W40", [("結婚式記念", 16.0)])
        self.assertIsNone(self.m.board()["stale"])
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-19"          # W43・上流が W40 のまま
        b = self.m.board()
        self.assertIn("2026-W40", b["stale"])
        self.assertEqual(b["segments"][0]["themes"][0]["decayed"], 16.0, "止まっていても点を勝手に作らない")

    def test_other_version_stops_with_reason(self):
        self.put("2026-W40", [("結婚式記念", 16.0)], version=2)
        b = self.m.board()
        self.assertIn("版", b["why"])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM theme_signal"), 0)

    def test_to_idea_once(self):
        self.put("2026-W40", [("結婚式記念", 16.17)])
        self.m.board()
        r = self.m.to_idea("fctr:結婚式記念", "funcreate_btoc", "u")
        i = self.store.one("SELECT * FROM idea WHERE id=?", (r["id"],))
        self.assertEqual((i["origin"], i["title"], i["theme_id"]), ("fctr", "結婚式記念", "fctr:結婚式記念"))
        self.assertIn("16.17", i["summary"])
        with self.assertRaises(ValueError):
            self.m.to_idea("fctr:結婚式記念", "funcreate_btoc", "u")
        self.assertEqual(self.m.board()["segments"][0]["themes"][0]["idea_id"], r["id"])


    def test_self_score_70_by_devdept_and_total_only_when_complete(self):
        self.put("2026-W40", [("結婚式記念", 16.0)])
        self.m.board()
        with self.store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_at) VALUES ('devdept','dev','x')")
        with self.assertRaises(PermissionError):
            self.m.save_self("fctr:結婚式記念", {"fit": "20"}, "u")
        with self.assertRaises(ValueError):
            self.m.save_self("fctr:結婚式記念", {"fit": "26"}, "dev")          # 上限25
        self.m.save_self("fctr:結婚式記念", {"fit": "20", "ops": "15"}, "dev")
        t = self.m.board()["segments"][0]["themes"][0]
        self.assertIsNone(t["total100"], "4軸がそろうまで合計を出さない")
        self.assertIsNone(t["self"]["speed"])
        self.m.save_self("fctr:結婚式記念", {"fit": "20", "ops": "15", "speed": "10", "profit": "5"}, "dev")
        self.assertEqual(self.m.board()["segments"][0]["themes"][0]["total100"], 66.0)    # 16 + 50


if __name__ == "__main__":
    unittest.main()
