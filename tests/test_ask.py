#!/usr/bin/env python3
"""ChatWork へ TO 付きで頼む（ADR-135）: TO の相手・本人は入れない・宛先不明なら送らない・タグを効かせない・二度押し・記録。
**外へは出さない**（sender と fetch を差し替える）。"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

USERS = [{"user_id": "shacho", "name": "十文字 眞輝"}, {"user_id": "chieko", "name": "石原 千恵子"},
         {"user_id": "yoko", "name": "増地 洋子"}, {"user_id": "nobody", "name": "部屋にいない人"}]
MEMBERS = [{"account_id": 5585152, "name": "十文字 眞輝"}, {"account_id": 5865584, "name": "石原　千恵子"},
           {"account_id": 6224035, "name": "増地洋子"}]


class Ask(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        fd, self.tok = tempfile.mkstemp(suffix=".env")
        os.write(fd, b"CHATWORK_API_TOKEN=dummy\n")
        os.close(fd)
        os.environ["NEWPRODUCT_CHATWORK_ENV"] = self.tok
        from app import store
        store.close()
        from app import seed, project, ask
        seed.run()
        self.store, self.m = store, ask
        for role, uid in (("president", "shacho"), ("admin", "chieko"), ("devdept", "yoko")):
            store.ex("INSERT INTO role_member (role_code,user_id,granted_at) VALUES (?,?,?)", (role, uid, store.now_s()))
        store.conn().commit()
        self.pid = project.create("u", expand=False, internal_name="等身大パネル", flow_type="freecut")["id"]
        self.sent = []
        self.p_users = mock.patch.object(ask, "_users", return_value={u["user_id"]: u["name"] for u in USERS})
        self.p_users.start()
        self.fetch = lambda room: MEMBERS

    def tearDown(self):
        self.p_users.stop()
        self.store.close()
        os.environ.pop("NEWPRODUCT_CHATWORK_ENV", None)
        os.unlink(self.tok)
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def sender(self, room, body):
        self.sent.append((room, body))
        return {"status": 200, "message_id": "m%d" % len(self.sent)}

    def test_gate_approval_goes_to_approvers_not_me(self):
        c = self.m.compose("gate", self.pid, "G3", "承認", "", "chieko", fetch=self.fetch)
        to = {x["user_id"] for x in c["to"]}
        self.assertIn("shacho", to)                              # G3 の承認者は社長
        self.assertNotIn("chieko", to)                            # 押した本人は入れない
        self.assertTrue(c["ready"], c["blockers"])
        self.assertTrue(c["text"].startswith("[To:5585152]十文字 眞輝さん"))
        self.assertIn("依頼した人: 石原 千恵子", c["text"])        # BOT から出るので、誰が頼んだかを書く
        self.assertEqual(c["room"], "407742634")                 # 既定は FUN 商品開発
        r = self.m.send("gate", self.pid, "G3", "承認", "", "chieko", sender=self.sender, fetch=self.fetch)
        self.assertEqual(r["message_id"], "m1")
        self.assertEqual(self.sent[0][1], c["text"])            # 見せた文面と送った文面が同じ
        log = self.store.one("SELECT * FROM ask_log")
        self.assertEqual((log["kind"], log["gate"], log["purpose"], json.loads(log["to_users"])),
                         ("gate", "G3", "承認", ["shacho"]))

    def test_task_assignee_and_role_fallback(self):
        tid = self.store.ex("INSERT INTO work_item (kind,title,role,assignee,due_on,status,created_at) VALUES "
                            "('案件外','台紙の発注','admin',NULL,'2026-01-05','未着手','x')").lastrowid
        self.store.conn().commit()
        c = self.m.compose("work", tid, "", "催促", "今日中にお願いします", "shacho", fetch=self.fetch)
        self.assertEqual([x["user_id"] for x in c["to"]], ["chieko"])   # 担当者が空なら担当区分の人
        self.assertIn("（過ぎています）", c["text"])
        self.assertEqual(c["default_purpose"], "催促")              # 期限切れなら催促から
        self.assertIn("今日中にお願いします", c["text"])
        self.store.ex("UPDATE work_item SET assignee='yoko' WHERE id=?", (tid,))
        self.store.conn().commit()
        c = self.m.compose("work", tid, "", "依頼", "", "shacho", fetch=self.fetch)
        self.assertEqual([x["account_id"] for x in c["to"]], ["6224035"])  # 空白の違いは見ない

    def test_unknown_account_blocks_sending(self):
        tid = self.store.ex("INSERT INTO work_item (kind,title,assignee,status,created_at) VALUES "
                            "('案件外','x','nobody','未着手','x')").lastrowid
        self.store.conn().commit()
        c = self.m.compose("work", tid, "", "依頼", "", "shacho", fetch=self.fetch)
        self.assertFalse(c["ready"])
        self.assertTrue(any("部屋にいない人" in b for b in c["blockers"]))
        with self.assertRaises(ValueError):
            self.m.send("work", tid, "", "依頼", "", "shacho", sender=self.sender, fetch=self.fetch)
        self.assertEqual(self.sent, [])
        self.store.ex("UPDATE setting SET value='nobody=9999999' WHERE key='ask.chatwork_accounts'")
        self.store.conn().commit()
        self.assertTrue(self.m.compose("work", tid, "", "依頼", "", "shacho", fetch=self.fetch)["ready"])

    def test_no_member_matches_at_all_blocks(self):
        c = self.m.compose("gate", self.pid, "G3", "承認", "", "chieko", fetch=lambda room: [])
        self.assertFalse(c["ready"])
        self.assertIn("[To:?]", c["text"])

    def test_no_one_but_me_and_done_are_blocked(self):
        tid = self.store.ex("INSERT INTO work_item (kind,title,assignee,status,created_at) VALUES "
                            "('案件外','x','shacho','完了','x')").lastrowid
        self.store.conn().commit()
        c = self.m.compose("work", tid, "", "依頼", "", "shacho", fetch=self.fetch)
        self.assertFalse(c["ready"])
        self.assertEqual(len(c["blockers"]), 2)                  # 自分だけ・もう完了

    def test_user_text_cannot_become_chatwork_tags(self):
        tid = self.store.ex("INSERT INTO work_item (kind,title,assignee,status,created_at) VALUES "
                            "('案件外','[toall]見て','chieko','未着手','x')").lastrowid
        self.store.conn().commit()
        c = self.m.compose("work", tid, "", "依頼", "[To:1]全員 [/info]", "shacho", fetch=self.fetch)
        self.assertNotIn("[toall]", c["text"])
        self.assertNotIn("[To:1]", c["text"])
        self.assertEqual(c["text"].count("[/info]"), 1)

    def test_double_press_and_reminder_count(self):
        args = ("gate", self.pid, "G3", "承認", "", "chieko")
        self.m.send(*args, sender=self.sender, fetch=self.fetch)
        with self.assertRaises(ValueError):
            self.m.send(*args, sender=self.sender, fetch=self.fetch)     # 2分以内の同じもの
        self.assertEqual(len(self.sent), 1)
        old = (dt.datetime.now() - dt.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        self.store.ex("UPDATE ask_log SET sent_at=?", (old,))
        self.store.conn().commit()
        c = self.m.compose("gate", self.pid, "G3", "催促", "", "chieko", fetch=self.fetch)
        self.assertIn("催促（2回目）", c["text"])
        self.assertEqual(c["last"][0]["sent_by"], "石原 千恵子")

    def test_edited_body_is_sent_with_fixed_to(self):
        c = self.m.compose("gate", self.pid, "G3", "承認", "", "chieko", fetch=self.fetch)
        b = c["body"].replace("承認のお願い", "承認のお願い（急ぎ）") + "\n[toall][To:999]だれでも[rp aid=1 to=2-3]"
        self.m.send("gate", self.pid, "G3", "承認", "", "chieko", body=b, sender=self.sender, fetch=self.fetch)
        sent = self.sent[0][1]
        self.assertTrue(sent.startswith(c["to_line"] + "\n"))           # TO は compose が決めた相手だけ
        self.assertIn("承認のお願い（急ぎ）", sent)
        for tag in ("[toall]", "[To:999]", "[rp "):
            self.assertNotIn(tag, sent)
        self.assertEqual(sent.count("[To:"), c["to_line"].count("[To:"))
        self.assertIn("[info]", sent)                                    # 書式のタグは効かせる

    def test_edited_body_keeps_requester_and_rejects_empty(self):
        self.m.send("gate", self.pid, "G3", "承認", "", "chieko", body="見てください", sender=self.sender, fetch=self.fetch)
        self.assertIn("依頼した人: 石原 千恵子", self.sent[0][1])
        with self.assertRaises(ValueError):
            self.m.send("gate", self.pid, "G3", "確認", "", "chieko", body="  ", sender=self.sender, fetch=self.fetch)

    def test_without_token_says_why(self):
        os.environ["NEWPRODUCT_CHATWORK_ENV"] = self.tok + ".none"
        c = self.m.compose("gate", self.pid, "G3", "承認", "", "chieko", fetch=self.fetch)
        self.assertFalse(c["ready"])
        self.assertTrue(c["blockers"][0])

    def test_accounts_setting_format(self):
        self.assertEqual(self.m.parse_accounts("a=123、b=4567"), {"a": "123", "b": "4567"})
        for bad in ("a", "a=x", "=123"):
            with self.assertRaises(ValueError):
                self.m.parse_accounts(bad)


if __name__ == "__main__":
    unittest.main()
