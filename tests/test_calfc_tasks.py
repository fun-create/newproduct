#!/usr/bin/env python3
"""
カレンダーへタスクを送る（FR-121・ADR-100）の検査。

見ているのは:
  - 送るのは期限と担当の入ったタスクだけ。無いものは件数と理由で数える（黙って落とさない）
  - 担当がカレンダーのメンバーに無い行は送らず、理由を残す
  - 同じ中身は二度送らない。変わったら送り直す。対象から外れたら消す
  - カレンダーの口が無い（403）ときは Unavailable で止まり、覚えない
  - 完了・未完了の受け口（set_done）: 形の違う ext_ref は断る・対象外は変えない・同じ状態なら変えない
  - 担当の入力は台帳にいる利用者だけ
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class FakeCal:
    def __init__(self, members):
        self.members = members
        self.calls = []
        self.deny = False

    def __call__(self, req, timeout=0):
        path = req.full_url.split("/api", 1)[1]
        body = json.loads(req.data.decode()) if req.data else None
        self.calls.append((req.get_method(), "/api" + path, body, req.get_header("X-actor")))
        if self.deny:
            raise urllib.error.HTTPError(req.full_url, 403, "x", {}, io.BytesIO('{"error":"この呼び出しは許可されていません"}'.encode()))
        if path == "/svc/members":
            out = {"members": [{"member_id": m, "user_id": u} for u, m in self.members.items()]}
        elif path == "/svc/task/upsert":
            out = {"ok": True, "task_id": "cal-" + body["ext_ref"].split(":")[-1], "created": True}
        else:
            out = {"ok": True, "deleted": True}
        return io.BytesIO(json.dumps(out).encode())


class CalfcTasks(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_CALFC_TASKS_OFF"] = "1"
        self.tokdir = tempfile.mkdtemp()
        tok = Path(self.tokdir) / "calfc_token"
        tok.write_text("t\n", encoding="utf-8")
        os.environ["NEWPRODUCT_CALFC_TOKEN"] = str(tok)
        self.dbfile = p
        from app import store, calfc
        store.close()
        self._tok_orig = calfc.TOKEN_FILE
        calfc.TOKEN_FILE = str(tok)
        from app import seed, project, calfc_tasks
        seed.run()
        self.store, self.m = store, calfc_tasks
        self.pid = project.create("u", expand=False, internal_name="送る検査", flow_type="meire",
                                  launch_date="2026-12-01")["id"]
        with store.tx() as c:
            for seq, title, due, who, st in ((1, "試作", "2026-11-01", "risa", "未着手"), (2, "期限なし", None, "risa", "未着手"),
                                            (3, "担当なし", "2026-11-02", None, "未着手"), (4, "よそ者", "2026-11-03", "nobody", "未着手"),
                                            (5, "済み", "2026-10-01", "risa", "完了")):
                c.execute("INSERT INTO task (project_id,seq,title,due_on,assignee,status,created_at) VALUES (?,?,?,?,?,?,?)",
                          (self.pid, seq, title, due, who, st, store.now_s()))
            c.execute("INSERT INTO work_item (kind,title,due_on,assignee,status,created_at) VALUES ('案件外','FBA納品','2026-11-05','risa','未着手',?)",
                      (store.now_s(),))
        self.cal = FakeCal({"risa": "yamashita", "masateru": "jumonji"})

    def tearDown(self):
        from app import calfc
        calfc.TOKEN_FILE = self._tok_orig          # 他の検査は「鍵が無い」前提（戻さないと順序で落ちる）
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass
        for k in ("NEWPRODUCT_CALFC_TOKEN", "NEWPRODUCT_CALFC_TASKS_OFF"):
            os.environ.pop(k, None)

    def test_only_dated_and_assigned_are_sent_with_reasons_for_the_rest(self):
        r = self.m.sync(opener=self.cal)
        self.assertIsNone(r["error"])
        self.assertEqual(r["sent"], 3, "試作・済み・FBA納品")
        self.assertEqual(r["skipped"], {"期限なし": 1, "担当なし": 1})
        self.assertEqual(list(r["unsendable"].values()), ["担当 nobody がカレンダーのメンバーにいません"])
        ups = [c for c in self.cal.calls if c[1] == "/api/svc/task/upsert"]
        self.assertEqual(ups[0][2]["members"], ["yamashita"], "担当は member_id に引き直して送る")
        self.assertEqual(ups[0][3], "risa", "X-Actor は担当の共通ログインID")
        done = [c for c in ups if c[2]["ext_ref"].endswith(":5")][0][2]
        self.assertEqual(done["status"], "done")
        self.assertIn("送る検査", ups[0][2]["title"])

    def test_same_content_is_not_resent_and_changes_are(self):
        self.m.sync(opener=self.cal)
        n = len(self.cal.calls)
        r = self.m.sync(opener=self.cal)
        self.assertEqual((r["sent"], r["deleted"]), (0, 0))
        self.assertEqual([c[1] for c in self.cal.calls[n:]], ["/api/svc/members"], "変わっていなければ members 以外を呼ばない")
        self.store.ex("UPDATE task SET due_on='2026-11-09' WHERE seq=1")
        self.store.conn().commit()
        r = self.m.sync(opener=self.cal)
        self.assertEqual(r["sent"], 1)

    def test_removed_from_scope_is_deleted(self):
        self.m.sync(opener=self.cal)
        self.store.ex("UPDATE task SET status='対象外' WHERE seq=1")
        self.store.ex("UPDATE work_item SET assignee=NULL")
        self.store.conn().commit()
        r = self.m.sync(opener=self.cal)
        self.assertEqual(r["deleted"], 2)
        dels = sorted(c[2]["ext_ref"] for c in self.cal.calls if c[1] == "/api/svc/task/delete")
        self.assertEqual(dels, ["newproduct:task:1", "newproduct:work:1"])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM calfc_task_sent WHERE state='送った'"), 1)
        self.assertEqual(self.m.sync(opener=self.cal)["deleted"], 0, "二度消さない")

    def test_no_route_stops_without_remembering(self):
        self.cal.deny = True
        r = self.m.sync(opener=self.cal)
        self.assertIn("許可されていません", r["error"])
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM calfc_task_sent"), 0)
        st = self.m.status()
        self.assertTrue(st["configured"])
        self.assertEqual(st["target"], 4, "期限と担当のある4件（カレンダーに無い担当の1件も対象には数える）")
        self.assertIn("許可されていません", st["last_sync"]["error"])

    def test_set_done_from_calendar(self):
        r = self.m.set_done("newproduct:task:1", True, "jumonji")
        self.assertEqual((r["status"], r["changed"]), ("完了", True))
        self.assertEqual(self.store.val("SELECT status FROM task WHERE seq=1"), "完了")
        self.assertFalse(self.m.set_done("newproduct:task:1", True, "jumonji")["changed"], "同じ状態なら変えない")
        self.assertEqual(self.m.set_done("newproduct:task:1", False, "jumonji")["status"], "未着手")
        self.assertEqual(self.m.set_done("newproduct:work:1", True, "x")["status"], "完了")
        with self.assertRaises(ValueError):
            self.m.set_done("lpscope:1:2", True, "x")
        with self.assertRaises(LookupError):
            self.m.set_done("newproduct:task:999", True, "x")
        self.store.ex("UPDATE task SET status='対象外' WHERE seq=3")
        self.store.conn().commit()
        with self.assertRaises(ValueError):
            self.m.set_done("newproduct:task:3", True, "x")

    def test_assignee_must_be_a_known_user(self):
        from app import task as task_m
        import auth
        orig = auth.users
        auth.users = lambda: [{"user_id": "risa"}, {"user_id": "masateru"}]
        try:
            r = task_m.set_assignee("task", 3, "masateru", "u")
            self.assertEqual(r["assignee"], "masateru")
            self.assertEqual(task_m.set_assignee("task", 3, "", "u")["assignee"], None)
            with self.assertRaises(ValueError):
                task_m.set_assignee("task", 3, "unknown", "u")
            with self.assertRaises(LookupError):
                task_m.set_assignee("work", 999, "risa", "u")
        finally:
            auth.users = orig


if __name__ == "__main__":
    unittest.main()
