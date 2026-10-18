#!/usr/bin/env python3
"""
進捗管理シート（Google スプレッドシート「●1.進捗管理（商品開発）」）→ アプリの同期（ADR-105・2026-10-18 十文字さん依頼）。

十文字さんの言葉: 「スプレッドシート管理からアプリに完全移行するまでは、スプレッドシートを確認してアプリ側をアップデートしてほしい」。
**正はシート（移行が終わるまで）。**アプリ側で人が直した値は上書きせず、衝突として出す。

    # 1) Mac で .xlsx を TSV に（シートは Google の書き出しで .xlsx にしたもの）
    python3 tools/xlsx_sheet.py 進捗管理.xlsx 全タスク一覧 ledger.tsv
    python3 tools/xlsx_sheet.py 進捗管理.xlsx 年間プラン2026年度 plan.tsv
    # 2) 差分を見る（何も書かない）
    python3 tools/sync_sheet.py ledger.tsv plan.tsv --sheet-date 2026-10-18
    # 3) 書く（名前が変わった案件は --rename で結ぶ）
    python3 tools/sync_sheet.py ledger.tsv plan.tsv --sheet-date 2026-10-18 --apply --rename '旧=新' ...

## 何を合わせるか
- 範囲は **2026年度の案件**と「商品発売以外のタスク」（案件外の仕事）。2025年度（発売済みの記録）は入れない（ADR-026 の範囲のまま）
- 案件: 名前で結ぶ（`--rename` で旧名と結んだら、アプリの名前と鍵も新しい名前にする）。無ければ作る
  - 発売予定日 ← 年間プランの「発売予定日」（商品名が結べたものだけ。**結べなければ入れない**）
  - ステージ ← 発売予定日（無ければ発売月）が今日より前なら 発売済、そうでなければ 開発中。**保留・中止・追跡中・評価完了は触らない**
- タスク: （案件, タスク名, 同じ名前の何番目）で結ぶ。進捗・開始・期限・ロール・標準時間をシートの値にする。シートに増えた行は足す
  - **アプリ側で直された行は上書きしない**（前回の同期で書いた値の指紋と違う＝人が直した）。衝突として出す
  - シートから消えた行は**消さずに「対象外」にする**（前回シートから取り込んだ行だけ。アプリで足した行・完了済みは触らない。元に戻せる）
- アプリにだけある案件は触らない（一覧に出す。中止・保留にするかは人が決める）
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import project as project_m   # noqa: E402
from app import store                  # noqa: E402

SHEET = "01_進捗管理商品開発__全タスク一覧.tsv"         # 鍵は前回の取り込みと同じ（source_sheet）
STATUS = {"未着手": "未着手", "完了": "完了", "取組み中": "着手"}
ROLE = {"管理者": "admin", "メンバー": "member", "商品開発部": "devdept",
        "Webマーケ": "webmkt", "生産部": "prod", "パートさん": "part", "パート": "part"}
TYPES = {"ページリニューアル": "pagerenew", "既成デザイン": "readymade", "フリーカット": "freecut",
         "スキンシール": "newmodel", "名入れ": "meire", "Web deco": "webdeco", "Webdeco": "webdeco"}
OUTSIDE = "商品発売以外のタスク"
FY = 2026
KEEP_STAGES = ("保留", "中止", "追跡中", "評価完了")


def _g(r, j):
    return r[j].strip() if len(r) > j else ""


def serial_date(s: str) -> str | None:
    """Excel の通し番号（46294 / 46294.0）→ YYYY-MM-DD。それ以外は None（推測しない）。"""
    if not re.fullmatch(r"\d{5}(\.0+)?", s or ""):
        return None
    n = int(float(s))
    if not 40000 <= n <= 60000:
        return None
    return (dt.date(1899, 12, 30) + dt.timedelta(days=n)).isoformat()


def _hours(s: str):
    return float(s) if re.fullmatch(r"\d+(\.\d+)?", s or "") else None


def norm(s: str) -> str:
    s = re.sub(r"[※＊].*$", "", s or "")
    return re.sub(r"[\s　・/／、,（）()\[\]【】「」]", "", s).lower()


# ── シートを読む ──────────────────────────────────────────
def parse_ledger(rows) -> list[dict]:
    """全タスク一覧 → 案件（グループ）とタスク。見出しの年度・発売月の下にあるものを拾う。"""
    groups, fy, month, outside, cur = [], None, None, False, None
    by_name: dict[str, dict] = {}
    for i, r in enumerate(rows, start=1):
        c0, c1 = _g(r, 0), _g(r, 1)
        m = re.match(r"(20\d\d)年度", c1)
        if m and not c0:
            fy, month, outside, cur = int(m.group(1)), None, False, None
            continue
        m = re.fullmatch(r"(\d{1,2})月発売", c1)
        if m and not c0:
            month, cur = int(m.group(1)), None
            continue
        if c1 == OUTSIDE and not c0:
            outside, month, cur = True, None, None
            continue
        is_task = bool(c0) and bool(c1) and (bool(_g(r, 2)) or bool(_g(r, 3)))
        if c1 and not c0 and not _g(r, 2) and not _g(r, 3) and not is_task:
            if c1.startswith("この行の上に"):
                cur = None
                continue
            key = (fy, outside, c1)
            cur = by_name.get(key)
            if cur is None:
                cur = {"name": c1, "row": i, "fy": fy, "month": month, "outside": outside, "tasks": []}
                by_name[key] = cur
                groups.append(cur)
            continue
        if is_task:
            g = by_name.get((fy, outside, c0))
            if g is None:
                g = {"name": c0, "row": i, "fy": fy, "month": month, "outside": outside, "tasks": []}
                by_name[(fy, outside, c0)] = g
                groups.append(g)
            role_raw = _g(r, 2)
            g["tasks"].append({"row": i, "title": c1, "role_raw": role_raw, "role": ROLE.get(role_raw),
                               "status": STATUS.get(_g(r, 3), "未着手"), "status_raw": _g(r, 3),
                               "start": serial_date(_g(r, 4)), "due": serial_date(_g(r, 5)),
                               "hours": _hours(_g(r, 6))})
    for g in groups:                                     # 同じ名前の何番目か（鍵）
        seen: dict[str, int] = {}
        for t in g["tasks"]:
            seen[t["title"]] = seen.get(t["title"], 0) + 1
            t["nth"] = seen[t["title"]]
    return [g for g in groups if g["fy"] == FY or g["outside"]]


def parse_plan(rows) -> list[dict]:
    """年間プラン2026年度 → 商品名・タイプ・発売予定日（タイプの語を起点に右へ読む）。"""
    out, year, month = [], None, None
    for i, r in enumerate(rows, start=1):
        for c in r[:3]:
            m = re.search(r"(20\d\d)年\\n(\d{1,2})月", c or "")
            if m:
                year, month = int(m.group(1)), int(m.group(2))
        t = next((j for j, c in enumerate(r) if c.strip() in TYPES), None)
        if t is None or t < 2:
            continue
        prod = _g(r, t - 2) if _g(r, t - 1) else _g(r, t - 1)
        if not prod:
            continue
        out.append({"row": i, "product": prod, "type": r[t].strip(), "flow": TYPES[r[t].strip()],
                    "setup_due": serial_date(_g(r, t + 2)), "launch": serial_date(_g(r, t + 4)),
                    "launched_flag": _g(r, t + 5) == "TRUE", "year": year, "month": month})
    return out


def plan_for(name: str, plan: list[dict]) -> dict | None:
    """案件名 → 年間プランの行。完全一致 → 片方が片方を含む → 「A/B」と2つ並べた行の片方 → 先頭6文字。
    **結べなければ None**（発売予定日は入れない）。"""
    n = norm(name)
    for how in ("eq", "in", "part", "head"):
        for p in plan:
            q = norm(p["product"])
            if how == "part":
                parts = [norm(x) for x in re.split(r"[/／]", re.sub(r"^LOVOT", "", p["product"]))]
                if len(parts) > 1 and any(len(x) >= 4 and x in n for x in parts):
                    return p
                continue
            if (how == "eq" and q == n) or (how == "in" and q and n and (q in n or n in q)) \
                    or (how == "head" and len(q) >= 6 and len(n) >= 6 and q[:6] == n[:6]):
                return p
    return None


# ── アプリと比べる ─────────────────────────────────────────
def _digest(*vals) -> str:
    return hashlib.sha256(json.dumps(vals, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def _task_vals(t) -> tuple:
    return (t["status"], t["start_on"], t["due_on"], t["role"], t["hours"])


def stage_for(launch: str | None, month: int | None, today: dt.date) -> str:
    if launch:
        return "発売済" if launch < today.isoformat() else "開発中"
    if month:
        y = FY if month >= 5 else FY + 1
        return "発売済" if f"{y:04d}-{month:02d}" < today.isoformat()[:7] else "開発中"
    return "開発中"


def plan_sync(groups, plan, renames: dict[str, str], today: dt.date) -> dict:
    apps = {r["source_key"]: dict(r) for r in store.q(
        "SELECT * FROM project WHERE source_sheet=? AND source_key IS NOT NULL", (SHEET,))}
    rev = {new: old for old, new in renames.items()}
    out = {"projects": [], "new_projects": [], "app_only": [], "work": [], "warnings": []}
    used = set()
    for g in groups:
        if g["outside"]:
            continue
        old = rev.get(g["name"], g["name"])
        p = apps.get(old)
        if p is None and old != g["name"]:
            old, p = g["name"], apps.get(g["name"])     # 付け替え済み（2回目の --rename）でも新しい名前で結ぶ
        pl = plan_for(g["name"], plan)
        launch = pl["launch"] if pl else None
        stage = stage_for(launch, g["month"], today)
        entry = {"group": g, "plan": pl, "launch": launch, "stage": stage, "project": p, "renamed_from": old if old != g["name"] else None}
        if p is None:
            out["new_projects"].append(entry)
            continue
        used.add(old)
        out["projects"].append(entry)
    for k, p in apps.items():
        if k not in used and p["stage"] not in ("中止",):
            out["app_only"].append(p)
    return out


def task_changes(pid: str, g: dict) -> dict:
    tasks = store.rows(store.q("SELECT * FROM task WHERE project_id=? ORDER BY seq, id", (pid,)))
    synced = {r["local_id"]: dict(r) for r in store.q("SELECT * FROM sheet_sync WHERE kind='task'")}
    by_key: dict[tuple, dict] = {}
    seen: dict[str, int] = {}
    for t in tasks:
        seen[t["title"]] = seen.get(t["title"], 0) + 1
        by_key[(t["title"], seen[t["title"]])] = t
    upd, add, conflict, same = [], [], [], 0
    matched = set()
    for st in g["tasks"]:
        t = by_key.get((st["title"], st["nth"]))
        new_vals = (st["status"], st["start"], st["due"], st["role"], st["hours"])
        if t is None:
            add.append(st)
            continue
        matched.add(t["id"])
        cur = _task_vals(t)
        if cur == new_vals:
            same += 1
            continue
        s = synced.get(str(t["id"]))
        if s is not None and s["digest"] != _digest(*cur):
            conflict.append((t, st))                   # 前回の同期の後にアプリで直された
            continue
        upd.append((t, st))
    gone = [t for t in tasks if t["id"] not in matched and t.get("source_row") is not None]
    return {"update": upd, "add": add, "conflict": conflict, "same": same, "gone": gone}


def work_changes(groups) -> dict:
    items = store.rows(store.q("SELECT * FROM work_item WHERE source_sheet=?", (SHEET,)))
    synced = {r["local_id"]: dict(r) for r in store.q("SELECT * FROM sheet_sync WHERE kind='work_item'")}
    by_key: dict[tuple, dict] = {}
    seen: dict[tuple, int] = {}
    for w in items:
        k = (w["category"], w["title"])
        seen[k] = seen.get(k, 0) + 1
        by_key[(w["category"], w["title"], seen[k])] = w
    upd, add, conflict, same = [], [], [], 0
    for g in groups:
        if not g["outside"]:
            continue
        for st in g["tasks"]:
            w = by_key.get((g["name"], st["title"], st["nth"]))
            new_vals = (st["status"], st["start"], st["due"], st["role"], st["hours"])
            if w is None:
                add.append((g["name"], st))
                continue
            cur = (w["status"], w["start_on"], w["due_on"], w["role"], w["hours"])
            if cur == new_vals:
                same += 1
                continue
            s = synced.get(str(w["id"]))
            if s is not None and s["digest"] != _digest(*cur):
                conflict.append((w, st))
                continue
            upd.append((w, st))
    return {"update": upd, "add": add, "conflict": conflict, "same": same}


# ── 書く ────────────────────────────────────────────────
def _set_task(c, tid, st, now):
    done = (st["due"] or now) if st["status"] == "完了" else None
    c.execute("UPDATE task SET status=?, start_on=?, due_on=?, role=?, hours=?, "
              "done_at=CASE WHEN ?='完了' THEN COALESCE(done_at, ?) ELSE NULL END WHERE id=?",
              (st["status"], st["start"], st["due"], st["role"], st["hours"], st["status"], done, tid))
    c.execute("INSERT INTO sheet_sync (kind,local_id,sheet_key,digest,synced_at) VALUES ('task',?,?,?,?) "
              "ON CONFLICT(kind,local_id) DO UPDATE SET sheet_key=excluded.sheet_key, digest=excluded.digest, synced_at=excluded.synced_at",
              (str(tid), f"{st['title']}#{st['nth']}", _digest(st["status"], st["start"], st["due"], st["role"], st["hours"]), now))


def apply(groups, plan, renames, today, sheet_date, user_id="sync") -> dict:
    ps = plan_sync(groups, plan, renames, today)
    now = store.now_s()
    n = {"projects_updated": 0, "projects_created": 0, "renamed": 0, "tasks_updated": 0, "tasks_added": 0,
         "conflicts": 0, "work_updated": 0, "work_added": 0, "tasks_retired": 0}
    for e in ps["projects"]:
        p, g = e["project"], e["group"]
        sets, vals = [], []
        if e["renamed_from"]:
            sets += ["internal_name=?", "source_key=?"]
            vals += [g["name"], g["name"]]
            n["renamed"] += 1
        if e["launch"] and e["launch"] != p["launch_date"]:
            sets.append("launch_date=?")
            vals.append(e["launch"])
        if p["stage"] not in KEEP_STAGES and e["stage"] != p["stage"]:
            sets.append("stage=?")
            vals.append(e["stage"])
        with store.tx() as c:
            if sets:
                c.execute(f"UPDATE project SET {','.join(sets)}, updated_at=?, updated_by=? WHERE id=?", (*vals, now, user_id, p["id"]))
                c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what) VALUES (?,?,?,?)",
                          (p["id"], now, user_id, f"進捗管理シート（{sheet_date}）と同期: " + "・".join(s.split("=")[0] for s in sets)))
                n["projects_updated"] += 1
            ch = task_changes(p["id"], g)
            for t, st in ch["update"]:
                _set_task(c, t["id"], st, now)
                n["tasks_updated"] += 1
            seq = c.execute("SELECT COALESCE(MAX(seq),0) FROM task WHERE project_id=?", (p["id"],)).fetchone()[0]
            for st in ch["add"]:
                seq += 1
                tid = c.execute("INSERT INTO task (project_id,seq,title,role,start_on,due_on,hours,status,created_at,kind,source_row) "
                                "VALUES (?,?,?,?,?,?,?,?,?,'実作業',?)",
                                (p["id"], seq, st["title"], st["role"], st["start"], st["due"], st["hours"], st["status"], now, st["row"])).lastrowid
                _set_task(c, tid, st, now)
                n["tasks_added"] += 1
            n["conflicts"] += len(ch["conflict"])
            for t in ch["gone"]:
                if t["status"] in ("完了", "対象外"):
                    continue
                c.execute("UPDATE task SET status='対象外' WHERE id=?", (t["id"],))
                c.execute("INSERT INTO sheet_sync (kind,local_id,sheet_key,digest,synced_at) VALUES ('task',?,?,?,?) "
                          "ON CONFLICT(kind,local_id) DO UPDATE SET digest=excluded.digest, synced_at=excluded.synced_at",
                          (str(t["id"]), "（シートに無い）",
                           _digest("対象外", t["start_on"], t["due_on"], t["role"], t["hours"]), now))
                n["tasks_retired"] += 1
    for e in ps["new_projects"]:
        g = e["group"]
        if not g["tasks"]:
            continue                                   # タスクの無い見出しは入れない（前回と同じ決まり）
        r = project_m.create(user_id, expand=False, internal_name=g["name"], stage=e["stage"],
                             flow_type=(e["plan"] or {}).get("flow"), launch_date=e["launch"],
                             summary=f"進捗管理シート（{sheet_date}）から同期で追加")
        pid = r["id"]
        with store.tx() as c:
            c.execute("UPDATE project SET source_sheet=?,source_key=?,source_row=? WHERE id=?", (SHEET, g["name"], g["row"], pid))
            for seq, st in enumerate(g["tasks"], 1):
                tid = c.execute("INSERT INTO task (project_id,seq,title,role,start_on,due_on,hours,status,created_at,kind,source_row) "
                                "VALUES (?,?,?,?,?,?,?,?,?,'実作業',?)",
                                (pid, seq, st["title"], st["role"], st["start"], st["due"], st["hours"], st["status"], now, st["row"])).lastrowid
                _set_task(c, tid, st, now)
                n["tasks_added"] += 1
        n["projects_created"] += 1
    wc = work_changes(groups)
    with store.tx() as c:
        for w, st in wc["update"]:
            c.execute("UPDATE work_item SET status=?, start_on=?, due_on=?, role=?, hours=?, "
                      "done_at=CASE WHEN ?='完了' THEN COALESCE(done_at, ?) ELSE NULL END WHERE id=?",
                      (st["status"], st["start"], st["due"], st["role"], st["hours"], st["status"], st["due"] or now, w["id"]))
            c.execute("INSERT INTO sheet_sync (kind,local_id,sheet_key,digest,synced_at) VALUES ('work_item',?,?,?,?) "
                      "ON CONFLICT(kind,local_id) DO UPDATE SET digest=excluded.digest, synced_at=excluded.synced_at",
                      (str(w["id"]), f"{w['category']}#{st['title']}#{st['nth']}",
                       _digest(st["status"], st["start"], st["due"], st["role"], st["hours"]), now))
            n["work_updated"] += 1
        for cat, st in wc["add"]:
            wid = c.execute("INSERT INTO work_item (kind,title,category,role,start_on,due_on,hours,status,created_at,source_sheet,source_key,source_row) "
                            "VALUES ('案件外',?,?,?,?,?,?,?,?,?,?,?)",
                            (st["title"], cat, st["role"], st["start"], st["due"], st["hours"], st["status"], now, SHEET,
                             f"{cat}#{st['title']}#{st['nth']}", st["row"])).lastrowid
            c.execute("INSERT INTO sheet_sync (kind,local_id,sheet_key,digest,synced_at) VALUES ('work_item',?,?,?,?)",
                      (str(wid), f"{cat}#{st['title']}#{st['nth']}", _digest(st["status"], st["start"], st["due"], st["role"], st["hours"]), now))
            n["work_added"] += 1
        n["conflicts"] += len(wc["conflict"])
        c.execute("INSERT INTO sheet_sync_run (sheet_date,applied_by,applied_at,summary) VALUES (?,?,?,?)",
                  (sheet_date, user_id, now, json.dumps(n, ensure_ascii=False)))
    store.audit(user_id, "sheet.sync", sheet_date, n)
    return n


# ── 報告 ────────────────────────────────────────────────
def report(groups, plan, renames, today) -> str:
    ps = plan_sync(groups, plan, renames, today)
    L = [f"# 進捗管理シートとアプリの差（{today}）", ""]
    L.append(f"## 案件（シートの2026年度 {sum(1 for g in groups if not g['outside'])} 件）")
    tot = {"update": 0, "add": 0, "conflict": 0, "same": 0, "gone": 0}
    for e in ps["projects"]:
        p, g = e["project"], e["group"]
        ch = task_changes(p["id"], g)
        for k in tot:
            tot[k] += ch[k] if k == "same" else len(ch[k])
        diffs = []
        if e["renamed_from"]:
            diffs.append(f"名前 {e['renamed_from']} → {g['name']}")
        if e["launch"] and e["launch"] != p["launch_date"]:
            diffs.append(f"発売予定日 {p['launch_date'] or '空'} → {e['launch']}")
        if not e["plan"]:
            diffs.append("年間プランの行と結べない（発売予定日は入れない）")
        if p["stage"] not in KEEP_STAGES and e["stage"] != p["stage"]:
            diffs.append(f"ステージ {p['stage']} → {e['stage']}")
        tk = f"タスク 直す {len(ch['update'])}・足す {len(ch['add'])}・衝突 {len(ch['conflict'])}・同じ {ch['same']}・シートに無い {len(ch['gone'])}"
        L.append(f"- {p['id']} {g['name']}（{g['month']}月発売）: " + ("／".join(diffs) + "／" if diffs else "") + tk)
        for t, st in ch["update"][:4]:
            L.append(f"    - 直す: {t['title'][:30]}  {t['status']}→{st['status']}  期限 {t['due_on'] or '空'}→{st['due'] or '空'}")
        for t, st in ch["conflict"]:
            L.append(f"    - ★衝突（アプリで直された）: {t['title'][:30]}  アプリ {t['status']}／シート {st['status']}")
        for t in ch["gone"][:3]:
            L.append(f"    - シートに無い: {t['title'][:40]}（{t['status']}" + ("→対象外にする）" if t["status"] not in ("完了", "対象外") else "・そのまま）"))
    L += ["", f"## シートにだけある案件（{len(ps['new_projects'])} 件・タスクのあるものを作る）"]
    for e in ps["new_projects"]:
        g = e["group"]
        L.append(f"- {g['name']}（{g['month']}月発売・タスク {len(g['tasks'])}・発売予定日 {e['launch'] or '結べない'}・{e['stage']}）"
                 + ("" if g["tasks"] else "　※タスクが無いので作らない"))
    L += ["", f"## アプリにだけある案件（{len(ps['app_only'])} 件・触らない）"]
    for p in ps["app_only"]:
        L.append(f"- {p['id']} {p['internal_name']}（{p['stage']}）")
    wc = work_changes(groups)
    L += ["", f"## 案件外の仕事: 直す {len(wc['update'])}・足す {len(wc['add'])}・衝突 {len(wc['conflict'])}・同じ {wc['same']}"]
    L += ["", f"## タスクの合計: 直す {tot['update']}・足す {tot['add']}・衝突 {tot['conflict']}・同じ {tot['same']}・シートに無い {tot['gone']}"]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ledger")
    ap.add_argument("plan")
    ap.add_argument("--sheet-date", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rename", action="append", default=[], help="旧=新（シートで名前が変わった案件）")
    ap.add_argument("--hold", action="append", default=[], help="シートから外れた案件の ID。保留にする（理由 sheet_removed）")
    ap.add_argument("--today")
    a = ap.parse_args()
    store.migrate()
    rd = lambda p: list(csv.reader(open(p, encoding="utf-8"), delimiter="\t"))   # noqa: E731
    groups, plan = parse_ledger(rd(a.ledger)), parse_plan(rd(a.plan))
    renames = dict(x.split("=", 1) for x in a.rename)
    today = dt.date.fromisoformat(a.today) if a.today else store.today()
    if a.apply:
        print(json.dumps(apply(groups, plan, renames, today, a.sheet_date), ensure_ascii=False))
        from app import seed
        seed.seed_masters()                            # 保留の理由（sheet_removed）を入れておく
        for pid in a.hold:
            r = project_m.move_stage(pid, "hold", "sync", "sheet_removed", f"進捗管理シート（{a.sheet_date}）に無い")
            print(f"保留: {pid} → {r['stage']}")
        return 0
    print(report(groups, plan, renames, today))
    return 0


if __name__ == "__main__":
    sys.exit(main())
