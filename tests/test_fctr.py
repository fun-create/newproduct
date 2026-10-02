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
                 "evidence": [{"signal": "競合3社"}], "components": {}} for t, sc in themes]}]}


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

    def test_decay_over_8_weeks(self):
        self.assertEqual(self.m.decay(16.0, "2026-W40", "2026-W40"), 16.0)
        self.assertEqual(self.m.decay(16.0, "2026-W40", "2026-W44"), 8.0)
        self.assertEqual(self.m.decay(16.0, "2026-W40", "2026-W48"), 0.0)
        # 2026年は ISO で53週まである。W52 → W53 → 2027-W01 → W02 で3週（番号の引き算だと2週と誤る）
        self.assertEqual(self.m.decay(16.0, "2026-W52", "2027-W02"), 10.0, "年をまたいでも実際の週の差で数える")

    def test_old_themes_fade_and_disappear(self):
        self.put("2026-W40", [("結婚式記念", 16.0)])
        self.m.board()
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-26"          # W44
        self.put("2026-W44", [("ハロウィン", 8.0)])
        ts = {t["label"]: t for t in self.m.board()["segments"][0]["themes"]}
        self.assertEqual(ts["結婚式記念"]["decayed"], 8.0)
        self.assertEqual(ts["結婚式記念"]["age_weeks"], 4)
        os.environ["NEWPRODUCT_TODAY"] = "2026-11-23"          # W48
        labels = [t["label"] for t in self.m.board()["segments"][0]["themes"]]
        self.assertNotIn("結婚式記念", labels, "8週たったら出さない")

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


if __name__ == "__main__":
    unittest.main()
