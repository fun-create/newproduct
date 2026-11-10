#!/usr/bin/env python3
"""新商品の売上（週次レポートから取り込む・修正依頼書 H06 段階1・ADR-131）。"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    """インライン文字列だけの小さな xlsx（検査用）"""
    def col(i):
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s
    b = io.BytesIO()
    z = zipfile.ZipFile(b, "w")
    names = list(sheets)
    z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
               + "".join(f'<sheet name="{escape(n)}" sheetId="{i+1}" r:id="rId{i+1}"/>' for i, n in enumerate(names))
               + "</sheets></workbook>")
    z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i+1}" Target="worksheets/sheet{i+1}.xml"/>' for i in range(len(names)))
               + "</Relationships>")
    for i, n in enumerate(names):
        rows = []
        for ri, r in enumerate(sheets[n], 1):
            cells = []
            for ci, v in enumerate(r):
                if v is None:
                    continue
                ref = f"{col(ci)}{ri}"
                if isinstance(v, (int, float)):
                    cells.append(f'<c r="{ref}"><v>{v}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>')
            rows.append(f'<row r="{ri}">' + "".join(cells) + "</row>")
        z.writestr(f"xl/worksheets/sheet{i+1}.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   "<sheetData>" + "".join(rows) + "</sheetData></worksheet>")
    z.close()
    return b.getvalue()


HEAD = ["商品ラベル", "件数", "数量", "売上(税込)", "期間目標売上", "達成率(売上)", "期間目標数量", "達成率(数量)"]
SHEET = [HEAD,
         ["ウォールステッカー", 19, 19, 81550, 35645.16, 2.28, 18, 1.0],
         ["目印チャーム【剣道】", 2, 2, 3000, 1000, 3.0, 1, 2.0],
         ["目印チャーム【弓道】", 1, 1, 1000, 1000, 1.0, 1, 1.0],
         ["合計", 22, 22, 85550, 37645.16, 2.27, 20, 1.1],
         [], [],
         ["ウォールステッカー", "1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月(〜11/7)"],
         ["月間目標売上(円)", None, None, None, None, None, None, None, None, 78000, 78000, 78000],
         ["売上金額(円)", None, None, None, None, None, None, None, None, 0, 110750, 29450],
         ["達成率(売上)", None, None, None, None, None, None, None, None, 0, 1.42, 0.38],
         ["月間目標数量(個)", None, None, None, None, None, None, None, None, 20, 20, 20],
         ["販売数量(個)", None, None, None, None, None, None, None, None, 0, 29, 5],
         ["達成率(数量)", None, None, None, None, None, None, None, None, 0, 1.45, 0.25],
         [],
         ["商品ラベル", "店名", "売上日", "大分類", "中分類", "小分類", "商品名", "単価", "数量", "税込合計", "原価合計", "メッセージ"],
         ["ウォールステッカー", "グッズ本店", "2026-11-01", "x", "x", "x", "お客さまの名前入り", 1000, 1, 1000, 0, "個人のメッセージ"]]


class Newprod(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(p)
        os.environ["NEWPRODUCT_DB"] = p
        self.dbfile = p
        from app import store
        store.close()
        from app import seed, project
        seed.run()
        self.store = store
        self.pid = project.create("u", expand=False, internal_name="ウォールステッカー", flow_type="freecut",
                                  launch_date="2026-09-29")["id"]
        store.ex("UPDATE project SET stage='発売済' WHERE id=?", (self.pid,))
        store.ex("INSERT INTO task (project_id,seq,title,status,created_at) VALUES (?,1,'新商品売上レポートに計上するよう設定','未着手',?)",
                 (self.pid, store.now_s()))

    def tearDown(self):
        self.store.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.unlink(self.dbfile + s)
            except OSError:
                pass

    def test_ingest_reads_only_the_summary_not_the_details(self):
        from app import newprod
        r = newprod.ingest("週次売上分析_20261023-20261105.xlsx", xlsx({"サマリー": [["区分"]], "新商品": SHEET}), "u")
        self.assertEqual((r["period_from"], r["period_to"], r["labels"], r["sales"]), ("2026-10-23", "2026-11-05", 3, 85550))
        snap = newprod.latest()
        m = snap["monthly"]["ウォールステッカー"]
        self.assertEqual(m["months"][-1], "2026-11")
        self.assertEqual(m["sales"][-3:], [0, 110750, 29450])
        raw = self.store.one("SELECT rows, monthly FROM newprod_weekly")
        self.assertNotIn("個人のメッセージ", raw["rows"] + raw["monthly"])      # 明細は持たない
        self.assertNotIn("お客さまの名前入り", raw["rows"] + raw["monthly"])
        # 同じ期間を取り込み直しても1件
        newprod.ingest("週次売上分析_20261023-20261105.xlsx", xlsx({"新商品": SHEET}), "u")
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM newprod_weekly", (), 0), 1)

    def test_bad_files_are_refused(self):
        from app import newprod
        with self.assertRaises(ValueError):
            newprod.ingest("名前に日付が無い.xlsx", xlsx({"新商品": SHEET}), "u")
        with self.assertRaises(ValueError):
            newprod.ingest("週次売上分析_20261023-20261105.xlsx", xlsx({"サマリー": [["区分"]]}), "u")
        with self.assertRaises(ValueError):
            newprod.ingest("週次売上分析_20261023-20261105.xlsx", xlsx({"新商品": [["違う見出し"]]}), "u")

    def test_pending_then_register_links_labels_and_completes_task(self):
        from app import newprod
        newprod.ingest("週次売上分析_20261023-20261105.xlsx", xlsx({"新商品": SHEET}), "u")
        newprod.on_launched(self.pid)
        newprod.on_launched(self.pid)                                   # 何度呼んでも1つ
        v = newprod.view()
        self.assertEqual([p["id"] for p in v["pending"]], [self.pid])
        self.assertEqual(v["pending"][0]["candidates"], ["ウォールステッカー"])
        r = newprod.register(self.pid, ["ウォールステッカー"], "u")
        self.assertEqual(r["task_done"], 1)
        self.assertEqual(self.store.val("SELECT status FROM task WHERE project_id=?", (self.pid,)), "完了")
        v = newprod.view()
        self.assertEqual(v["pending"], [])
        row = v["rows"][0]
        self.assertEqual((row["period"]["sales"], row["monthly"]["cumulative_sales"]), (81550, 140200))
        self.assertEqual([x["label"] for x in v["unlinked_labels"]], ["目印チャーム【剣道】", "目印チャーム【弓道】"])
        with self.assertRaises(ValueError):
            newprod.register(self.pid, ["  "], "u")

    def test_several_labels_add_up(self):
        from app import newprod
        newprod.ingest("週次売上分析_20261023-20261105.xlsx", xlsx({"新商品": SHEET}), "u")
        newprod.register(self.pid, ["目印チャーム【剣道】", "目印チャーム【弓道】"], "u")
        p = newprod.of_project(self.pid)
        self.assertEqual((p["period"]["sales"], p["period"]["target_sales"]), (4000, 2000))
        self.assertEqual(p["period"]["rate"], 2.0)

    def test_one_year_boundary_drops_from_count_but_keeps_record(self):
        import datetime as dt
        from unittest import mock
        from app import newprod
        self.assertEqual(newprod.window_since(dt.date(2026, 11, 10)), "2025-11-10")
        self.assertEqual(newprod.window_since(dt.date(2028, 2, 29)), "2027-02-28")
        self.assertTrue(newprod.in_window("2025-11-11", dt.date(2026, 11, 10)))
        self.assertFalse(newprod.in_window("2025-11-10", dt.date(2026, 11, 10)))    # 1年たった日から外す
        self.assertFalse(newprod.in_window("2026-11-11", dt.date(2026, 11, 10)))    # 発売前は数えない
        newprod.ingest("週次売上分析_20261023-20261105.xlsx", xlsx({"新商品": SHEET}), "u")
        newprod.register(self.pid, ["ウォールステッカー"], "u")
        with mock.patch.object(self.store, "today", return_value=dt.date(2027, 9, 28)):
            v = newprod.view()
            self.assertEqual([r["id"] for r in v["rows"]], [self.pid])
            self.assertEqual(v["expired"], [])
        with mock.patch.object(self.store, "today", return_value=dt.date(2027, 9, 29)):
            v = newprod.view()
            self.assertEqual(v["rows"], [])
            self.assertEqual([r["id"] for r in v["expired"]], [self.pid])         # 外しても一覧で見える
            self.assertEqual(newprod.of_project(self.pid)["period"]["sales"], 81550)  # カルテの記録は残る
        self.assertEqual(self.store.val("SELECT COUNT(*) FROM newprod_weekly", (), 0), 1)  # 取り込んだ数字は消さない

    def test_unlinked_launched_project_is_pending_not_zero(self):
        from app import newprod
        newprod.ingest("週次売上分析_20261023-20261105.xlsx", xlsx({"新商品": SHEET}), "u")
        v = newprod.view()
        self.assertEqual([p["id"] for p in v["pending"]], [self.pid])            # 結んでいない発売済みは未紐付け
        self.assertEqual(v["rows"], [])                                          # 0円の行にはしない

    def test_g5_puts_project_in_pending(self):
        from app import gate, newprod
        self.store.ex("UPDATE project SET stage='生産確定' WHERE id=?", (self.pid,))
        gate._launched(dict(self.store.one("SELECT * FROM project WHERE id=?", (self.pid,))), "u")
        self.assertEqual(newprod.of_project(self.pid)["state"], "登録待ち")
