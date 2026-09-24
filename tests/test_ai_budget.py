#!/usr/bin/env python3
"""
AI予算の確認と記録（FR-146）の検査。**外部通信をしない。**

口は Auto GROWTH 側（`http://127.0.0.1:8789/api/ai-usage`）。ここでは
呼び出しを差し替えて、**こちら側の判断**だけを見る。

  - **確かめられないときは使わない**（`unavailable`。「0円だった」と混ぜない）
  - **`newproduct-` で始まらない job を送る前に弾く**
    （実測で `cap 0.0 / remaining null` になり、**全体の枠を引ける**）
  - **枠を使い切ったら使わない**
  - **`request_id` を必ず付ける**（再送で二重に数えない）
  - **記録できなかったら成功扱いにしない**
  - `spent` が**枠の合算**であることを言葉で持つ
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

OK_GET = {"ok": True, "job": "newproduct-image", "scope": "newproduct-*",
          "cap": 5.0, "spent": 0.42, "remaining": 4.58,
          "month_total": 22.57, "month_cap": 50.0}


class Base(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-09-25"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed
        seed.run()
        from app import ai_budget
        self.m = ai_budget
        self.calls = []

    def tearDown(self):
        from app import store
        store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def caller(self, reply):
        def c(method, url, payload):
            self.calls.append((method, url, payload))
            if isinstance(reply, Exception):
                raise reply
            return reply
        return c


class TestJobName(Base):
    def test_a_job_without_the_prefix_is_rejected_before_sending(self):
        """**実測（2026-09-25）**: `newproduct` は cap 0.0 / remaining null で、
        **job 別の上限が効かず全体50.0だけが効く。**送る前に弾く。"""
        with self.assertRaises(self.m.BadJob):
            self.m.check_job("newproduct")
        with self.assertRaises(self.m.BadJob):
            self.m.check_job("image")
        with self.assertRaises(self.m.BadJob):
            self.m.check_job("")
        self.assertEqual(self.m.check_job("newproduct-image"), "newproduct-image")

    def test_check_returns_bad_job_without_calling_out(self):
        r = self.m.check("newproduct", caller=self.caller(OK_GET))
        self.assertEqual(r["state"], "bad_job")
        self.assertFalse(r["usable"])
        self.assertEqual(self.calls, [], "1回も呼んでいないこと")

    def test_record_refuses_a_bad_job(self):
        with self.assertRaises(self.m.BadJob):
            self.m.record("newproduct", "m", 0.1, caller=self.caller(OK_GET))
        self.assertEqual(self.calls, [])

    def test_both_jobs_start_with_the_prefix(self):
        for j in self.m.JOBS:
            self.assertTrue(j.startswith(self.m.JOB_PREFIX), j)


class TestCheck(Base):
    def test_a_reply_that_is_not_a_budget_answer_is_unavailable(self):
        """**同じポートに別のものが居ることがある。**形で確かめる。"""
        r = self.m.check("newproduct-image",
                         caller=self.caller({"error": "ログインしてください"}))
        self.assertEqual(r["state"], "unavailable")
        self.assertFalse(r["usable"])
        self.assertIn("別のものが同じポートに居る", r["why"])

    def test_ok(self):
        r = self.m.check("newproduct-image", caller=self.caller(OK_GET))
        self.assertEqual(r["state"], "ok")
        self.assertTrue(r["usable"])
        self.assertEqual(r["remaining"], 4.58)
        self.assertEqual(self.calls[0][0], "GET")
        self.assertIn("job=newproduct-image", self.calls[0][1])

    def test_unreachable_means_do_not_use(self):
        """**「使えない」と「使っていない」を混ぜない。**黙って使うほうが危ない。"""
        r = self.m.check("newproduct-image",
                         caller=self.caller(OSError("connection refused")))
        self.assertEqual(r["state"], "unavailable")
        self.assertFalse(r["usable"])
        self.assertIn("確かめられないので使いません", r["why"])

    def test_no_remaining_is_over_cap(self):
        d = dict(OK_GET, spent=5.0, remaining=0.0)
        r = self.m.check("newproduct-image", caller=self.caller(d))
        self.assertEqual(r["state"], "over_cap")
        self.assertFalse(r["usable"])

    def test_a_job_with_no_cap_is_not_usable(self):
        """枠に入っていない job（`remaining: null`）で使わせない。"""
        d = {"ok": True, "job": "newproduct-x", "scope": "", "cap": 0.0,
             "spent": 0.0, "remaining": None}
        r = self.m.check("newproduct-x", caller=self.caller(d))
        self.assertEqual(r["state"], "no_cap")
        self.assertFalse(r["usable"])

    def test_the_amount_is_labelled_as_the_shared_scope(self):
        """**`spent` は枠の合算。**「image の使用額」と書かない（Auto GROWTH の申し送り）。"""
        r = self.m.check("newproduct-image", caller=self.caller(OK_GET))
        self.assertIn("newproduct-*", r["spent_label"])
        self.assertIn("合算", r["spent_label"] + r["note"])
        self.assertIn("分け合います", r["note"])


class TestRecord(Base):
    def test_request_id_is_always_sent(self):
        """**付けないと、タイムアウト後の再送で二重に数えられる。**"""
        ok = {"ok": True, "counted": True, "recorded_usd": 0.42,
              "spent": 0.42, "remaining": 4.58, "cap": 5.0}
        r = self.m.record("newproduct-image", "m", 0.42, caller=self.caller(ok))
        payload = self.calls[0][2]
        self.assertTrue(payload["request_id"])
        self.assertEqual(r["request_id"], payload["request_id"])
        self.assertTrue(r["counted"])

    def test_the_same_request_id_is_reported_as_not_counted(self):
        again = {"ok": True, "counted": False, "recorded_usd": 0.42,
                 "spent": 0.42, "remaining": 4.58, "cap": 5.0}
        r = self.m.record("newproduct-image", "m", 0.42, request_id="fixed",
                          caller=self.caller(again))
        self.assertFalse(r["counted"], "再送は数えられていないこと")
        self.assertEqual(r["spent"], 0.42)

    def test_over_cap_raises(self):
        over = {"ok": False, "reason": "over_cap", "cap": 5.0, "spent": 5.0,
                "remaining": 0.0, "detail": "枠が今月の上限に達しました"}
        with self.assertRaises(self.m.OverCap):
            self.m.record("newproduct-image", "m", 0.1, caller=self.caller(over))

    def test_unreachable_raises_instead_of_looking_successful(self):
        """**記録できなかったら成功扱いにしない。**"""
        with self.assertRaises(self.m.BudgetUnavailable):
            self.m.record("newproduct-image", "m", 0.1,
                          caller=self.caller(OSError("boom")))

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            self.m.record("newproduct-image", "m", -1, caller=self.caller({}))


class TestScoringGate(Base):
    def test_scoring_stays_off_when_the_budget_cannot_be_checked(self):
        """設定を on にしても、**枠を確かめられなければ採点しない。**"""
        from app import ai_score, store
        store.ex("UPDATE setting SET value='1' WHERE key='ai_scoring_enabled'")
        store.conn().commit()
        # **口を検査用のどこにも居ない先へ向ける。**手元の 8789 に別のものが
        # 居ることがあるので、実在しないポートを指す（2026-09-25 に実際そうだった）
        store.ex("UPDATE setting SET value='http://127.0.0.1:1/api/ai-usage' "
                 "WHERE key=?", (self.m.ENDPOINT_SETTING,))
        store.conn().commit()
        st = ai_score.status()
        self.assertTrue(st["setting_on"])
        self.assertFalse(st["enabled"], "枠を確かめられないなら採点しない")
        self.assertIn(st["budget"]["state"], ("unavailable", "refused"))
        self.assertTrue(st["reason"])

    def test_the_scorer_itself_is_still_absent(self):
        """**予算が付いた＝採点できる、ではない。**モデルを呼ぶ実装はまだ無い。"""
        from app import ai_score
        self.assertFalse(ai_score.status()["scorer_implemented"])

    def test_the_scoring_job_starts_with_the_prefix(self):
        from app import ai_score
        self.assertTrue(ai_score.JOB.startswith(self.m.JOB_PREFIX))


class TestNoOutboundImports(Base):
    def test_ai_score_does_not_import_the_network(self):
        """`ai_score.py` は通信しない。**呼ぶのは差し替えた Scorer の仕事。**"""
        src = (BASE / "app" / "ai_score.py").read_text(encoding="utf-8")
        for bad in ("import urllib", "import http", "import socket", "import requests"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
