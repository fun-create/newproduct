#!/usr/bin/env python3
"""
石原さんの追加修正依頼書（2026-11-10）の本番データの整理。**既定は書かない。**表を出すだけ。

    sudo -u newproduct env ... python3 tools/req1110.py duo        [--apply --by masateru]  # 2-1 DUO を完了・発売済
    sudo -u newproduct env ... python3 tools/req1110.py retire     [--apply --by masateru]  # 2-3 発売済み案件の残りを対象外
    sudo -u newproduct env ... python3 tools/req1110.py assign     [--apply --by masateru]  # 2-2 空の担当を既定で埋める
    sudo -u newproduct env ... python3 tools/req1110.py revenue    [--apply --by masateru]  # 4-1 売上計上を「含める・全額」に

順番は duo → retire → assign → revenue（依頼書 §5。DUO は「対象外」より先に「完了」）。書く前に deploy/backup.sh で控えを取る。

- duo: 案件「iPhone DUOスキンシール」の**未完了**のタスクを完了にする（既に完了の行の日付・記録はそのまま。
  対象外の行は対象外のまま＝発売後の仕事として案件外へ移す行）。適用する G0〜G5 のうち通過の記録が無いものに
  **移行補正**の記録を入れる（承認者・承認日時は推測しない。H04 と同じ）。そのあと G5 を通したときと同じ処理
  （ステージ 発売済・発売後の仕事・新商品の売上の未紐付け・CIP の確認）。どれも同じものを2つ作らない
- retire: ステージが発売済・追跡中・評価完了の案件（DUO を除く）の**未完了の案件タスク**を対象外に。案件外の仕事は触らない。
  完了の行・日付は変えない。消さない
- assign: app/assign.fill_unassigned（未完了・担当が空・中止でない案件）。人が入れた担当は触らない
- revenue: 売上計上が「含めない」で方式が空の案件（中止・ページリニューアルを除く）を「含める・全額」に。
  増分を選んだ案件は触らない
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import assign, gate, project, store  # noqa: E402

DUO = "2ad4"
WHY = "追加修正依頼書（2026-11-10 石原さん）"
OPEN = ("未着手", "着手", "保留")


def _rev(c, pid, by, what, detail):
    c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
              (pid, store.now_s(), by, what, detail[:1000]))


def duo(apply: bool, by: str) -> int:
    p = store.one("SELECT * FROM project WHERE id=?", (DUO,))
    if p is None or "DUO" not in (p["internal_name"] or ""):
        print("DUO の案件が見つかりません（id を確かめる）")
        return 1
    p = dict(p)
    ts = store.rows(store.q("SELECT id, seq, title, status FROM task WHERE project_id=? ORDER BY seq", (DUO,)))
    opn = [t for t in ts if t["status"] in OPEN]
    gs = []
    for gd in gate.defs():
        if gd["gate"] == "G6" or not gate.applies(gd, p.get("flow_type")):
            continue
        st, _ = gate.state_of(DUO, gd["gate"])
        if st != "通過":
            gs.append((gd["gate"], st))
    wi = store.rows(store.q("SELECT id, title, origin, status FROM work_item WHERE project_id=?", (DUO,)))
    print(f"# 2-1 DUO（{DUO} {p['internal_name']}・いま {p['stage']}・発売予定 {p['launch_date']}）"
          f"（{'書く' if apply else '見るだけ'}）")
    print(f"完了にするタスク {len(opn)} 件: " + "、".join(f"{t['seq']} {t['title']}" for t in opn))
    print("そのままにする: " + "、".join(f"{t['seq']} {t['title']}（{t['status']}）" for t in ts if t not in opn))
    print(f"移行補正するゲート {len(gs)} 件: " + "、".join(f"{g}（{s}）" for g, s in gs))
    print(f"いまある案件外の仕事 {len(wi)} 件: " + ("、".join(f"{w['title']}（{w['origin']}）" for w in wi) or "なし"))
    if not apply:
        return 0
    now = store.now_s()
    comment = f"移行補正（{WHY} 2-1）: DUO を発売済みにする。実際の承認の記録ではありません（承認者・承認日は不明）"
    with store.tx() as c:
        for t in opn:
            c.execute("UPDATE task SET status='完了', done_at=? WHERE id=? AND status IN (?,?,?)", (now, t["id"], *OPEN))
            _rev(c, DUO, by, "タスクを完了にした", f"{t['seq']} {t['title']}（{t['status']}→完了）。{WHY} 2-1: DUO は全タスク完了")
        for g, st in gs:
            c.execute("INSERT INTO gate_review (project_id,gate,result,approved_by,approved_role,approved_at,comment,"
                      "missing_items,kind) VALUES (?,?,?,?,?,?,?,?,'migration')",
                      (DUO, g, "通過", by, "移行補正", now, comment, json.dumps([])))
            _rev(c, DUO, by, f"{g} 通過（移行補正）", f"補正前: {st}。{comment}")
    out = gate._launched(dict(store.one("SELECT * FROM project WHERE id=?", (DUO,))), by)
    store.audit(by, "req1110.duo", DUO, {"done": [t["id"] for t in opn], "gates": [g for g, _ in gs],
                                         "stage_moved": out["stage_moved"]})
    print(f"\n書きました: 完了 {len(opn)} 件・移行補正 {len(gs)} 件・ステージ {'発売済へ' if out['stage_moved'] else '変えず'}")
    print("発売後の仕事: " + json.dumps(out["followup"], ensure_ascii=False)[:600])
    print("CIP の確認: " + out["links"]["cip"]["state"] + " " + out["links"]["cip"]["summary"])
    return 0


def retire(apply: bool, by: str) -> int:
    rows = store.rows(store.q(
        "SELECT t.id, t.seq, t.title, t.status, t.due_on, t.project_id, p.internal_name, p.stage FROM task t "
        "JOIN project p ON p.id=t.project_id WHERE p.stage IN ('発売済','追跡中','評価完了') AND p.id!=? "
        f"AND t.status IN ({','.join('?' * len(OPEN))}) ORDER BY p.id, t.seq", (DUO, *OPEN)))
    print(f"# 2-3 発売済み案件の残りのタスクを対象外に（{'書く' if apply else '見るだけ'}）: {len(rows)} 件")
    print("| 案件 | 名前 | タスク | 行 | いま | 期限 |\n|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['project_id']} | {r['internal_name']} | {r['id']} | {r['seq']} {r['title']} | {r['status']} | {r['due_on'] or '空'} |")
    if not apply:
        return 0
    with store.tx() as c:
        for r in rows:
            c.execute("UPDATE task SET status='対象外' WHERE id=? AND status IN (?,?,?)", (r["id"], *OPEN))
            _rev(c, r["project_id"], by, "タスクを対象外にした",
                 f"{r['seq']} {r['title']}（{r['status']}→対象外）。{WHY} 2-3: 発売済みの案件に残った未完了タスク")
    store.audit(by, "req1110.retire", None, {"tasks": [r["id"] for r in rows]})
    print(f"\n書きました: 対象外 {len(rows)} 件")
    return 0


def assign_(apply: bool, by: str) -> int:
    r = assign.fill_unassigned(by, apply=apply)
    print(f"# 2-2 空の担当を既定で埋める（{'書いた' if apply else '見るだけ'}）")
    print(f"案件タスク {r['task']} 件・案件外の仕事 {r['work_item']} 件（うち期限あり {r['with_due']} 件＝カレンダーに出る）")
    print("人ごと: " + "、".join(f"{u} {n}" for u, n in sorted(r["by_user"].items())))
    print("入れなかった（担当区分）: " + ("、".join(f"{k} {n}" for k, n in r["skipped"].items()) or "なし"))
    return 0


def revenue(apply: bool, by: str) -> int:
    rows = store.rows(store.q(
        "SELECT id, internal_name, stage, flow_type FROM project WHERE revenue_counted=0 AND revenue_basis IS NULL "
        "AND stage!='中止' AND COALESCE(flow_type,'') NOT IN (%s) ORDER BY stage, id"
        % ",".join("?" * len(project.REVENUE_OPT_OUT)), project.REVENUE_OPT_OUT))
    keep = store.rows(store.q("SELECT id, internal_name, stage, revenue_counted, revenue_basis FROM project "
                              "WHERE flow_type IN (%s) OR revenue_basis='増分'"
                              % ",".join("?" * len(project.REVENUE_OPT_OUT)), project.REVENUE_OPT_OUT))
    print(f"# 4-1 売上計上を「含める・全額」に（{'書く' if apply else '見るだけ'}）: {len(rows)} 件")
    for r in rows:
        print(f"- {r['id']} {r['internal_name']}（{r['stage']}・{r['flow_type'] or '開発タイプ空'}）")
    print("触らない: " + ("、".join(f"{r['id']} {r['internal_name']}（{'含めない' if not r['revenue_counted'] else r['revenue_basis']}）" for r in keep) or "なし"))
    if not apply:
        return 0
    with store.tx() as c:
        for r in rows:
            c.execute("UPDATE project SET revenue_counted=1, revenue_basis='全額', updated_at=?, updated_by=? "
                      "WHERE id=? AND revenue_counted=0 AND revenue_basis IS NULL", (store.now_s(), by, r["id"]))
            _rev(c, r["id"], by, "売上計上", f"含めない → 含める（全額）。{WHY} 4-1: 新商品売上の既定は全額")
    store.audit(by, "req1110.revenue", None, {"projects": [r["id"] for r in rows]})
    print(f"\n書きました: {len(rows)} 件")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("duo", "retire", "assign", "revenue"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--by", default="")
    a = ap.parse_args()
    if a.apply and not a.by:
        print("--by に実施者の user_id を入れてください")
        return 2
    return {"duo": duo, "retire": retire, "assign": assign_, "revenue": revenue}[a.what](a.apply, a.by or "-")


if __name__ == "__main__":
    sys.exit(main())
