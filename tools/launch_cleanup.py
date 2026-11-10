#!/usr/bin/env python3
"""
発売済みと発売後の整理（2026-11-09 修正依頼書 H04・H05・H07・ADR-128）。**既定は書かない。**表を出すだけ。

    sudo -u newproduct env ... python3 tools/launch_cleanup.py gates               # H04 発売済みのゲート補正（見るだけ）
    sudo -u newproduct env ... python3 tools/launch_cleanup.py gates --apply --by masateru
    sudo -u newproduct env ... python3 tools/launch_cleanup.py tasks               # H05 既存の発売後タスクの整理
    sudo -u newproduct env ... python3 tools/launch_cleanup.py tasks --apply --by masateru
    sudo -u newproduct env ... python3 tools/launch_cleanup.py templates           # H05 ひな形から発売後の行を外す
    sudo -u newproduct env ... python3 tools/launch_cleanup.py templates --apply --by masateru

## gates（H04）
ステージが発売済・追跡中・評価完了の案件で、適用する G0〜G5 のうち「通過」の記録が無いものに、
**移行補正**の記録（gate_review.kind='migration'）を入れる。承認者・承認日は推測しない（実施者と実施日時・理由を残す）。
既存の人の判定は消さない。簡易フローで対象外のゲートは対象外のまま。

## tasks（H05・H07）
新商品の案件に残っている**未完了の**発売後の行（生産の問題点を確認・モール出品・FBA化の検討・売上集計（2週間後））を
「対象外」にし、案件の履歴に理由を残す。完了の行・日程・作業時間は変えない。
- 発売済みの案件で、生産確認・出品・FBA が未完了だったものは、案件外の仕事へ移す（期限は元の行のまま）
- 開発中・保留の案件は、G5 を通したときに案件外の仕事として作られる（app/followup.py）
- 売上集計（2週間後）は案件外にしない（売上の週次集計と隔週ミーティングで扱う・依頼書 H05）
- 「新商品売上レポートに計上するよう設定」は残す（H06 が動くまで・2026-11-09 十文字さん）

## templates（H05）
使用中のひな形を写した下書きから、発売後の行（POST_ROWS）を外し、理由を付けて使い始める。
**既にある案件のタスクは変わらない**（ひな形の改訂は新しい案件だけに効く）。標準時間の空欄が残って使い始められない
開発タイプは、下書きのまま残して理由を出す。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import assign, followup, gate, seed, store, templates  # noqa: E402

REASON = "修正依頼書（2026-11-09）H05: 新商品の案件のタスクは発売まで。発売後の仕事は案件外の仕事・売上の集計で扱う"


def gates(apply: bool, by: str) -> int:
    rows = []
    for p in store.q("SELECT * FROM project WHERE stage IN ('発売済','追跡中','評価完了') ORDER BY launch_date, id"):
        p = dict(p)
        for gd in gate.defs():
            if gd["gate"] == "G6" or not gate.applies(gd, p.get("flow_type")):
                continue
            st, _r = gate.state_of(p["id"], gd["gate"])
            if st != "通過":
                rows.append((p, gd["gate"], st))
    print(f"# H04 発売済みのゲート補正（{'書く' if apply else '見るだけ'}）")
    print(f"対象の案件 {len({r[0]['id'] for r in rows})} 件・補正するゲート {len(rows)} 件\n")
    print("| 案件 | 名前 | ステージ | 発売日 | ゲート | いまの状態 |\n|---|---|---|---|---|---|")
    for p, g, st in rows:
        print(f"| {p['id']} | {p.get('internal_name') or ''} | {p['stage']} | {p.get('launch_date') or '空'} | {g} | {st} |")
    if not apply:
        return 0
    now = store.now_s()
    comment = "移行補正（修正依頼書 H04）: 発売済みの案件。実際の承認の記録ではありません（承認者・承認日は不明）"
    with store.tx() as c:
        for p, g, st in rows:
            c.execute("INSERT INTO gate_review (project_id,gate,result,approved_by,approved_role,approved_at,comment,"
                      "missing_items,kind) VALUES (?,?,?,?,?,?,?,?,'migration')",
                      (p["id"], g, "通過", by, "移行補正", now, comment, json.dumps([])))
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                      (p["id"], now, by, f"{g} 通過（移行補正）", f"補正前: {st}。{comment}"))
    store.audit(by, "gate.migration_fix", None, {"n": len(rows), "projects": sorted({r[0]["id"] for r in rows})})
    print(f"\n書きました: {len(rows)} 件")
    return 0


def tasks(apply: bool, by: str) -> int:
    rows = []
    for t in store.q("SELECT t.*, p.stage, p.internal_name, p.launch_date, p.flow_type FROM task t "
                     "JOIN project p ON p.id=t.project_id WHERE t.status NOT IN ('完了','対象外') ORDER BY p.id, t.seq"):
        k = followup.row_kind(t["title"])
        if k:
            rows.append((dict(t), k))
    print(f"# H05 既存の発売後タスクの整理（{'書く' if apply else '見るだけ'}）")
    print(f"対象外にする未完了の行 {len(rows)} 件\n")
    print("| 案件 | 名前 | ステージ | タスク | 行 | 期限 | 種類 | 移し先 |\n|---|---|---|---|---|---|---|---|")
    moves = {}
    for t, k in rows:
        launched = t["stage"] in gate.LAUNCHED
        if k == "weekly":
            dest = "売上の週次集計・隔週ミーティング（案件外にしない）"
        elif launched:
            dest = f"案件外の仕事（{followup.KINDS[k]['category']}）へ移す・期限そのまま"
            moves.setdefault((t["project_id"], k), t)
        else:
            dest = "G5 を通したときに案件外の仕事として作る"
        print(f"| {t['project_id']} | {t['internal_name'] or ''} | {t['stage']} | {t['id']} | {t['title']} | "
              f"{t['due_on'] or '空'} | {k} | {dest} |")
    if not apply:
        return 0
    now = store.now_s()
    made = 0
    with store.tx() as c:
        for t, k in rows:
            c.execute("UPDATE task SET status='対象外' WHERE id=? AND status NOT IN ('完了','対象外')", (t["id"],))
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                      (t["project_id"], now, by, "発売後のタスクを対象外にした",
                       f"{t['title']}（タスク {t['id']}・{t['status']}→対象外）。{REASON}"))
        for (pid, k), t in moves.items():
            if c.execute("SELECT 1 FROM work_item WHERE project_id=? AND origin=?", (pid, k)).fetchone():
                continue
            kk = followup.KINDS[k]
            wid = c.execute("INSERT INTO work_item (kind,title,category,role,assignee,due_on,status,created_at,project_id,origin) "
                            "VALUES ('案件外',?,?,?,?,?,'未着手',?,?,?)",
                            (kk["title"].format(name=t["internal_name"] or pid), kk["category"], kk["role"],
                             assign.for_role(kk["role"]), t["due_on"], now, pid, k)).lastrowid
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                      (pid, now, by, "発売後の仕事を作った",
                       f"{kk['category']}（案件外の仕事 {wid}・期限 {t['due_on'] or '空'}）。元のタスク {t['id']} から移した"))
            made += 1
    store.audit(by, "task.post_launch_retire", None, {"retired": len(rows), "work_items": made})
    print(f"\n書きました: 対象外 {len(rows)} 件・案件外の仕事 {made} 件")
    return 0


def tpls(apply: bool, by: str) -> int:
    print(f"# H05 ひな形から発売後の行を外す（{'書く' if apply else '見るだけ'}）\n")
    print("| 開発タイプ | 使用中の版 | 外す行 |\n|---|---|---|")
    plan = []
    for f in store.q("SELECT code, active_template_version FROM flow_type ORDER BY code"):
        rs = store.q("SELECT id, seq, title FROM task_template WHERE flow_type=? AND template_version=? ORDER BY seq",
                     (f["code"], f["active_template_version"]))
        drop = [r for r in rs if followup.row_kind(r["title"])]
        print(f"| {f['code']} | {f['active_template_version']} | " + ("、".join(r["title"] for r in drop) or "なし") + " |")
        if drop:
            plan.append((f["code"], [r["title"] for r in drop]))
    if not apply:
        return 0
    for code, titles in plan:
        try:
            templates.new_draft(code, by)
        except ValueError as e:
            print(f"- {code}: 下書きを作れませんでした（{e}）。とばします")
            continue
        v = templates._draft_v(code)
        for r in store.q("SELECT id, title FROM task_template WHERE flow_type=? AND template_version=?", (code, v)):
            if followup.row_kind(r["title"]):
                templates.delete_row(code, r["id"], by)
        try:
            templates.activate(code, by, REASON)
            print(f"- {code}: 版 {v} を使い始めました（{len(titles)} 行を外した）")
        except ValueError as e:
            print(f"- {code}: 版 {v} は下書きのまま（{e}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["gates", "tasks", "templates"])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--by", default="", help="実施者の利用者ID（--apply のとき必須）")
    a = ap.parse_args()
    if a.apply and not a.by:
        print("--apply には --by（実施者）が要ります", file=sys.stderr)
        return 2
    seed.run()
    return {"gates": gates, "tasks": tasks, "templates": tpls}[a.what](a.apply, a.by)


if __name__ == "__main__":
    sys.exit(main())
