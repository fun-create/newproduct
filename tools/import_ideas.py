#!/usr/bin/env python3
"""
アイデアの移行（F-1-1）。4シートを `idea` へ1本にまとめる。

    python3 tools/import_ideas.py --dry-run     # 数えるだけ。DB を触らない
    python3 tools/import_ideas.py               # 入れる
    python3 tools/import_ideas.py --report      # いま DB に何件入っているか

**守っていること。**

1. **スコアは v1 として原文のまま。**再採点しない（F-1-10・第8章 ⑦）。
   総合点もランクも、シートに書いてある値をそのまま入れる。
   ウェイトも閾値も**シートごとに別**で、それは `rubric` の
   v1-original / v1-uchiwa / v1-lovot / v1-bukkomi に記録してある。
2. **`origin`（起票経路）は移行分では不明。**元のシートに列そのものが無い。
   `NULL` ＋ `origin_note='移行時不明'`。**推測で埋めない**（N-10）。
3. **冪等。**2回流しても重複しない。鍵は (source_sheet, 正規化した商品案名)。
   `idea_score.scored_at` は**2回目で書き換えない**（採点した日時であって、
   取り込んだ日時ではない）。
4. **件数を必ず報告する。**「入っているはず」で通さない。

**シートの件数について。**要件定義は「オリジナル750件・推し活うちわ755件」と
書いているが、**書き出された TSV で商品案名が入っている行はそれより少ない**
（実測値はこのツールの出力を見ること）。スプレッドシートの行数には、
書式だけが残った空行が含まれている。**取り込むのは商品案名のある行だけ。**
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import idea as idea_m    # noqa: E402
from app import store             # noqa: E402

SEED_DIR = BASE / "seed"

ORIGINAL_TSV = SEED_DIR / "02_アイデアリスト__オリジナルグッズ評価用.tsv"
UCHIWA_TSV = SEED_DIR / "02_アイデアリスト__推し活うちわ評価用.tsv"
BUKKOMI_TSV = SEED_DIR / "02_アイデアリスト__ぶっこみ評価用.tsv"
LOVOT_TSV = SEED_DIR / "10_アイデアリストLOVOT専用__アイデア評価.tsv"

IMPORTED_BY = "import_ideas"
ORIGIN_NOTE = "移行時不明"

# 書き出された TSV はセル内の改行を `\n` の2文字に潰してある。
# **UNC パス（`\\New-terastation\...`）を壊さないため、`\\` を含む値は触らない。**
_UNC = "\\\\"


def _text(s: str | None) -> str | None:
    s = (s or "").strip()
    if not s:
        return None
    if _UNC in s:
        return s
    return s.replace("\\n", "\n")


def _lead_int(s: str | None) -> int | None:
    """「4点：フリーカット」「4：既存資材で…」→ 4。**空なら None。0 にしない。**"""
    m = re.match(r"\s*([1-5])\s*[点:：]", s or "")
    return int(m.group(1)) if m else None


def _num(s: str | None) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _cells(row: list[str], n: int) -> list[str]:
    return [(c or "").strip() for c in row] + [""] * max(0, n - len(row))


# ══════════════════════════════════════════════════════════
# シートごとの読み取り
# ══════════════════════════════════════════════════════════
def read_sheet_6axis(path: Path) -> list[dict]:
    """オリジナル／推し活うちわ。列の並びは2シートで同じ（見出しの語だけ違う）。

    0 新商品案 / 2 概要・仕様 / 3 想定ターゲット / 4 デザイン自由度 /
    5,6 参考商品 / 7〜12 スコア6項目 / 13 総合点 / 15 ランク /
    16,17 エリア候補 / 18 生産方法
    """
    out = []
    with path.open(encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.reader(f, delimiter="\t")):
            if i < 2:                       # 0 注記行 / 1 見出し行
                continue
            c = _cells(row, 19)
            if not c[0]:
                continue                     # 書式だけが残った空行
            axes = {}
            for code, col in (("demand", 7), ("market_size", 8),
                              ("advantage", 9), ("premium", 10),
                              ("year_round", 11), ("theme_fit", 12)):
                axes[code] = _num(c[col])
            out.append({
                "row": i + 1, "title": c[0], "summary": _text(c[2]),
                "target_scene": _text(c[3]), "design_freedom": _lead_int(c[4]),
                "ref_url1": _text(c[5]), "ref_url2": _text(c[6]),
                "area1": _text(c[16]) or None, "area2": _text(c[17]) or None,
                "production_feasibility": _lead_int(c[18]),
                "axes": axes, "total": _num(c[13]), "rank": c[15] or None,
            })
    return out


def read_bukkomi(path: Path) -> list[dict]:
    """ぶっこみ。見出しは7行目。**実データは未採点の2件だけ。**

    0 新商品案 / 1 概要 / 2 想定ターゲット / 3,4 参考商品 /
    5 購買意欲 / 6 ターゲット規模 / 7 生産工数 / 8 合計 / 9 ランク / 10 生産方法
    """
    out = []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    start = None
    for i, row in enumerate(rows):
        if row and (row[0] or "").strip() == "新商品案":
            start = i + 1
            break
    if start is None:
        return out
    for i in range(start, len(rows)):
        c = _cells(rows[i], 11)
        if not c[0]:
            continue
        out.append({
            "row": i + 1, "title": c[0], "summary": _text(c[1]),
            "target_scene": _text(c[2]), "design_freedom": None,
            "ref_url1": _text(c[3]), "ref_url2": _text(c[4]),
            "area1": None, "area2": None,
            "production_feasibility": _lead_int(c[10]),
            "axes": {"demand": _num(c[5]), "market_size": _num(c[6]),
                     "effort": _num(c[7])},
            "total": _num(c[8]), "rank": c[9] or None,
        })
    return out


LOVOT_COLS = [("lovot_love", 3), ("offkai", 4), ("lovot_rule", 5),
              ("repeat", 6), ("bundle", 7), ("manufacturability", 8),
              ("price_freedom", 9)]


def read_lovot(path: Path) -> list[dict]:
    """LOVOT専用。7項目・140点満点。

    末尾に `【追加】` の見出しがあり、その下は**採点されていない案の名前だけ**。
    捨てずに入れる（採点が無いことは `idea_score` が無いことで表す）。
    """
    out = []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    extra = False
    for i, row in enumerate(rows):
        if i == 0:
            continue
        c = _cells(row, 17)
        if not any(c):
            continue
        if c[0] == "【追加】":
            extra = True
            continue
        if not c[0]:
            continue
        if extra:
            out.append({"row": i + 1, "title": c[0], "summary": None,
                        "target_scene": None, "design_freedom": None,
                        "ref_url1": None, "ref_url2": None,
                        "area1": None, "area2": None,
                        "production_feasibility": None,
                        "axes": {}, "total": None, "rank": None,
                        "note": "元シートの【追加】欄。**採点されていない。**"})
            continue
        axes = {code: _num(c[col]) for code, col in LOVOT_COLS}
        out.append({
            "row": i + 1, "title": c[0], "summary": _text(c[1]),
            "target_scene": _text(c[2]), "design_freedom": None,
            "ref_url1": None, "ref_url2": None,
            "area1": None, "area2": None, "production_feasibility": None,
            "axes": axes, "total": _num(c[10]), "rank": c[11] or None,
            "note": _text(c[16]),
        })
    return out


SHEETS = [
    ("original", ORIGINAL_TSV, read_sheet_6axis, "v1-original", "lifeevent"),
    ("uchiwa", UCHIWA_TSV, read_sheet_6axis, "v1-uchiwa", "oshikatsu"),
    ("bukkomi", BUKKOMI_TSV, read_bukkomi, "v1-bukkomi", "bukkomi"),
    ("lovot", LOVOT_TSV, read_lovot, "v1-lovot", "lovot"),
]


# ══════════════════════════════════════════════════════════
# 入れる
# ══════════════════════════════════════════════════════════
def _upsert_idea(sheet: str, theme_id: str, r: dict) -> tuple[str, bool]:
    """(idea_id, 新規か) を返す。**鍵は (シート, 正規化した商品案名)。**"""
    key = idea_m.normalize(r["title"])
    cur = store.one("SELECT id FROM idea WHERE source_sheet=? AND source_key=?",
                    (sheet, key))
    has_score = any(v is not None for v in r["axes"].values()) or r["total"] is not None
    if cur is not None:
        # **シート由来の列だけを更新する。**
        # 人が足した `origin` `stage` `demand_cycle` `expected_margin_yen` `note` は触らない
        store.ex(
            "UPDATE idea SET title=?,summary=?,target_scene=?,design_freedom=?,"
            "ref_url1=?,ref_url2=?,area1=?,area2=?,production_feasibility=?,"
            "source_row=?,updated_at=?,updated_by=? WHERE id=?",
            (r["title"], r["summary"], r["target_scene"], r["design_freedom"],
             r["ref_url1"], r["ref_url2"], r["area1"], r["area2"],
             r["production_feasibility"], r["row"], store.now_s(), IMPORTED_BY,
             cur["id"]))
        return cur["id"], False
    iid = store.new_id("idea")
    store.ex(
        "INSERT INTO idea (id,title,summary,target_scene,design_freedom,"
        "ref_url1,ref_url2,area1,area2,production_feasibility,origin,origin_note,"
        "theme_id,stage,note,source_sheet,source_key,source_row,"
        "created_at,created_by,updated_at,updated_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?)",
        (iid, r["title"], r["summary"], r["target_scene"], r["design_freedom"],
         r["ref_url1"], r["ref_url2"], r["area1"], r["area2"],
         r["production_feasibility"], ORIGIN_NOTE, theme_id,
         "採点済" if has_score else "起票", r.get("note"),
         sheet, key, r["row"], store.now_s(), IMPORTED_BY,
         store.now_s(), IMPORTED_BY))
    return iid, True


def _upsert_score(idea_id: str, version: str, r: dict) -> bool:
    """v1 のスコア。**原文のまま。**再計算しない（F-1-10）。"""
    axes = {k: v for k, v in r["axes"].items() if v is not None}
    if not axes and r["total"] is None:
        return False
    cur = store.one("SELECT id FROM idea_score WHERE idea_id=? AND rubric_version=?",
                    (idea_id, version))
    payload = (json.dumps(axes, ensure_ascii=False), r["total"], r["rank"])
    if cur is not None:
        # **`scored_at` は書き換えない。**採点した日時であって取り込んだ日時ではない
        store.ex("UPDATE idea_score SET axes=?,total=?,rank=? WHERE id=?",
                 payload + (cur["id"],))
        return False
    store.ex(
        "INSERT INTO idea_score (idea_id,rubric_version,axes,total,rank,"
        "rank_basis,scored_by,scored_at,source_note) "
        "VALUES (?,?,?,?,?, 'sheet','import',?,?)",
        (idea_id, version, payload[0], payload[1], payload[2], store.now_s(),
         "シートの総合点・ランクをそのまま。**再採点していない**（F-1-10）。"
         "ウェイトと閾値は rubric の " + version + " に記録"))
    return True


def run(dry: bool = False) -> dict:
    """**合計件数を必ず返す。**シート別に。"""
    store.migrate()
    idea_m.seed_themes()
    idea_m.seed_rubrics()
    idea_m.seed_settings()
    store.conn().commit()

    out = {"sheets": [], "missing": [], "total_rows": 0,
           "total_ideas_new": 0, "total_scores_new": 0}
    for sheet, path, reader, version, theme_id in SHEETS:
        if not path.is_file():
            out["missing"].append(str(path))
            out["sheets"].append({"sheet": sheet, "file": path.name,
                                  "state": "ファイルが無い", "rows": None})
            continue
        rows = reader(path)
        scored = sum(1 for r in rows
                     if any(v is not None for v in r["axes"].values())
                     or r["total"] is not None)
        rec = {"sheet": sheet, "file": path.name, "rubric_version": version,
               "theme_id": theme_id, "rows": len(rows), "with_v1_score": scored,
               "without_v1_score": len(rows) - scored}
        out["total_rows"] += len(rows)
        if not dry:
            new_i = new_s = 0
            for r in rows:
                iid, created = _upsert_idea(sheet, theme_id, r)
                new_i += 1 if created else 0
                new_s += 1 if _upsert_score(iid, version, r) else 0
            store.conn().commit()
            rec["ideas_new"] = new_i
            rec["ideas_existing"] = len(rows) - new_i
            rec["scores_new"] = new_s
            out["total_ideas_new"] += new_i
            out["total_scores_new"] += new_s
        out["sheets"].append(rec)

    if not dry:
        out["db"] = idea_m.counts()
        out["origin_note"] = (
            f"移行した {out['db']['idea_without_origin']} 件の `origin`（起票経路）は "
            "**不明**です。元のシートに起票経路の列そのものがありません。"
            "推測では埋めていません（N-10）。")
    out["note"] = (
        "スコアは v1 として**原文のまま**入れています。再採点していません"
        "（F-1-10・第8章 ⑦）。ウェイトも閾値もシートごとに別で、"
        "rubric の v1-original / v1-uchiwa / v1-lovot / v1-bukkomi に記録済みです。")
    return out


def report() -> dict:
    store.migrate()
    return {"db": idea_m.counts(),
            "rubrics": [{"version": r["version"], "label": r["label"],
                         "generation": r["generation"],
                         "rank_method": r["rank_method"],
                         "thresholds": r["thresholds"]}
                        for r in idea_m.rubrics()]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="数えるだけ。DB を触らない")
    ap.add_argument("--report", action="store_true",
                    help="いま DB に何件入っているかを出す")
    a = ap.parse_args()
    d = report() if a.report else run(dry=a.dry_run)
    print(json.dumps(d, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
