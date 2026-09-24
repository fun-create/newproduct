#!/usr/bin/env python3
"""
自動化依頼（F-15 ／ FR-159〜）の検査。**外部通信をしない。**

2026-09-24 十文字さん指示（商品開発 増地さんの15件）。見ているのは、
**この機能が「要件が揃う前に実装へ渡さない」ための道具として働くか**。

  - **答えが揃う前に `要件確定` 以降へ進めない**こと（これが機能の芯）
  - **未回答と「無いという答え」を区別する**こと（空欄を『無し』と読み替えない・N-10）
  - **原文を書き換えない**こと
  - 標準タスクに当たらない11件を **`0` ではなく数として**持つこと
  - 効果は**答えから数が取れたときだけ**出すこと（文章のままなら「未計測」）
  - 要件の書き出しに、未回答が**未回答として**出ること
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Base(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        os.environ["NEWPRODUCT_TODAY"] = "2026-09-24"
        self.dbfile = p
        from app import store
        store.close()
        from app import seed
        seed.run()
        from app import automation
        self.m = automation
        spec = importlib.util.spec_from_file_location(
            "import_automation", BASE / "tools" / "import_automation.py")
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def tearDown(self):
        from app import store
        store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def one(self, **f):
        f.setdefault("title", "検査用の作業")
        return self.m.create("tester", **f)["id"]

    def answer_all(self, rid, skip=()):
        for q in self.m.QUESTIONS:
            if not q[3] or q[0] in skip:
                continue
            self.m.answer(rid, q[0], "こう答えました", "tester")


class TestIntake(Base):
    def test_a_bare_title_is_enough_to_file_a_request(self):
        """**作業名だけで出せる。**重くすると誰も出さない（F-1-3 と同じ考え）。"""
        rid = self.one(title="治具作成")
        d = self.m.detail(rid)
        self.assertEqual(d["request"]["stage"], "起票")
        self.assertEqual(d["progress"]["answered"], 0)
        self.assertFalse(d["progress"]["ready"])

    def test_raw_request_is_not_rewritten(self):
        raw = "スタンプ登録\n→元となる画像を作成したらあとは各サイズ作ってくれて登録もしてほしい"
        rid = self.one(title="スタンプ登録", raw_request=raw)
        self.assertEqual(self.m.detail(rid)["request"]["raw_request"], raw)

    def test_the_same_work_is_not_filed_twice(self):
        self.one(title="背景登録")
        with self.assertRaises(ValueError):
            self.one(title="背景登録")


class TestQuestions(Base):
    def test_unanswered_and_answered_nothing_are_different(self):
        """**空欄を『無し』と読み替えない。**聞いた結果の「無い」は情報。"""
        rid = self.one()
        self.m.answer(rid, "judgement", "", "tester")      # 聞いて「無い」
        q = {x["key"]: x for x in self.m.detail(rid)["questions"]}
        self.assertEqual(q["judgement"]["state"], "無しと回答")
        self.assertEqual(q["input"]["state"], "未回答")
        # **「無い」も答えたことに数える**
        self.assertNotIn("judgement", self.m.progress(rid)["missing"])
        self.assertIn("judgement", self.m.progress(rid)["blank"])

    def test_answering_moves_it_out_of_起票_but_not_to_要件確定(self):
        rid = self.one()
        self.m.answer(rid, "how_now", "いまはこうしています", "tester")
        self.assertEqual(self.m.detail(rid)["request"]["stage"], "質問中")

    def test_unknown_question_is_rejected(self):
        rid = self.one()
        with self.assertRaises(ValueError):
            self.m.answer(rid, "でっちあげ", "x", "tester")

    def test_every_required_question_says_why_it_is_asked(self):
        """**理由の無い質問を置かない。**答える側が埋めるだけになる。"""
        for q in self.m.questions():
            self.assertTrue(q["why"].strip(), q["key"])
            self.assertTrue(q["text"].strip(), q["key"])


class TestGate(Base):
    def test_cannot_hand_over_before_the_answers_are_in(self):
        """**この機能の芯。**揃わないまま渡すと、作る側が想像で埋める。"""
        rid = self.one()
        self.answer_all(rid, skip=("done_check",))
        for stage in ("要件確定", "実装待ち", "実装中", "実装済"):
            with self.assertRaises(ValueError, msg=stage):
                self.m.set_stage(rid, stage, "tester")
        # 残り1問に答えれば通る
        self.m.answer(rid, "done_check", "一覧に3サイズ並んでいること", "tester")
        self.assertEqual(self.m.set_stage(rid, "要件確定", "tester")["stage"], "要件確定")

    def test_見送り_needs_no_answers(self):
        """**やらないと決めるのに、答えは要らない。**"""
        rid = self.one()
        self.assertEqual(self.m.set_stage(rid, "見送り", "tester")["stage"], "見送り")

    def test_unknown_stage_is_rejected(self):
        rid = self.one()
        with self.assertRaises(ValueError):
            self.m.set_stage(rid, "だいたい終わり", "tester")


class TestEffort(Base):
    def test_unmeasured_until_numbers_are_given(self):
        """**文章のままでは数えない**（N-10）。「月20回くらい」は数ではない。"""
        rid = self.one()
        self.m.answer(rid, "freq", "月20回・1回30分くらい", "tester")
        e = self.m.effort(rid)
        self.assertIsNone(e["hours_per_month"])
        self.assertEqual(e["state"], "未計測")
        self.assertIn("文章のままでは数えられません", e["why"])

    def test_hours_are_computed_from_minutes_and_times(self):
        rid = self.one()
        self.m.set_effort(rid, 30, 20, "tester")
        e = self.m.effort(rid)
        self.assertEqual(e["hours_per_month"], 10.0)
        self.assertEqual(e["per_year"], 120.0)

    def test_listing_says_how_many_are_unmeasured(self):
        """**合計だけ見せない。**全部の合計に見える。"""
        a = self.one(title="A")
        self.one(title="B")
        self.m.set_effort(a, 30, 20, "tester")
        d = self.m.listing()
        self.assertEqual(d["hours_per_month"], 10.0)
        self.assertEqual(d["hours_measured_n"], 1)
        self.assertIn("1 / 2 件", d["hours_note"])


class TestMasudaList(Base):
    def test_the_15_items_load_and_11_are_not_in_the_template(self):
        """**標準タスクに無い11件を、`0` ではなく数として持つ。**"""
        self.tool.apply()
        d = self.m.listing()
        self.assertEqual(d["total"], 15)
        self.assertEqual(d["not_in_template"], 11)
        self.assertIn("11 件は標準タスク178行のどれにも当たりません",
                      d["template_note"])

    def test_loading_twice_does_not_duplicate(self):
        self.tool.apply()
        n = self.tool.apply()
        self.assertEqual(n["created"], 0)
        self.assertEqual(n["kept"], 15)
        self.assertEqual(self.m.listing()["total"], 15)

    def test_the_wish_lines_are_kept(self):
        """増地さんが「→」で書いた**どうなってほしいか**を落とさない。"""
        from app import store
        self.tool.apply()
        raw = store.val("SELECT raw_request FROM automation_request "
                        "WHERE title='スタンプ登録'")
        self.assertIn("→元となる画像を作成したら", raw)
        raw2 = store.val("SELECT raw_request FROM automation_request "
                         "WHERE title='季節ごとにスタンプのカテゴリを修正'")
        self.assertIn("通知が来るようになっていますが、変更までしてほしい", raw2)


class TestRequirementText(Base):
    def test_unanswered_questions_are_shown_as_unanswered(self):
        """**書き出しで穴を隠さない。**渡された側が、足りないことに気づけること。"""
        rid = self.one(title="スタンプ登録", raw_request="元画像から各サイズ")
        self.m.answer(rid, "input", "元となる画像1枚", "tester")
        t = self.m.requirement_text(rid)
        self.assertIn("元となる画像1枚", t)
        self.assertIn("**未回答。**", t)
        self.assertIn("元画像から各サイズ", t)
        self.assertIn("**未計測**", t)
        self.assertIn("178行のどれにも当たりません", t)


# ══════════════════════════════════════════════════════════
class TestChatWorkHandoff(Base):
    """ChatWork へ渡す（F-15-6 ／ 2026-09-24 十文字さんの選択C）。

    **外へは1バイトも出さない。**送信は差し替えた関数で受ける。
    """

    def setUp(self):
        super().setUp()
        self.sent = []
        self.fake = lambda room, body: (self.sent.append((room, body))
                                        or {"status": 200, "message_id": "m1"})

    def _ready(self, room="99", token=True):
        from app import store
        import tempfile as tf
        store.ex("UPDATE setting SET value=? WHERE key=?", (room, self.m.ROOM_SETTING))
        store.conn().commit()
        if token:
            fd, p = tf.mkstemp(suffix=".env")
            os.close(fd)
            pathlib.Path(p).write_text("CHATWORK_API_TOKEN=dummy\n", encoding="utf-8")
            os.environ["NEWPRODUCT_CHATWORK_ENV"] = p
            self.tokenfile = p
        else:
            os.environ["NEWPRODUCT_CHATWORK_ENV"] = "/nonexistent/chatwork.env"

    def tearDown(self):
        os.environ.pop("NEWPRODUCT_CHATWORK_ENV", None)
        f = getattr(self, "tokenfile", None)
        if f:
            try:
                os.unlink(f)
            except OSError:
                pass
        super().tearDown()

    def test_the_room_is_unset_out_of_the_box(self):
        """**送り先を種データで決めない。**決めるのは人。"""
        rid = self.one()
        st = self.m.chatwork_status(rid)
        self.assertFalse(st["ready"])
        self.assertTrue(any("部屋" in (b or "") for b in st["blockers"]))

    def test_missing_token_says_so_instead_of_failing_silently(self):
        """**黙って落ちない**（N-10）。何が足りないかを言葉で返す。"""
        self._ready(token=False)
        rid = self.one()
        st = self.m.chatwork_status(rid)
        self.assertFalse(st["ready"])
        self.assertIn("chatwork.env", " ".join(b or "" for b in st["blockers"]))
        self.assertIn("他のアプリのトークンを写さないでください",
                      " ".join(b or "" for b in st["blockers"]))

    def test_cannot_send_before_the_answers_are_in(self):
        self._ready()
        rid = self.one()
        with self.assertRaises(ValueError):
            self.m.chatwork_send(rid, "tester", sender=self.fake)
        self.assertEqual(self.sent, [], "1通も出ていないこと")

    def test_sends_once_and_records_it(self):
        self._ready()
        rid = self.one(title="スタンプ登録")
        self.answer_all(rid)
        r = self.m.chatwork_send(rid, "tester", sender=self.fake)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "99")
        self.assertEqual(r["message_id"], "m1")
        d = self.m.detail(rid)["request"]
        self.assertEqual(d["sent_count"], 1)
        self.assertEqual(d["sent_room"], "99")
        self.assertIn("ChatWork", d["handoff_to"])

    def test_pressing_twice_does_not_post_twice(self):
        """**ChatWork は取り消せない。**二度押しを DB の記録で止める。"""
        self._ready()
        rid = self.one()
        self.answer_all(rid)
        self.m.chatwork_send(rid, "tester", sender=self.fake)
        with self.assertRaises(ValueError) as e:
            self.m.chatwork_send(rid, "tester", sender=self.fake)
        self.assertIn("既に送っています", str(e.exception))
        self.assertEqual(len(self.sent), 1)
        # 明示的に再送を選べば通る
        self.m.chatwork_send(rid, "tester", sender=self.fake, allow_resend=True)
        self.assertEqual(len(self.sent), 2)

    def test_the_body_uses_chatwork_notation_not_markdown(self):
        """**Markdown は効かない。**`**` をそのまま送るとアスタリスクが出る。"""
        self._ready()
        rid = self.one(title="スタンプ登録", raw_request="元画像から各サイズ")
        self.answer_all(rid)
        body = self.m.chatwork_text(rid)
        self.assertTrue(body.startswith("[info][title]"))
        self.assertTrue(body.rstrip().endswith("[/info]"))
        self.assertNotIn("**", body)
        self.assertNotIn("# ", body)
        self.assertIn("元画像から各サイズ", body)

    def test_the_body_says_when_answers_are_missing(self):
        """**穴を隠さない。**渡された側が気づけないと意味がない。"""
        self._ready()
        rid = self.one()
        self.m.answer(rid, "input", "画像1枚", "tester")
        body = self.m.chatwork_text(rid)
        self.assertIn("未回答", body)
        self.assertIn("実装の前に確かめてください", body)

    def test_preview_is_returned_before_sending(self):
        self._ready()
        rid = self.one()
        st = self.m.chatwork_status(rid)
        self.assertEqual(st["preview"], self.m.chatwork_text(rid))
        self.assertIn("取り消せません", st["note"])

    def test_the_token_value_never_appears_in_what_we_return(self):
        """**値を返さない。**在る／無いだけ。"""
        self._ready()
        rid = self.one()
        blob = repr(self.m.chatwork_status(rid))
        self.assertNotIn("dummy", blob)


if __name__ == "__main__":
    unittest.main()
