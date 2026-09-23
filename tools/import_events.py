#!/usr/bin/env python3
"""
機会カレンダーの取り込み（F-2 ／ FR-78・FR-79）。

    python3 tools/import_events.py --dry-run   # 数えるだけ。DB を触らない
    python3 tools/import_events.py --review    # 人が突き合わせるための一覧を出す
    python3 tools/import_events.py             # 入れる
    python3 tools/import_events.py --report    # いま DB に何件入っているか

**2026-09-21 に「機械では読めない」として 0件のままにした表**（ADR-020）。
読み直したところ、**式で裏を取れば読める**ことが分かった。以下が判断の全部。

## 元表の形

`seed/01_進捗管理商品開発__イベント.tsv`（249行）は、見た目が1枚でも**中身は3つ**。

| ブロック | 行 | 中身 |
|---|---|---|
| 1 | 3〜57 | 年間イベント（列2-4）／販売可能性が高い年間行事（列6）／**採点済みライフイベント**／新商品が作れていないライフイベント |
| 2 | 58〜184 | 年齢別ライフイベント × 購入者（本人・親・祖父母・友人知人・子や孫）の◯印。**採点は無い** |
| 3 | 243〜248 | 絵文字つきの走り書き（12月の商戦メモ） |

**このツールが入れるのはブロック1だけ。**ブロック2は形がまったく違う別の資産で、
F-2（機会カレンダー）の要件ではない。**入れないことを、ここに書いて残す。**

## 「8列目と10列目にずれている」件の決着

採点済みライフイベントは、名前が **8列目（12件）と10列目（7件）**に分かれている
（1始まり。0始まりなら 7 と 9）。ずれは行15から始まり、最後まで続く。

**見出しで判断していない。**次の2つで裏を取った。

1. **式**: 総合 ＝ 記念品購買意欲×2 ＋ 写真親和性×1 ＋ 発生頻度×2（満点40）。
   **19件すべて誤差0。**数値5連をこの式で検算し、合ったものだけ採点行とみなす
2. **順位の連続性**: 総合スコアが 38 → 34 → 32 → 29×5 → 28 → 27×4 → 26×3 → 25×3 と
   単調に下がり、優先順位も 1→8 と切れ目なく続く。**ずれた7件は同じ並びの続き**

**10列目には別のものも入っている。**行24以降の「新商品が作れていないライフイベント」
（13件・商品例つき）が同じ列を使う。こちらは数値5連を持たないので、式の検算で分かれる。
**列位置だけで読むと、この2つが混ざる。**それが 09-21 に恐れたことで、恐れ自体は正しかった。

## 入れないもの

- **日付は原文のまま** `theme.day` へ入れる（「11月15日前後の土日」「1月の第２月曜日」
  「7〜8月」など）。**MM-DD に直さない。**直せない行が多く、直せた行だけ直すと
  「日付が入っている行」が実際より正確に見える
- 月が空のままの行（お花見・夏休み・冬休み）は **`month` を直前の月で埋める**。
  表計算の結合セルで、見出しの月がその行にも掛かっている（元表を目で確認済み）
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import store             # noqa: E402

SRC = BASE / "seed" / "01_進捗管理商品開発__イベント.tsv"

BLOCK1 = (3, 58)            # 年間イベント＋ライフイベント（0始まりの行番号・終端は含まない）
SOURCE = "01_進捗管理商品開発__イベント.tsv"


def _cell(row: list[str], j: int) -> str:
    return row[j].strip() if len(row) > j else ""


def _int(s: str):
    s = (s or "").strip()
    return int(s) if re.fullmatch(r"\d+", s) else None


def read_rows() -> list[list[str]]:
    if not SRC.is_file():
        raise FileNotFoundError(f"{SRC} がありません")
    with SRC.open(encoding="utf-8") as f:
        return list(csv.reader(f, delimiter="\t"))


def month_of(s: str):
    m = re.match(r"(\d{1,2})月", (s or "").strip())
    return int(m.group(1)) if m else None


def parse(rows: list[list[str]]) -> dict:
    """元表から4つを取り出す。**読めなかったものは数えて返す。**"""
    annual, scored, gaps = [], [], []
    sellable: set[str] = set()
    month, month_label = None, ""
    gap_header_row = None

    for i in range(*BLOCK1):
        r = rows[i]
        if _cell(r, 1):
            month_label = _cell(r, 1)
            month = month_of(month_label) or month
        # ① 年間イベント
        if _cell(r, 3):
            annual.append({"row": i, "month": month, "day": _cell(r, 2) or None,
                           "name": _cell(r, 3)})
        # ② 販売可能性が高い年間行事（列5）。年間イベント側の印として使う
        if _cell(r, 5):
            sellable.add(_cell(r, 5))
        # ④ の見出し（列9）。これ以降の列9は採点ではない
        if "新商品が作れていない" in _cell(r, 9):
            gap_header_row = i
        # ③ 採点済みライフイベント。**式で裏を取る**
        hit = None
        for j in range(len(r) - 4):
            v = [_int(_cell(r, j + k)) for k in range(5)]
            if all(x is not None for x in v) and v[0] * 2 + v[1] + v[2] * 2 == v[3]:
                hit = j
                break
        if hit is not None and hit >= 1 and _cell(r, hit - 1):
            scored.append({
                "row": i, "col": hit - 1, "name": _cell(r, hit - 1),
                "gift_intent": _int(_cell(r, hit)), "photo_fit": _int(_cell(r, hit + 1)),
                "frequency": _int(_cell(r, hit + 2)), "total": _int(_cell(r, hit + 3)),
                "priority": _int(_cell(r, hit + 4)),
            })
            continue
        # ④ 新商品が作れていないライフイベント（見出しより後の列9）
        if gap_header_row is not None and i > gap_header_row and _cell(r, 9):
            ideas = [x for x in (_cell(r, 10), _cell(r, 12), _cell(r, 13)) if x]
            gaps.append({"row": i, "name": _cell(r, 9),
                         "ideas": " / ".join(ideas) or None})
    names = {e["name"] for e in annual}
    return {"annual": annual, "scored": scored, "gaps": gaps,
            "sellable": sorted(sellable),
            # **名前の揺れで結びつかないものを黙って落とさない**（N-10）。
            # 「入園入学」と「入学式」、「部活動引退（夏～秋）」と「部活動引退」など、
            # 人が見れば同じでも**機械で寄せると間違える**（「七五三/犬の日」は2つ）。
            # 対応表は人に決めてもらう。それまで印は付けない
            "sellable_unmatched": sorted(x for x in sellable if x not in names)}


def theme_id(kind: str, name: str) -> str:
    """**名前から決める。**2回流しても同じIDになること（冪等の鍵）。"""
    import hashlib
    h = hashlib.sha256(f"{kind}\t{name}".encode()).hexdigest()[:8]
    return ("ev" if kind == "年間イベント" else "lf") + h


def apply(d: dict, user_id: str = "import") -> dict:
    n = {"annual": 0, "scored": 0, "gaps": 0, "score_rows": 0}
    now = store.now_s()
    with store.tx() as c:
        for k, e in enumerate(d["annual"]):
            tid = theme_id("年間イベント", e["name"])
            c.execute(
                "INSERT INTO theme (id,label,kind,month,day,sort,note,sellable,"
                "source_row,source_col) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET month=excluded.month,day=excluded.day,"
                "sort=excluded.sort,sellable=excluded.sellable,"
                "source_row=excluded.source_row",
                (tid, e["name"], "年間イベント", e["month"], e["day"], k, None,
                 1 if e["name"] in d["sellable"] else 0, e["row"], 3))
            n["annual"] += 1
        for k, e in enumerate(d["scored"]):
            tid = theme_id("ライフイベント", e["name"])
            c.execute(
                "INSERT INTO theme (id,label,kind,month,day,sort,note,sellable,"
                "source_row,source_col) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET sort=excluded.sort,"
                "sellable=excluded.sellable,"
                "source_row=excluded.source_row,source_col=excluded.source_col",
                # **採点済みライフイベントは全件が「販売可能性が高い」。**
                # 元表2行目の見出しが「販売可能性が高いライフイベント」（7列目）で、
                # この列に並んでいること自体が印。別に印の列があるわけではない
                (tid, e["name"], "ライフイベント", None, None, k, None, 1,
                 e["row"], e["col"] + 1))          # 1始まりで残す（8列目/10列目）
            c.execute(
                "INSERT INTO theme_score (theme_id,gift_intent,photo_fit,frequency,"
                "total,priority,source,captured_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(theme_id) DO UPDATE SET gift_intent=excluded.gift_intent,"
                "photo_fit=excluded.photo_fit,frequency=excluded.frequency,"
                "total=excluded.total,priority=excluded.priority",
                (tid, e["gift_intent"], e["photo_fit"], e["frequency"], e["total"],
                 e["priority"], f"{SOURCE} 行{e['row']}（{e['col'] + 1}列目）", now))
            n["scored"] += 1
            n["score_rows"] += 1
        for e in d["gaps"]:
            tid = theme_id("ライフイベント", e["name"])
            # **採点済みと同じ名前なら、その行に印を足すだけ**（別レコードにしない）
            c.execute(
                "INSERT INTO theme (id,label,kind,sort,product_gap,product_ideas,"
                "source_row,source_col) VALUES (?,?,?,?,1,?,?,10) "
                "ON CONFLICT(id) DO UPDATE SET product_gap=1,"
                "product_ideas=excluded.product_ideas",
                (tid, e["name"], "ライフイベント", 900 + e["row"], e["ideas"],
                 e["row"]))
            n["gaps"] += 1
    return n


def report() -> dict:
    return {
        "年間イベント": store.val("SELECT COUNT(*) FROM theme WHERE kind='年間イベント'", (), 0),
        "ライフイベント": store.val("SELECT COUNT(*) FROM theme WHERE kind='ライフイベント'", (), 0),
        "採点あり": store.val("SELECT COUNT(*) FROM theme_score", (), 0),
        "販売可能性が高い": store.val("SELECT COUNT(*) FROM theme WHERE sellable=1", (), 0),
        "商品が作れていない": store.val("SELECT COUNT(*) FROM theme WHERE product_gap=1", (), 0),
        "評価テーマ": store.val("SELECT COUNT(*) FROM theme WHERE kind='評価テーマ'", (), 0),
    }


def review(d: dict) -> str:
    """人が元表と突き合わせるための一覧。**列位置を必ず出す。**"""
    out = ["# 機会カレンダー 取り込み前の確認", "",
           f"元表: `seed/{SOURCE}`（249行）／ブロック1（元表の4〜58行目）だけを読みます。", ""]
    out += ["## ① 年間イベント %d件（FR-78）" % len(d["annual"]), "",
            "| 元表の行 | 月 | 日付（原文のまま） | イベント | 販売可能性が高い |",
            "|---|---|---|---|---|"]
    for e in d["annual"]:
        out.append(f"| {e['row'] + 1} | {e['month'] or '—'} | {e['day'] or '—'} | "
                   f"{e['name']} | {'○' if e['name'] in d['sellable'] else ''} |")
    out += ["", "## ② 採点済みライフイベント %d件（FR-79）" % len(d["scored"]), "",
            "**名前が何列目にあったかを必ず見てください。**8列目が12件、10列目が7件です。",
            "総合は 購買意欲×2 ＋ 写真親和性 ＋ 発生頻度×2（満点40）で、**19件すべて誤差0**です。", "",
            "| 元表の行 | 名前の列 | ライフイベント | 購買意欲 | 写真 | 頻度 | 総合 | 順位 |",
            "|---|---|---|---|---|---|---|---|"]
    for e in d["scored"]:
        col = f"**{e['col'] + 1}列目**" if e["col"] + 1 != 8 else "8列目"
        out.append(f"| {e['row'] + 1} | {col} | {e['name']} | {e['gift_intent']} | "
                   f"{e['photo_fit']} | {e['frequency']} | {e['total']} | {e['priority']} |")
    if d["sellable_unmatched"]:
        out += ["", "## ★ 確認してほしいこと ── 名前の揺れ %d件"
                % len(d["sellable_unmatched"]), "",
                "元表6列目「販売可能性が高い年間イベント」12件のうち、**下の6件は"
                "年間イベントの名前と文字が一致しません。**人が見れば同じでも、"
                "機械で寄せると間違えます（「七五三/犬の日」は2つのイベントです）。",
                "**推測で結びつけていないので、いまは印が付いていません。**", "",
                "| 6列目の書き方 | 年間イベント側の候補（こちらの見立て。確認してください） |",
                "|---|---|"]
        guess = {"七五三/犬の日": "七五三 ＋ 犬の日（**2つに分かれます**）",
                 "入園入学": "入学式",
                 "卒園卒業、部活動引退（冬～春）": "卒園・卒業・卒部",
                 "夏フェス・コンサート": "夏フェス",
                 "新入生・新入社員歓迎": "**該当なし。**年間イベント53件に見当たりません",
                 "部活動引退（夏～秋）": "部活動引退"}
        for x in d["sellable_unmatched"]:
            out.append(f"| {x} | {guess.get(x, '—')} |")
    out += ["", "## ③ 新商品が作れていないライフイベント %d件" % len(d["gaps"]), "",
            "**同じ10列目ですが、採点はありません。**自社の欠けの申告なので、スコアと混ぜません。", "",
            "| 元表の行 | ライフイベント | 商品例（原文） |", "|---|---|---|"]
    for e in d["gaps"]:
        out.append(f"| {e['row'] + 1} | {e['name']} | {e['ideas'] or '—'} |")
    out += ["", "## 入れないもの", "",
            "- **元表 59〜185行目**（年齢別ライフイベント × 購入者の◯印・124行）。"
            "形がまったく違う別の資産で、F-2 の要件ではありません",
            "- **元表 244〜249行目**（絵文字つきの走り書き・12月の商戦メモ）",
            "- 日付を `MM-DD` に直すこと。「11月15日前後の土日」のように直せない行が多く、"
            "直せた行だけ直すと**日付が入っている行が実際より正確に見えます**"]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="数えるだけ。DB を触らない")
    ap.add_argument("--review", action="store_true", help="確認用の一覧を出す")
    ap.add_argument("--report", action="store_true", help="いま DB に何件入っているか")
    a = ap.parse_args()
    if a.report:
        for k, v in report().items():
            print(f"  {k:24} {v}")
        return 0
    d = parse(read_rows())
    if a.review:
        print(review(d), end="")
        return 0
    print(f"年間イベント {len(d['annual'])} 件 ／ 採点済みライフイベント {len(d['scored'])} 件"
          f"（8列目 {sum(1 for e in d['scored'] if e['col'] == 7)} ・"
          f"10列目 {sum(1 for e in d['scored'] if e['col'] == 9)}）"
          f" ／ 商品が作れていない {len(d['gaps'])} 件"
          f" ／ 販売可能性が高い年間行事 {len(d['sellable'])} 件")
    if d["sellable_unmatched"]:
        print(f"※ そのうち {len(d['sellable_unmatched'])} 件は年間イベント名と一致しません。"
              "**推測で結びつけないので、印は付きません**（人の確認待ち）:")
        for x in d["sellable_unmatched"]:
            print(f"    - {x}")
    if a.dry_run:
        print("[dry-run] DB は触っていません")
        return 0
    store.migrate()
    n = apply(d)
    print("入れました:", n)
    for k, v in report().items():
        print(f"  {k:24} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
