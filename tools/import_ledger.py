#!/usr/bin/env python3
"""
進捗管理シート（全タスク一覧）の取り込み（F-4-8・F-5 ／ 2026-09-26 十文字さんの選択A）。

    python3 tools/import_ledger.py --review    # 案件と件数、年間プランとの名前の突き合わせ
    python3 tools/import_ledger.py --dry-run
    python3 tools/import_ledger.py             # 案件・実タスク・案件外の仕事を入れる

**これが「既存作業の置き換え」の本丸。**`進捗管理（商品開発）` シートは商品開発部が毎日開く
台帳で、要件定義書の「未着手256・期限空欄88」の出どころ。ここが移らないと、
第2段は誰も使わない（全体設計書 R-5）。

## 元表の形（`seed/01_進捗管理商品開発__全タスク一覧.tsv`・522行）

    列0 案件名（タスク行に入る）  列1 タスク  列2 担当者  列3 進捗状況
    列4 開始  列5 期限  列6 所要時間(h/日)  列7〜13 今日〜6日後の対応フラグ

- 見出し行は列1だけ: 「2025年度（5～4月）」「2026年度（5～4月）」「N月発売」「商品発売以外のタスク」
- 案件の見出し行は列1に名前＋列7に「←終了したらグループを折り畳む」。**タスク0件の案件もある**
- **日付に年が無い**（「8月17日」）。**推測せず、元表の年度見出しから決める**:
  年度 Y の 5〜12月 → Y、1〜4月 → Y+1。これは表の構造であって推測ではない
- 「商品発売以外のタスク」より下は `work_item`（案件外・FR-47）。**同じ工数勘定に載せる**

## 入れ方

- 案件は `stage='開発中'`・`source_of_truth='app'`。**雛形のタスクは展開しない**
  （実際に消化した完了137件と、雛形の未着手が二重に並ぶため）。実タスクをそのまま持つ
- 進捗: 未着手→未着手／完了→完了／取組み中→着手
- 担当者: 管理者→admin／メンバー→member／商品開発部→devdept／Webマーケ→webmkt／生産部→prod／
  パートさん→part。**SE部は役割マスタに無いので NULL**（1行。発明しない）
- **年間プランの枠とは自動で結びつけない。**名前が揺れている（「うちの子 足形 アクスタスタンプ」と
  「うちの子あしあとアクスタスタンプ」）。候補を `--review` に出し、人が決める
- 冪等: 案件は `(source_sheet, source_key=案件名)`、タスクは `(project_id, source_row)`
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import project as project_m   # noqa: E402
from app import store                  # noqa: E402

SRC = BASE / "seed" / "01_進捗管理商品開発__全タスク一覧.tsv"
SHEET = "01_進捗管理商品開発__全タスク一覧.tsv"
STATUS = {"未着手": "未着手", "完了": "完了", "取組み中": "着手"}
ROLE = {"管理者": "admin", "メンバー": "member", "商品開発部": "devdept",
        "Webマーケ": "webmkt", "生産部": "prod", "パートさん": "part", "パート": "part"}
FOLD = "折り畳む"
OUTSIDE = "商品発売以外のタスク"


def _g(r, j):
    return r[j].strip() if len(r) > j else ""


def read_rows():
    if not SRC.is_file():
        raise FileNotFoundError(f"{SRC} がありません")
    with SRC.open(encoding="utf-8") as f:
        return list(csv.reader(f, delimiter="\t"))


def _date(s, fy):
    """「8月17日」→ ISO。**年は年度見出しから**。読めなければ None（推測しない）。"""
    m = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", s or "")
    if not m or not fy:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    y = fy if mo >= 5 else fy + 1
    try:
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except ValueError:
        return None


def parse(rows) -> dict:
    groups, fy, launch_month, outside, cur = [], None, None, False, None
    for i, r in enumerate(rows):
        c0, c1 = _g(r, 0), _g(r, 1)
        m = re.match(r"(20\d\d)年度", c1)
        if m and not c0:
            fy = int(m.group(1)); continue
        m = re.fullmatch(r"(\d{1,2})月発売", c1)
        if m and not c0:
            launch_month = int(m.group(1)); continue
        if c1 == OUTSIDE:
            outside = True; continue
        # 案件の見出し（列1に名前、列7に折り畳みの印、タスクの列が空）
        if c1 and not c0 and not _g(r, 2) and not _g(r, 3) and (FOLD in _g(r, 7) or outside):
            if c1.startswith("この行の上に") or c1 in ("4か月目標作成",):
                cur = None; continue
            cur = {"name": c1, "row": i, "fy": fy, "launch_month": launch_month,
                   "outside": outside, "tasks": []}
            groups.append(cur); continue
        # タスク行
        if c1 and (c0 or cur) and (_g(r, 2) or _g(r, 3) or _g(r, 6)):
            name = c0 or (cur["name"] if cur else None)
            if cur is None or cur["name"] != name:
                cur = next((g for g in groups if g["name"] == name), None)
                if cur is None:
                    cur = {"name": name, "row": i, "fy": fy, "launch_month": launch_month,
                           "outside": outside, "tasks": []}
                    groups.append(cur)
            role_raw, st_raw = _g(r, 2), _g(r, 3)
            hours = _g(r, 6)
            cur["tasks"].append({
                "row": i, "title": c1, "role_raw": role_raw,
                "role": ROLE.get(role_raw), "status": STATUS.get(st_raw, "未着手"),
                "status_raw": st_raw, "start": _date(_g(r, 4), cur["fy"]),
                "due": _date(_g(r, 5), cur["fy"]),
                "start_raw": _g(r, 4), "due_raw": _g(r, 5),
                "hours": float(hours) if re.fullmatch(r"\d+(\.\d+)?", hours) else None})
    return {"groups": groups}


def _norm(s):
    """名前の照合用。括弧・※以降を落とし、空白と中黒を抜く。**照合にだけ使う。保存しない。**"""
    s = re.sub(r"[（()【\[※].*$", "", s or "")
    return re.sub(r"[\s　・]", "", s)


def _slot_product(note):
    m = re.match(r"商品:\s*(.*?)(?:／|$)", note or "")
    return m.group(1).strip() if m else ""


def plan_candidates(groups):
    """年間プランの枠との名前の突き合わせ。**結びつけない。候補を出すだけ。**

    名前は揺れている（「うちの子 足形 アクスタスタンプ」と「うちの子あしあとアクスタスタンプ」）。
    **先頭4文字の一致**で候補を広めに拾い、決めるのは人（画面の「案件にする」）。
    """
    slots = store.rows(store.q("SELECT id, note, launch_month FROM plan_slot WHERE source_sheet IS NOT NULL"))
    out = []
    for g in groups:
        if g["outside"]:
            continue
        key = _norm(g["name"])
        hits = []
        for s in slots:
            prod = _norm(_slot_product(s["note"]))
            if key and prod and (key[:4] in prod or prod[:4] in key):
                hits.append((s["launch_month"], _slot_product(s["note"])[:26]))
        out.append((g["name"], hits[:3]))
    return out


TODAY_YM = None   # 検査で固定できるように。None なら store.today()


def launch_ym(g) -> str | None:
    if not (g["fy"] and g["launch_month"]):
        return None
    y = g["fy"] + (0 if g["launch_month"] >= 5 else 1)
    return f"{y:04d}-{g['launch_month']:02d}"


def stage_of(g) -> str:
    """**発売月が今月より前なら発売済、そうでなければ開発中。**
    表の年度・発売月の見出しから機械的に決める（推測ではなく表の構造）。画面で直せる。"""
    ym = launch_ym(g)
    now = TODAY_YM or store.today().isoformat()[:7]
    return "発売済" if (ym and ym < now) else "開発中"


def importable(g) -> bool:
    """**タスクが1件も無い案件の見出しは入れない。**
    2025年度の見出し行はすべて発売済みの記録で、タスクを持たない（実測: 31件）。
    要件定義書 8-3 ⑭「準備シート（開発中）は移す。発売済みは後段」に従う。"""
    return g["outside"] or len(g["tasks"]) > 0


def apply(user_id="import") -> dict:
    n = {"project": 0, "project_kept": 0, "skipped_no_task": 0, "task": 0,
         "work_item": 0, "work_item_kept": 0, "role_unknown": 0, "date_unreadable": 0}
    d = parse(read_rows())
    for g in d["groups"]:
        if not importable(g):
            n["skipped_no_task"] += 1
            continue
        if g["outside"]:
            for t in g["tasks"]:
                key = f"{g['name']}#{t['row']}"
                if store.one("SELECT 1 FROM work_item WHERE source_sheet=? AND source_key=?", (SHEET, key)):
                    n["work_item_kept"] += 1; continue
                store.ex("INSERT INTO work_item (kind,title,category,role,start_on,due_on,hours,"
                         "status,created_at,source_sheet,source_key,source_row) "
                         "VALUES ('案件外',?,?,?,?,?,?,?,?,?,?,?)",
                         (t["title"], g["name"], t["role"], t["start"], t["due"], t["hours"],
                          t["status"], store.now_s(), SHEET, key, t["row"]))
                n["work_item"] += 1
                n["role_unknown"] += (1 if t["role_raw"] and not t["role"] else 0)
            continue
        p = store.one("SELECT id FROM project WHERE source_sheet=? AND source_key=?", (SHEET, g["name"]))
        if p:
            pid = p["id"]; n["project_kept"] += 1
        else:
            lm = launch_ym(g)
            r = project_m.create(user_id, expand=False, internal_name=g["name"], stage=stage_of(g),
                                 summary=f"進捗管理シートから 2026-09-26 に移行"
                                         + (f"（{lm} 発売の枠）" if lm else ""),
                                 occasion=None)
            pid = r["id"]
            store.ex("UPDATE project SET source_sheet=?,source_key=?,source_row=? WHERE id=?",
                     (SHEET, g["name"], g["row"], pid))
            n["project"] += 1
        for seq, t in enumerate(g["tasks"], 1):
            if store.one("SELECT 1 FROM task WHERE project_id=? AND source_row=?", (pid, t["row"])):
                continue
            store.ex("INSERT INTO task (project_id,seq,title,role,start_on,due_on,hours,status,"
                     "done_at,created_at,kind,source_row) VALUES (?,?,?,?,?,?,?,?,?,?,'実作業',?)",
                     (pid, seq, t["title"], t["role"], t["start"], t["due"], t["hours"], t["status"],
                      (t["due"] or store.now_s()) if t["status"] == "完了" else None,
                      store.now_s(), t["row"]))
            n["task"] += 1
            n["role_unknown"] += (1 if t["role_raw"] and not t["role"] else 0)
            n["date_unreadable"] += (1 if (t["due_raw"] and not t["due"]) else 0)
    store.conn().commit()
    return n


def review(d) -> str:
    gs = d["groups"]
    prod = [g for g in gs if not g["outside"]]
    outs = [g for g in gs if g["outside"]]
    keep = [g for g in prod if importable(g)]
    skip = [g for g in prod if not importable(g)]
    out = ["# 進捗管理シート 取り込み前の確認", "",
           f"元表: `seed/{SHEET}`。案件の見出し {len(prod)}（**入れるのはタスクを持つ {len(keep)}**）・案件外の仕事 {len(outs)} グループ。", "",
           "## 入れる案件（→ `project` ＋ 実タスク）", "",
           "| 元表の行 | 案件名（原文） | 発売（年-月） | 段階 | タスク | 未着手 | 完了 | 年間プランの枠の候補（**結びつけていません**） |",
           "|---|---|---|---|---|---|---|---|"]
    cands = dict(plan_candidates(gs))
    for g in keep:
        st = [t["status"] for t in g["tasks"]]
        c = cands.get(g["name"], [])
        out.append(f"| {g['row']+1} | {g['name'][:34]} | {launch_ym(g) or '—'} | {stage_of(g)} | "
                   f"{len(g['tasks'])} | {st.count('未着手')} | {st.count('完了')} | "
                   f"{'; '.join(f'{m} {n}' for m, n in c) if c else '—'} |")
    out += ["", f"## 入れない見出し {len(skip)} 件（タスクが1件も無い＝発売済みの記録）", "",
            "要件定義書 8-3 ⑭「準備シート（開発中）は移す。発売済みは後段」。**消してはいません。**元表に残っています。", "",
            "| 元表の行 | 案件名 | 発売（年-月） |", "|---|---|---|"]
    for g in skip:
        out.append(f"| {g['row']+1} | {g['name'][:40]} | {launch_ym(g) or '—'} |")
    out += ["", "## 案件外の仕事（→ `work_item`・FR-47）", "", "| 元表の行 | グループ | 件数 |", "|---|---|---|"]
    for g in outs:
        out.append(f"| {g['row']+1} | {g['name'][:34]} | {len(g['tasks'])} |")
    allt = [t for g in gs for t in g["tasks"]]
    unk = sorted({t["role_raw"] for t in allt if t["role_raw"] and not t["role"]})
    bad = [t for t in allt if t["due_raw"] and not t["due"]]
    out += ["", "## 読み方", "",
            f"- タスク合計 {len(allt)}（未着手 {sum(1 for t in allt if t['status']=='未着手')}・"
            f"完了 {sum(1 for t in allt if t['status']=='完了')}・着手 {sum(1 for t in allt if t['status']=='着手')}）",
            "- **日付の年は元表の年度見出しから**（5〜12月→その年度、1〜4月→翌年）。推測ではなく表の構造です",
            f"- 期限が読めなかった行: {len(bad)}" + (f"（例: {bad[0]['due_raw'][:20]}）" if bad else ""),
            f"- 役割マスタに無い担当者: {', '.join(unk) if unk else '無し'}（該当 {sum(1 for t in allt if t['role_raw'] and not t['role'])} 行は担当 NULL。**発明しません**）",
            "- **段階は発売月から決めています**（今月より前なら発売済、それ以外は開発中）。画面で直せます",
            "- **雛形のタスクは展開しません。**実際に消化した完了と雛形の未着手が二重に並ぶためです",
            "- **年間プランの枠とは結びつけていません。**候補の列を見て、画面から「案件にする」で紐づけてください"]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--review", action="store_true")
    a = ap.parse_args()
    d = parse(read_rows())
    if a.review:
        print(review(d), end=""); return 0
    gs = d["groups"]
    print(f"案件 {sum(1 for g in gs if not g['outside'])}・案件外 {sum(1 for g in gs if g['outside'])}・"
          f"タスク {sum(len(g['tasks']) for g in gs)}")
    if a.dry_run:
        print("[dry-run] DB は触っていません"); return 0
    store.migrate()
    print("入れました:", apply())
    return 0


if __name__ == "__main__":
    sys.exit(main())
