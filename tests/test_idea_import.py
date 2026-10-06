#!/usr/bin/env python3
"""
アイデアの一括登録（ADR-062）の検査。

  - 見出しの語で列を読む（並びが違っても読める）。商品案名の見出しが無ければ断る
  - 確認は何も保存しない。起票経路が空の行は止める（選べば入れる）。既にある・貼り付けた中で重複・個人情報らしい文字は入れない
  - 登録は確認した札と一致するときだけ。同じ札では二度登録しない。外した行は入れない
  - 取り消しは手が入っていない行だけ消す（採点・編集・枠につながった行は残す）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

TEXT = ("起票経路\t商品案名\t概要\n"
        "社内アイデア\tアクリル名札キット\t名札\n"
        "\t推し色マグ\t空欄の経路\n"
        "internal\tアクリル名札キット\t同じ名前\n"
        "社内アイデア\t既存の案\t—\n"
        "社内アイデア\t連絡先入り\tfoo@example.com に送る\n"
        "社内アイデア\t推しうちわ立て\t立てる\n")


class IdeaImport(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, idea, idea_import
        seed.run()
        self.m, self.store, self.idea = idea_import, store, idea
        with store.tx() as c:
            c.execute("INSERT INTO role_member (role_code,user_id) VALUES ('devdept','dev')")
        idea.create("dev", title="既存の案", origin="internal")

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def n(self):
        return self.store.val("SELECT COUNT(*) FROM idea", (), 0)

    def test_preview_saves_nothing_and_judges_rows(self):
        p = self.m.preview(TEXT)
        self.assertEqual(self.n(), 1, "確認では保存しない")
        st = {x["line"]: x["state"] for x in p["rows"]}
        self.assertEqual(st, {2: "入る", 3: "止まる", 4: "貼り付けた中で重複", 5: "既にある", 6: "止まる", 7: "入る"})
        p2 = self.m.preview(TEXT, default_origin="internal")
        self.assertEqual({x["line"]: x["state"] for x in p2["rows"]}[3], "入る", "選んだ経路を空欄に入れる")
        with self.assertRaises(ValueError):
            self.m.preview("概要\n何か\n")                        # 商品案名の見出しが無い

    def test_register_token_once_exclude_and_undo_untouched_only(self):
        p = self.m.preview(TEXT)
        with self.assertRaises(PermissionError):
            self.m.register(TEXT, "", p["token"], [], "nobody")
        with self.assertRaises(ValueError):
            self.m.register(TEXT, "internal", p["token"], [], "dev")   # 確認した内容と違う
        r = self.m.register(TEXT, "", p["token"], ["7"], "dev")
        self.assertEqual(r["n"], 1, "7行目は外した")
        with self.assertRaises(ValueError):
            self.m.register(TEXT, "", p["token"], [], "dev")           # 同じ札は二度登録しない
        p3 = self.m.preview(TEXT, "internal")
        self.m.register(TEXT, "internal", p3["token"], [], "dev")
        ids = [x["id"] for x in self.store.rows(self.store.q(
            "SELECT id FROM idea WHERE source_sheet=? ORDER BY source_row", ("一括:" + p3["token"],)))]
        self.assertEqual(len(ids), 2)                                   # 3行目（経路を補った）・7行目
        self.idea.update_fields(ids[0], "dev", summary="手を入れた")
        self.store.audit("dev", "idea.fields", ids[0], {"summary": "手を入れた"})   # 画面からの操作は監査に残る
        u = self.m.undo(p3["token"], "dev")
        self.assertEqual((u["removed"], [k["why"] for k in u["kept"]]), (1, ["登録後に直されています"]))
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM idea WHERE id=?", (ids[0],)), 1)


if __name__ == "__main__":
    unittest.main()
