#!/usr/bin/env python3
"""やること（ガイド・ADR-133）: 見る人で出し分け・事前の準備の済／未・週次の取り込み・済にする・やり方の節が実在。"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


class Guide(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project, guide
        seed.run()
        self.store, self.g = store, guide
        for role, uid in (("president", "shacho"), ("admin", "kanri"), ("prod", "seisan")):
            store.ex("INSERT INTO role_member (role_code,user_id,granted_at) VALUES (?,?,?)", (role, uid, store.now_s()))
        store.conn().commit()
        self.pid = project.create("u", expand=False, internal_name="検査の商品", flow_type="freecut",
                                  launch_date=(store.today() + dt.timedelta(days=10)).isoformat())["id"]
        store.ex("UPDATE project SET stage='開発中' WHERE id=?", (self.pid,))
        store.conn().commit()

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def keys(self, uid, group=None):
        b = self.g.build(uid)
        return {x["key"] for x in (b[group] if group else b["now"] + b["prep"] + b["soon"])}

    def test_shown_by_viewer(self):
        k_admin, k_prod, k_pres = self.keys("kanri"), self.keys("seisan"), self.keys("shacho")
        self.assertIn(f"cip_{self.pid}", k_admin)                 # 発売10日前で CIP 未登録 → 管理者
        self.assertNotIn(f"cip_{self.pid}", k_prod)               # 生産部には出さない
        self.assertIn(f"cip_{self.pid}", k_pres)                  # 社長は全部
        self.assertIn(f"launch_{self.pid}", k_prod)               # 発売の予定は全員
        self.assertNotIn("prep_cutover", k_prod)

    def test_weekly_report_due_on_monday(self):
        from app import newprod
        mon = dt.date(2026, 11, 9)                                # 月曜
        with mock.patch.object(self.store, "today", return_value=mon):
            b = self.g.build("kanri")
            w = [x for x in b["now"] if x["key"].startswith("weekly_")]
            self.assertEqual((w[0]["key"], w[0]["state"]), ("weekly_2026-11-05", "今やる"))
        with mock.patch.object(self.store, "today", return_value=dt.date(2026, 11, 12)):   # 木曜まで取り込んでいない
            w = [x for x in self.g.build("kanri")["now"] if x["key"].startswith("weekly_")]
            self.assertEqual(w[0]["state"], "期限切れ")
        with self.store.tx() as c:
            c.execute("INSERT INTO newprod_weekly (period_from,period_to,rows,monthly,received_at) VALUES "
                      "('2026-10-23','2026-11-05','{\"rows\":[],\"total\":null}','{}','x')")
        with mock.patch.object(self.store, "today", return_value=mon):
            self.assertFalse([x for x in self.g.build("kanri")["now"] if x["key"].startswith("weekly_")])
        self.assertIsNotNone(newprod.latest())

    def test_prep_turns_done_by_itself_and_by_mark(self):
        prep = {x["key"]: x for x in self.g.build("kanri")["prep"]}
        self.assertEqual(prep["prep_routine"]["state"], "未")
        self.store.ex("INSERT INTO routine (title,cadence,active) VALUES ('x','weekly',1)")
        self.store.conn().commit()
        prep = {x["key"]: x for x in self.g.build("kanri")["prep"]}
        self.assertEqual(prep["prep_routine"]["state"], "済")
        with self.assertRaises(PermissionError):
            self.g.mark("seisan", "prep_cutover", True)
        self.g.mark("kanri", "prep_cutover", True)
        prep = {x["key"]: x for x in self.g.build("kanri")["prep"]}
        self.assertEqual((prep["prep_cutover"]["state"], prep["prep_cutover"]["done_by"]), ("済", "kanri"))
        self.g.mark("kanri", "prep_cutover", False)
        self.assertEqual({x["key"]: x for x in self.g.build("kanri")["prep"]}["prep_cutover"]["state"], "未")

    def test_one_failing_check_does_not_hide_the_rest(self):
        def boom(*a):
            raise RuntimeError("x")
        with mock.patch.object(self.g, "CHECKS", (boom,) + self.g.CHECKS):
            b = self.g.build("kanri")
        self.assertEqual(b["failed"], ["boom: RuntimeError"])
        self.assertTrue(b["now"])

    def test_every_how_is_a_real_manual_section(self):
        """やり方の節が欠けると、押しても使い方のどこにも飛ばない（Business Analysis の ADR-051 と同じ検査）"""
        text = (BASE / "docs" / "操作マニュアル.md").read_text(encoding="utf-8")
        heads = [h.strip() for h in re.findall(r"^#{2,3} (.+)$", text, re.M)]
        src = (BASE / "app" / "guide.py").read_text(encoding="utf-8")
        hows = set(re.findall(r'"how": "([^"]+)"', src)) | set(re.findall(r'^\s+f?"[^"]*", "([^"]+)", "#/', src, re.M))
        hows |= {x["how"] for x in self.g.build("shacho")["now"] + self.g.build("shacho")["prep"]}
        for h in hows:
            self.assertTrue(any(x.startswith(h) for x in heads), h)


class Bulletin(Guide):
    """朝の便（docs/contracts/guide_v1.md）: 期限切れと今やるだけ・7日より先は載せない・並び・URL"""

    def test_bulletin_items(self):
        later = (self.store.today() + dt.timedelta(days=20)).isoformat()
        p2 = __import__("app.project", fromlist=["x"]).create("u", expand=False, internal_name="先の商品", flow_type="freecut",
                                                             launch_date=later)["id"]
        self.store.ex("UPDATE project SET stage='開発中' WHERE id=?", (p2,))
        self.store.conn().commit()
        soon = (self.store.today() + dt.timedelta(days=5)).isoformat()
        p3 = __import__("app.project", fromlist=["x"]).create("u", expand=False, internal_name="近い商品", flow_type="freecut",
                                                             launch_date=soon)["id"]
        self.store.ex("UPDATE project SET stage='開発中' WHERE id=?", (p3,))
        self.store.conn().commit()
        d = self.g.todo_for_bulletin("kanri", "https://np.example/")
        keys = [i["key"] for i in d["items"]]
        self.assertIn(f"cip_{p3}", keys)                          # 期限（発売日）が7日以内 → 載せる
        self.assertNotIn(f"cip_{self.pid}", keys)                 # 10日先 → まだ載せない（やること一覧には出る）
        self.assertIn("gate_mine", keys) if "gate_mine" in [x["key"] for x in self.g.build("kanri")["now"]] else None
        self.assertNotIn(f"target_{p2}", keys)                    # 20日先は「これから」なので載せない
        self.assertTrue(all(i["state"] in ("late", "todo") for i in d["items"]))
        self.assertTrue(all(i["url"].startswith("https://np.example/#/") for i in d["items"]))
        self.assertEqual(d["todo_url"], "https://np.example/#/todo")
        st = [i["state"] for i in d["items"]]
        self.assertEqual(st, sorted(st, key=lambda s: s != "late"))
        self.assertNotIn("¥", str(d))
