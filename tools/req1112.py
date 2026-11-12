#!/usr/bin/env python3
"""
石原さんの希望（ChatWork FUN 商品開発 2026-11-12 11:34）の本番データの整理。**既定は書かない。**表を出すだけ。

    sudo -u newproduct env ... python3 tools/req1112.py launch    [--apply --by masateru]  # ビールジョッキ再販を発売済に
    sudo -u newproduct env ... python3 tools/req1112.py occasion  [--apply --by masateru] [--versions d542,9537]

- launch: 案件 0f53「ビールジョッキ再販」（発売 2026-05-01）を発売済に。適用する G0〜G5 のうち通過の記録が無いものに
  **移行補正**の記録を入れ（承認者・承認日は推測しない。11-10 の DUO と同じ）、G5 を通したときと同じ処理
  （ステージ・新商品の売上の未紐付け・発売後の仕事・CIP の確認）
- occasion: 年間プランの枠の「機会（なぜその月か）」を、発売月が 2026-10 までの枠で空にする（11月以降は石原さんが直す）。
  既定は策定中の版だけ。**承認済みの版は --versions に明示したときだけ**。案件の「機会」も、発売予定日が 10月までなら空に。
  消す前の値は project_revision と audit に残す（戻せるように）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import followup, gate, store  # noqa: E402

BEER = "0f53"
WHY = "石原さん 2026-11-12 ChatWork"
UNTIL = "2026-10"


def launch(apply: bool, by: str) -> int:
    p = store.one("SELECT * FROM project WHERE id=?", (BEER,))
    if p is None or "ジョッキ" not in (p["internal_name"] or ""):
        print("ビールジョッキ再販の案件が見つかりません")
        return 1
    p = dict(p)
    gs = []
    for gd in gate.defs():
        if gd["gate"] == "G6" or not gate.applies(gd, p.get("flow_type")):
            continue
        st, _ = gate.state_of(BEER, gd["gate"])
        if st != "通過":
            gs.append((gd["gate"], st))
    opn = store.val("SELECT COUNT(*) FROM task WHERE project_id=? AND status IN ('未着手','着手','保留')", (BEER,))
    print(f"# ビールジョッキ再販（{BEER}・いま {p['stage']}・発売 {p['launch_date']}・{p['flow_type']}）"
          f"（{'書く' if apply else '見るだけ'}）")
    print(f"未完了のタスク {opn} 件（触らない）")
    print(f"移行補正するゲート {len(gs)} 件: " + ("、".join(f"{g}（{s}）" for g, s in gs) or "なし"))
    print("発売後の仕事（作る予定）: " + ("、".join(f"{k}（{d}）" for k, d in followup.wanted(p)) or "なし"))
    if not apply:
        return 0
    now = store.now_s()
    comment = f"移行補正（{WHY}）: 発売済み（2026-05-01 発売）にする。実際の承認の記録ではありません（承認者・承認日は不明）"
    with store.tx() as c:
        for g, st in gs:
            c.execute("INSERT INTO gate_review (project_id,gate,result,approved_by,approved_role,approved_at,comment,"
                      "missing_items,kind) VALUES (?,?,?,?,?,?,?,?,'migration')",
                      (BEER, g, "通過", by, "移行補正", now, comment, json.dumps([])))
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                      (BEER, now, by, f"{g} 通過（移行補正）", f"補正前: {st}。{comment}"))
    out = gate._launched(dict(store.one("SELECT * FROM project WHERE id=?", (BEER,))), by)
    store.audit(by, "req1112.launch", BEER, {"gates": [g for g, _ in gs], "stage_moved": out["stage_moved"]})
    print(f"\n書きました: 移行補正 {len(gs)} 件・ステージ {'発売済へ' if out['stage_moved'] else '変えず'}")
    print("発売後の仕事: " + json.dumps(out["followup"], ensure_ascii=False)[:400])
    print("CIP の確認: " + out["links"]["cip"]["state"] + " " + out["links"]["cip"]["summary"])
    return 0


def occasion(apply: bool, by: str, versions: list[str] | None) -> int:
    vs = store.rows(store.q("SELECT id, label, state FROM plan_version ORDER BY created_at"))
    pick = [v for v in vs if (v["id"] in versions if versions else v["state"] == "策定中")]
    print(f"# 機会を空にする（発売月 {UNTIL} まで）（{'書く' if apply else '見るだけ'}）")
    rows = []
    for v in pick:
        rs = store.rows(store.q("SELECT id, launch_month, product, occasion, project_id FROM plan_slot WHERE version_id=? "
                                "AND launch_month<=? AND occasion IS NOT NULL AND occasion!='' ORDER BY launch_month",
                                (v["id"], UNTIL)))
        print(f"\n## 版 {v['id']}（{v['state']}）{v['label']}: {len(rs)} 枠")
        for r in rs:
            print(f"  {r['launch_month']} {r['product']}: 「{r['occasion']}」")
        rows += [(v["id"], r) for r in rs]
    ps = store.rows(store.q("SELECT id, internal_name, launch_date, occasion FROM project WHERE occasion IS NOT NULL "
                            "AND occasion!='' AND launch_date IS NOT NULL AND substr(launch_date,1,7)<=?", (UNTIL,)))
    print(f"\n## 案件の機会: {len(ps)} 件")
    for x in ps:
        print(f"  {x['id']} {x['internal_name']}（{x['launch_date']}）: 「{x['occasion']}」")
    skipped = [v["id"] for v in vs if v not in pick]
    if skipped:
        print(f"\n触らない版: {', '.join(skipped)}")
    if not apply:
        return 0
    now = store.now_s()
    with store.tx() as c:
        for vid, r in rows:
            c.execute("UPDATE plan_slot SET occasion=NULL, updated_at=?, updated_by=? WHERE id=?", (now, by, r["id"]))
        for x in ps:
            c.execute("UPDATE project SET occasion=NULL, updated_at=?, updated_by=? WHERE id=?", (now, by, x["id"]))
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                      (x["id"], now, by, "機会を空に", f"前: 「{x['occasion']}」。{WHY}（10月分までは未設定に）"))
    store.audit(by, "req1112.occasion", "", {"until": UNTIL, "slots": [{"id": r["id"], "version": vid, "was": r["occasion"]}
                                                                        for vid, r in rows],
                                              "projects": [{"id": x["id"], "was": x["occasion"]} for x in ps]})
    print(f"\n書きました: 枠 {len(rows)}・案件 {len(ps)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["launch", "occasion"])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--by", default="")
    ap.add_argument("--versions", default="")
    a = ap.parse_args()
    if a.apply and not a.by:
        print("--apply には --by が要ります")
        return 2
    if a.what == "launch":
        return launch(a.apply, a.by)
    return occasion(a.apply, a.by, [x for x in a.versions.split(",") if x] or None)


if __name__ == "__main__":
    sys.exit(main())
