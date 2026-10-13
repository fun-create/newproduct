#!/usr/bin/env python3
"""
ログインを保つ長さ（2026-10-13 全アプリをカレンダーと同じに・HUB decisions/2026-10.md）。

  - NEW PRODUCT の既定は「ログアウトするまで」（server.py で上書き）。期限なしのセッションは掃かれず、再起動で読み直しても残る
  - 利用者が短くすると、いま生きているセッションにもすぐ効く（操作しないままその時間がたつと切れる）
  - 一覧に無い値（任意の秒数）は受けない
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class SessionKeep(unittest.TestCase):
    def setUp(self):
        import auth
        self.auth = auth
        self.tmp = tempfile.mkdtemp()
        import server  # noqa: F401  server.py が既定を上書きする（読み込みは1回だけなので、既定は戻さない）
        self.saved = {k: getattr(auth, k) for k in ("USERS_PATH", "SESSIONS_PATH")}
        auth.USERS_PATH = Path(self.tmp) / "users.json"
        auth.SESSIONS_PATH = Path(self.tmp) / ".sessions.json"
        auth._SESSIONS.clear()
        self.assertEqual(auth.SESSION_DEFAULT, "keep", "NEW PRODUCT の既定は「ログアウトするまで」")
        # パスワードは使わないので、台帳に1人だけ直接書く（手元の Python に scrypt が無いことがある）
        import json
        auth.USERS_PATH.write_text(json.dumps({"users": [{"user_id": "tester", "name": "検査", "role": "user"}]}), encoding="utf-8")
        self.assertTrue(auth.get("tester"), "台帳を読めること")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(self.auth, k, v)
        self.auth._SESSIONS.clear()

    def _login(self):
        h = "h" + os.urandom(4).hex()
        a = self.auth
        ttl = a.SESSION_CHOICES[a.session_keep_of(a.get("tester") or {})]
        a._SESSIONS[h] = {"h": h, "user_id": "tester", "ttl": ttl, "expires": 0 if ttl == 0 else time.time() + ttl,
                          "ip": "", "created": time.time()}
        return h

    def test_default_keep_has_no_expiry(self):
        a = self.auth
        self.assertEqual(a.session_keep_of(a.get("tester")), "keep")
        h = self._login()
        self.assertEqual(a._SESSIONS[h]["expires"], 0, "期限なし")
        a._persist()
        a._SESSIONS.clear()
        a._load_sessions()
        self.assertIn(h, a._SESSIONS, "期限なしのセッションは再起動で読み直しても残る（0 を期限切れと読まない）")

    def test_shorten_applies_to_live_session(self):
        a = self.auth
        h = self._login()
        rec, err = a.set_session_keep("tester", "12h")
        self.assertEqual((rec["session_keep"], err), ("12h", ""))
        self.assertGreater(a._SESSIONS[h]["expires"], time.time() + 11 * 3600, "いまのログインにもすぐ効く")
        self.assertEqual(a._SESSIONS[h]["ttl"], 12 * 3600)

    def test_arbitrary_seconds_rejected(self):
        rec, err = self.auth.set_session_keep("tester", "999999")
        self.assertIsNone(rec)
        self.assertIn("不正", err)


if __name__ == "__main__":
    unittest.main()
