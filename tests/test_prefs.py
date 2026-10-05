#!/usr/bin/env python3
"""
利用者ごとの表示設定（ADR-057）の検査。

見ているのは:
  - 既定は今までの見え方（標準・標準・ダッシュボード・上位3・客層の順・減衰中も出す）
  - **許可リストに無い値は1つでも混ざれば何も変えない**（画面の属性・URL に入るため）
  - 人ごとに分かれる（自分を変えても他の人は変わらない）
  - トレンドは、印を付ける数・並び・減衰中を出すかが効く。既定の呼び方は今までどおり
  - 画面の外枠に、その人の属性が入る（{ROOTATTR} が残らない・auto は data-theme を付けない）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Prefs(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-10-05"            # 2026-W41
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, prefs
        seed.run()
        self.m, self.store = prefs, store

    def tearDown(self):
        self.store.close()
        os.environ.pop("NEWPRODUCT_FCTR", None)
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_defaults_are_the_old_look(self):
        self.assertEqual(self.m.get("u"), {"font": "normal", "density": "normal", "start": "#/",
                                           "trend_top": "3", "trend_order": "segment", "trend_faded": "show"})

    def test_bad_value_changes_nothing(self):
        for bad in ({"font": "huge"}, {"start": "javascript:alert(1)"}, {"start": "#/x\"><script>"},
                    {"font": "large", "density": "tight"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.m.save("u", bad)
        self.assertEqual(self.m.get("u")["font"], "normal", "一部だけ通ることもない")
        with self.assertRaises(ValueError):
            self.m.save("u", {"unknown": "x"})

    def test_per_user_and_hand_edited_value_falls_back(self):
        self.m.save("a", {"font": "large", "start": "#/tasks"})
        self.assertEqual((self.m.get("a")["font"], self.m.get("a")["start"]), ("large", "#/tasks"))
        self.assertEqual(self.m.get("b")["font"], "normal")
        with self.store.tx() as c:
            c.execute("UPDATE user_pref SET value='\"><b>' WHERE user_id='a' AND key='font'")
        self.assertEqual(self.m.get("a")["font"], "normal", "知らない値は既定へ倒す")

    def test_trend_view_options(self):
        from app import fctr
        js = Path(tempfile.mkdtemp()) / "fctr_weekly.json"
        os.environ["NEWPRODUCT_FCTR"] = str(js)

        def th(name, sc, obs="2026-W41"):
            return {"theme": name, "theme_key": name, "market_score": sc, "market_score_max": 25.0,
                    "weeks_present": [obs], "observed_this_week": obs == "2026-W41"}
        js.write_text(json.dumps({"contract_version": 1, "week_id": "2026-W41", "segments": [
            {"segment_id": "a_seg", "site_name": "A", "segment_name": "1", "themes": [th("x", 5.0), th("y", 4.0), th("z", 3.0, "2026-W40")]},
            {"segment_id": "b_seg", "site_name": "B", "segment_name": "2", "themes": [th("w", 9.0)]}]}), encoding="utf-8")
        d = fctr.board()
        self.assertEqual([s["segment"] for s in d["segments"]], ["a_seg", "b_seg"])
        self.assertEqual(sum(t["top"] for t in d["segments"][0]["themes"]), 3)
        d = fctr.board(top_n=1, order="score", faded=False)
        self.assertEqual([s["segment"] for s in d["segments"]], ["b_seg", "a_seg"], "点の高い客層から")
        a = d["segments"][1]["themes"]
        self.assertEqual([t["label"] for t in a], ["x", "y"], "減衰中（W40 が最後）を出さない")
        self.assertEqual([t["top"] for t in a], [True, False])

    def test_root_attrs(self):
        import server
        self.m.save("a", {"font": "xlarge", "density": "compact", "start": "#/sales"})
        got = server._root_attrs({"user_id": "a", "theme": "dark"})
        self.assertEqual(got, ' data-theme="dark" data-np-font="xlarge" data-np-density="compact" data-np-start="#/sales"')
        self.assertNotIn("data-theme", server._root_attrs({"user_id": "a", "theme": "auto"}), "auto は端末に従う")
        self.assertNotIn("data-theme", server._root_attrs({"user_id": "a", "theme": "\"><x"}))
        page = server._page("index.html", USER="u", ROOTATTR=server._root_attrs({"user_id": "a"})).decode()
        self.assertNotIn("{ROOTATTR}", page)
        self.assertIn('data-np-start="#/sales">', page)


if __name__ == "__main__":
    unittest.main()
