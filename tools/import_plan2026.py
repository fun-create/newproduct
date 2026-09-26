#!/usr/bin/env python3
"""
年間プラン2026年度の取り込み（F-3 ／ 2026-09-26 十文字さんの選択A）。

    python3 tools/import_plan2026.py --review    # 各行をどう読んだかの一覧（人が突き合わせる）
    python3 tools/import_plan2026.py --dry-run
    python3 tools/import_plan2026.py             # 2026年度の版（策定中）と枠26本を入れる

## 元表の形（`seed/01_進捗管理商品開発__年間プラン2026年度.tsv`・108行）

結合セルのせいで**列位置が行ごとにずれる**（機会カレンダーと同じ）。
**列位置で読まず、語彙で読む。**

- 商品タイプは6語のどれか: ページリニューアル／既成デザイン／フリーカット／スキンシール／名入れ／Web deco
  → この語がある列 t を起点にする
- 作成エリアは t-1（グッズ／うちわ／横並び・その他／アイコス／アイロン／UV）
- 商品名は t-2。**t-1 がエリアの語彙に無いときは、t-1 が商品名でエリアは空**
  （保険。2026-09-26 の実測では26行すべてが語彙どおりで、この分岐は0件だった）
- タスク設定期限 = 行内の `yyyy/m/d`、発売予定日 = `m/d`（年は「2026年 5月」等の見出しから）
- 月ごとの工数ポイント（14.5 など）は**月の合計**であって枠の値ではない。**枠には入れない**

## 入れ方

- 版は `2026年度 年間プラン（進捗管理シートから移行）`・**策定中**。承認は十文字さんが画面で行う
- 枠の `product_kind`: エリアが うちわ → `uchiwa`／タイプが ページリニューアル → `pagerenew`／それ以外 → `original`
  （ADR-030「うちわか、そうでないか」に合わせた最小の割り当て。**あとから画面で変えられる**）
- `flow_type` はタイプから（Web deco→webdeco、スキンシール→newmodel 等）。**工数ポイントは係数から自動**
- `occasion` は機会の列（年間イベント／2か月前／ライフイベント）を**原文のまま繋いだもの**
- 冪等: `(source_sheet, source_key=元表の行番号)` で2回流しても増えない
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import plan as plan_m   # noqa: E402
from app import store            # noqa: E402

SRC = BASE / "seed" / "01_進捗管理商品開発__年間プラン2026年度.tsv"
SHEET = "01_進捗管理商品開発__年間プラン2026年度.tsv"
LABEL = "2026年度 年間プラン（進捗管理シートから移行）"
FY = 2026

TYPES = {"ページリニューアル": "pagerenew", "既成デザイン": "readymade",
         "フリーカット": "freecut", "スキンシール": "newmodel",
         "名入れ": "meire", "Web deco": "webdeco", "Webdeco": "webdeco"}
AREAS = {"グッズ", "うちわ", "横並び・その他", "アイコス", "アイロン", "UV"}


def _g(r, j):
    """セルの文字。**書き出しは改行を文字列 `\\n` で持ち、ゼロ幅スペースも混じる。**両方落とす。"""
    v = r[j] if len(r) > j else ""
    return v.replace("\\n", " ").replace("\u200b", "").strip()


def read_rows():
    if not SRC.is_file():
        raise FileNotFoundError(f"{SRC} がありません")
    with SRC.open(encoding="utf-8") as f:
        return list(csv.reader(f, delimiter="\t"))


def parse(rows) -> list[dict]:
    out, year, month = [], None, None
    for i, r in enumerate(rows):
        # 「2026年\n5月」「2027年\n1月」の見出し（列0か列1）
        for j in (0, 1):
            m = re.search(r"(20\d\d)年\s*(\d{1,2})月", _g(r, j))
            if m:
                year, month = int(m.group(1)), int(m.group(2))
        t = next((j for j in range(len(r)) if _g(r, j) in TYPES), None)
        if t is None:
            continue
        typ = _g(r, t)
        area, prod, flags = _g(r, t - 1), _g(r, t - 2), []
        if area and area not in AREAS:
            prod, area = area, ""              # 保険（実測では0件）
            flags.append("エリア列に商品名があった（エリアは空として読んだ）")
        if not prod:
            flags.append("商品名が空")
        setup = next((_g(r, j) for j in range(len(r))
                      if re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", _g(r, j))), "")
        md = next((_g(r, j) for j in range(len(r))
                   if re.fullmatch(r"\d{1,2}/\d{1,2}", _g(r, j))), "")
        launch = None
        if md and year and month:
            mm, dd = (int(x) for x in md.split("/"))
            if mm != month:
                flags.append(f"発売日の月({mm})と見出しの月({month})が違う")
            launch = f"{year:04d}-{mm:02d}-{dd:02d}"
        # 機会（列1〜3 のうち、商品名より左にある文字列）。原文のまま繋ぐ
        occ = " ／ ".join(x for x in (_g(r, j) for j in range(1, max(1, t - 2))) if x)
        out.append({"row": i, "year": year, "month": month, "type": typ,
                    "flow": TYPES[typ], "area": area, "product": prod,
                    "setup_due": setup.replace("/", "-") if setup else None,
                    "launch": launch, "occasion": occ or None, "flags": flags})
    return out


def kind_of(e) -> str:
    if e["area"] == "うちわ":
        return "uchiwa"
    if e["flow"] == "pagerenew":
        return "pagerenew"
    return "original"


def apply(user_id="import") -> dict:
    v = store.one("SELECT id FROM plan_version WHERE fiscal_year=? AND label=?", (FY, LABEL))
    vid = v["id"] if v else plan_m.create_version(user_id, FY, LABEL,
        note=f"{SHEET} から 2026-09-26 に移行。枠の中身は元表のまま。承認は画面で")["id"]
    n = {"version": vid, "created": 0, "kept": 0}
    for e in parse(read_rows()):
        key = str(e["row"])
        if store.one("SELECT 1 FROM plan_slot WHERE source_sheet=? AND source_key=?", (SHEET, key)):
            n["kept"] += 1
            continue
        s = plan_m.create_slot(user_id, version_id=vid,
                               launch_month=e["launch"][:7] if e["launch"] else f"{e['year']}-{e['month']:02d}",
                               launch_date=e["launch"] or "",
                               product_kind=kind_of(e), flow_type=e["flow"],
                               area=e["area"], occasion=e["occasion"],
                               note=("商品: " + (e["product"] or "（空）")
                                     + (f"／タスク設定期限（元表）: {e['setup_due']}" if e["setup_due"] else "")
                                     + (("／" + "・".join(e["flags"])) if e["flags"] else "")))
        store.ex("UPDATE plan_slot SET source_sheet=?,source_key=?,source_row=? WHERE id=?",
                 (SHEET, key, e["row"], s["id"]))
        n["created"] += 1
    store.conn().commit()
    return n


def review(items) -> str:
    out = ["# 年間プラン2026年度 取り込み前の確認", "",
           f"元表: `seed/{SHEET}`。**列位置ではなく語彙で読みました**（商品タイプ6語を起点に、左隣がエリア、その左が商品名）。", "",
           "| 元表の行 | 発売予定日 | 商品（原文） | エリア | 商品タイプ | 枠の種別 | 機会（原文） | 注意 |",
           "|---|---|---|---|---|---|---|---|"]
    for e in items:
        out.append(f"| {e['row']+1} | {e['launch'] or '—'} | {e['product'] or '—'} | {e['area'] or '—'} | "
                   f"{e['type']} | {kind_of(e)} | {(e['occasion'] or '—')[:40]} | {'**' + '・'.join(e['flags']) + '**' if e['flags'] else ''} |")
    out += ["", f"合計 {len(items)} 枠。**工数ポイントは入れていません**（元表の 14.5 等は月の合計で、枠の値ではありません。"
            "枠の工数ポイントは開発タイプの係数から自動で入ります）。", "",
            "枠の種別は ADR-030（うちわか、そうでないか）に合わせた最小の割り当てです。**画面で変えられます。**"]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--review", action="store_true")
    a = ap.parse_args()
    items = parse(read_rows())
    if a.review:
        print(review(items), end="")
        return 0
    flagged = sum(1 for e in items if e["flags"])
    print(f"{len(items)} 枠（注意あり {flagged}）／ 種別: "
          + ", ".join(f"{k} {sum(1 for e in items if kind_of(e)==k)}" for k in ("original", "uchiwa", "pagerenew")))
    if a.dry_run:
        print("[dry-run] DB は触っていません")
        return 0
    store.migrate()
    print("入れました:", apply())
    return 0


if __name__ == "__main__":
    sys.exit(main())
