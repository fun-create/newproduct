#!/usr/bin/env python3
"""
石原さんの追加修正依頼書（2026-11-16）R04・R03 の本番データの整理。**既定は書かない。**表を出すだけ。

    # R04: 2025年度に発売した案件を進捗管理シートから入れる
    sudo -u newproduct env ... python3 tools/migrate_r04.py r04 /tmp/ledger.tsv --sheet-date 2026-11-19 [--apply --by masateru]
    # R03: 目印チャーム（新規分）を作ってリニューアル分から11件を移す・国旗ピンズを長方形ピンズへ
    sudo -u newproduct env ... python3 tools/migrate_r04.py r03 [--apply --by masateru]

決めたこと（照合表 `docs/migration/R04_R03_照合表_2026-11-19.xlsx`・石原さん 11-19 10:59 の回答・十文字さん 11-19 の選択・ADR-159）:
- R04 の案件は**発売済**で入れ、適用する G0〜G5 に**移行補正**の記録を入れる（承認者・承認日は推測しない）。
  タスクはシートのまま（完了は日付ごと・未完了は対象外）。テンプレートは展開しない。開発タイプは空のまま
- **G5 を通したときの処理は呼ばない**（発売後の仕事・CIP の確認・通知を起こさない）。新商品の売上は
  ラベルのある3件だけ登録済みにし、ほかは登録待ち（＝未紐付け。ラベルは杉浦さんが新商品マスタに足す）
- 【改修】バッグ・キャンバス、在庫処分、社内システムは入れない。スタンプ 浸透印は 1×1cm追加と1案件（発売日 2026-03-03）
- シートに無い缶バッジマシン再販・国旗シール デザインリニューアルは、タスクの無い発売済の案件として作る
- 年間プランは変えない（R03 の新規分だけ、承認済みの版の空いた枠 c008 と結ぶ）
- 何度流しても増えない（同じ鍵の案件があれば飛ばす）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import gate, newprod, plan, project as project_m, seed, store  # noqa: E402
from tools import sync_sheet as ss  # noqa: E402

SRC = "R04_進捗管理_2025年度"
WHY = "石原さんの追加修正依頼書（2026-11-16）R04・11-19 の回答"
GATE_NOTE = ("移行補正（{why}）: 2025年度に発売済みの案件。実際の承認の記録ではありません（承認者・承認日は不明）")

# シートの案件名 → (NP の名前, 発売日, 週次レポートの商品ラベル)。発売日は新商品売上（past_product）の確定値
ADD = {
    "冷感タオル　Mサイズ": ("冷感タオル Mサイズ", "2025-07-10", []),
    "ハンディファン": ("ハンディファン", "2025-08-07", []),
    "リルハイブリッド3.0 スキンシール": ("リルハイブリッド3.0 スキンシール", "2025-08-19", []),
    "シルエットステッカー（テニス男女）": ("シルエットステッカー（テニス男女）", "2025-08-29", []),
    "Ploom AURA スキンシール": ("Ploom AURA スキンシール", "2025-09-12", []),
    "アクリル時計": ("アクリル時計", "2025-10-03", []),
    "glo HILO スキンシール": ("glo HILO スキンシール", "2025-10-10", []),
    "フレークシール（フリーカット）": ("フレークシール（フリーカット）", "2025-10-21", []),
    "iPhone17シリーズ スキンシール": ("iPhone17シリーズ スキンシール", "2025-10-21", []),
    "ミニトート": ("ミニトート", "2025-11-05", []),
    "ブランケットの素材変更": ("ブランケットの素材変更", "2025-11-14", []),
    "NFCカード": ("NFCカード", "2025-12-12", []),
    "等身大タオル": ("等身大タオル", "2025-12-23", []),
    "switch2 スキンシール": ("switch2 スキンシール", "2026-01-20", []),
    "Air Pods Pro3スキンシール": ("Air Pods Pro3スキンシール", "2026-01-22", []),
    "お名前シール 衣類用": ("お名前シール 衣類用", "2026-02-20", ["Web deco お名前シール【ノンアイロン】【衣類用】"]),
    "嵐うちわキット＆デコプリシール": ("嵐うちわキット＆デコプリシール", "2026-02-27", []),
    "ブロックメモ": ("ブロックメモ", "2026-03-17", ["Web deco ブロックメモ【三面印刷】"]),
    "等身大アクスタ": ("等身大アクスタ", "2026-03-19", ["等身大アクリルスタンド"]),
    "嵐スケスケ衣装アクスタ/アクキー": ("嵐スケスケ衣装アクスタ/アクキー", "2026-03-24", []),
    "嵐スケスケ衣装スタンプ浸透印": ("嵐スケスケ衣装スタンプ浸透印", "2026-03-24", []),
    "Ploom CUBE スキンシール": ("Ploom CUBE スキンシール", "2026-03-27", []),
    "iPhone17e スキンシール": ("iPhone17e スキンシール", "2026-03-27", []),
    "嵐ピンクスーツ アクスタ/アクキー/スタンプ浸透印": ("嵐ピンクスーツ アクスタ/アクキー/スタンプ浸透印", "2026-04-07", []),
    "PS5 Slim デジタルエディション /PS5 Pro スキンシール": ("PS5 Slim デジタルエディション /PS5 Pro スキンシール", "2026-04-14", []),
}
# 2つのシートの案件を1つに（石原さん 11-19: 1×1cm追加の発売日で1案件・アクスタ スタンプとは結ばない）
MERGE = {"スタンプ 浸透印（1×1cm追加）": (["スタンプ 浸透印", "スタンプ 浸透印　1×1cm追加"], "2026-03-03", [])}
# シートに無いが新商品売上にある（石原さん 11-19: 発売済みとして作る）
EXTRA = {"缶バッジマシン再販": "2025-05-19", "国旗シール デザインリニューアル": "2025-08-25"}
SKIP = {"夫婦食器の在庫処分": "在庫処分（新商品ではない）", "【改修】バッグ": "石原さん 11-19: 何もしない",
        "【改修】キャンバス": "石原さん 11-19: 何もしない", "【システム】画像送信と注文の一元化": "社内システム"}


def _groups(ledger: str) -> dict[str, dict]:
    with open(ledger, encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    old = ss.FY
    ss.FY = 2025
    try:
        gs = [g for g in ss.parse_ledger(rows) if g["fy"] == 2025 and not g["outside"] and g["tasks"]]
    finally:
        ss.FY = old
    return {g["name"]: g for g in gs}


def plan_r04(ledger: str) -> dict:
    gs = _groups(ledger)
    known = set(ADD) | set(SKIP) | {n for ns, _l, _b in MERGE.values() for n in ns}
    unknown = sorted(set(gs) - known)
    missing = sorted(known - set(gs))
    items = []
    for sname, (name, ld, labels) in ADD.items():
        if sname in gs:
            items.append({"name": name, "launch": ld, "labels": labels, "tasks": gs[sname]["tasks"], "sheet": [sname],
                          "row": gs[sname]["row"]})
    for name, (snames, ld, labels) in MERGE.items():
        ts = [t for s in snames if s in gs for t in gs[s]["tasks"]]
        items.append({"name": name, "launch": ld, "labels": labels, "tasks": ts, "sheet": snames,
                      "row": gs[snames[0]]["row"] if snames[0] in gs else None})
    for name, ld in EXTRA.items():
        items.append({"name": name, "launch": ld, "labels": [], "tasks": [], "sheet": [], "row": None})
    have = {r["source_key"]: r["id"] for r in store.q("SELECT id, source_key FROM project WHERE source_sheet=?", (SRC,))}
    for it in items:
        it["exists"] = have.get(it["name"])
    return {"items": items, "unknown": unknown, "missing": missing}


def _migration_gates(c, pid: str, flow_type, by: str, now: str, why: str) -> int:
    n = 0
    for gd in gate.defs():
        if gd["gate"] == "G6" or not gate.applies(gd, flow_type):
            continue
        prev = c.execute("SELECT result FROM gate_review WHERE project_id=? AND gate=? ORDER BY id DESC LIMIT 1",
                         (pid, gd["gate"])).fetchone()
        if prev and prev["result"] == "通過":
            continue
        note = GATE_NOTE.format(why=why)
        c.execute("INSERT INTO gate_review (project_id,gate,result,approved_by,approved_role,approved_at,comment,"
                  "missing_items,kind) VALUES (?,?,?,?,?,?,?,?,'migration')",
                  (pid, gd["gate"], "通過", by, "移行補正", now, note, json.dumps([])))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                  (pid, now, by, f"{gd['gate']} 通過（移行補正）", f"補正前: {prev['result'] if prev else '未'}。{note}"))
        n += 1
    return n


def r04(ledger: str, sheet_date: str, apply: bool, by: str) -> int:
    pl = plan_r04(ledger)
    print(f"# R04 2025年度の発売済み案件（{'書く' if apply else '見るだけ'}・シート {sheet_date}）\n")
    if pl["unknown"] or pl["missing"]:
        print("★シートの案件が照合表と合いません。止めます")
        for n in pl["unknown"]:
            print(f"  - 照合表に無い: {n}")
        for n in pl["missing"]:
            print(f"  - シートに無い: {n}")
        return 1
    print("| 案件 | 発売日 | タスク（完了／対象外にする） | ラベル | 状態 |\n|---|---|---|---|---|")
    for it in pl["items"]:
        done = sum(1 for t in it["tasks"] if t["status"] == "完了")
        print(f"| {it['name']} | {it['launch']} | {len(it['tasks'])}（{done}／{len(it['tasks']) - done}） | "
              f"{'、'.join(it['labels']) or '無し（登録待ち）'} | {'作成済み ' + it['exists'] + '（飛ばす）' if it['exists'] else '作る'} |")
    print(f"\n入れない: " + "、".join(f"{k}（{v}）" for k, v in SKIP.items()))
    todo = [it for it in pl["items"] if not it["exists"]]
    print(f"作る案件 {len(todo)} 件・タスク {sum(len(it['tasks']) for it in todo)} 件")
    if not apply:
        return 0
    made = []
    for it in todo:
        why = f"{WHY}・進捗管理シート {sheet_date}"
        pid = project_m.create(by, expand=False, internal_name=it["name"], stage="発売済", launch_date=it["launch"],
                               summary=f"2025年度に発売済み。{why} から移行（シートの案件: {'・'.join(it['sheet']) or 'シートに無い'}）")["id"]
        now = store.now_s()
        with store.tx() as c:
            c.execute("UPDATE project SET source_sheet=?, source_key=?, source_row=? WHERE id=?",
                      (SRC, it["name"], it["row"], pid))
            for seq, t in enumerate(it["tasks"], 1):
                st = "完了" if t["status"] == "完了" else "対象外"
                done_at = (t["due"] or t["start"] or it["launch"]) if st == "完了" else None
                c.execute("INSERT INTO task (project_id,seq,title,role,start_on,due_on,hours,status,done_at,created_at,kind,source_row) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,'実作業',?)",
                          (pid, seq, t["title"], t["role"], t["start"], t["due"], t["hours"], st, done_at, now, t["row"]))
            ng = _migration_gates(c, pid, None, by, now, why)
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                      (pid, now, by, "2025年度の発売済み案件として移行",
                       f"タスク {len(it['tasks'])} 件（未完了は対象外）・ゲートの移行補正 {ng} 件。{why}"))
        if it["labels"]:
            newprod.register(pid, it["labels"], by)
        else:
            newprod.on_launched(pid)
        made.append(pid)
    store.audit(by, "migrate.r04", sheet_date, {"projects": made})
    print(f"\n書きました: 案件 {len(made)} 件（{', '.join(made)}）")
    return 0


# ── R03 ─────────────────────────────────────────────
F0BB = "f0bb"
NEW_NAME = "目印チャームに移行（新規分）"
NEW_LAUNCH = "2026-10-15"       # 「フルオーダー 目印チャーム 販売開始・全体に周知」の完了日
SLOT = "c008"                   # 承認済み d542 の 2026-10「目印チャームに移行（新規分）」（未結び）
# シート 976〜982・985・986 行（A 列がリニューアル分になっていた新規分）と、元の新規分の行（164〜170 行）の古い2件。
# 簡単オーダーの2件（983・984 行＝タスク 341・20）は石原さんの返事（976a と同じか）を待つので、ここでは動かさない
MOVE = [(21, "シリコンリング黒100個購入"), (336, "シリコンリング白/全色セット　価格決定"),
        (337, "シリコンリング白/全色セットを販売開始"), (338, "効率的なデータ作成方法を検討"),
        (339, "フル全商品　モールの価格"), (19, "フルオーダー 目印チャーム　販売開始"),
        (340, "新商品売上レポートに計上するよう設定"), (24, "ユニフォーム以外のストラップ商品の販売停止"),
        (25, "ナンバープレートをナスカンキーホルダー付きに"), (22, "シリコンリング全色セット　価格決定"),
        (23, "シリコンリング全色セットを販売開始")]
NEW_LABELS = ["フルオーダー 目印チャーム（全競技計）"]
PINS, PINS_LABEL = "8afa", "国旗ピンズ"


def r03(apply: bool, by: str) -> int:
    print(f"# R03 目印チャーム（新規分）と国旗ピンズ（{'書く' if apply else '見るだけ'}）\n")
    ok = True
    exists = store.one("SELECT id FROM project WHERE internal_name=?", (NEW_NAME,))
    rows = []
    for tid, head in MOVE:
        t = store.one("SELECT id, project_id, title, status, due_on FROM task WHERE id=?", (tid,))
        good = t is not None and t["project_id"] == F0BB and t["title"].startswith(head)
        if t is not None and exists and t["project_id"] == exists["id"]:
            good = True
        ok &= good
        rows.append((tid, head, t, good))
    print(f"新規分の案件: {'作成済み ' + exists['id'] if exists else '作る'}（発売済・{NEW_LAUNCH}・ラベル {'、'.join(NEW_LABELS)}・枠 {SLOT}）")
    print("| タスク | いまの案件 | 名前 | 状態 | 期限 | 確認 |\n|---|---|---|---|---|---|")
    for tid, head, t, good in rows:
        print(f"| {tid} | {t['project_id'] if t else '無し'} | {t['title'] if t else head} | {t['status'] if t else ''} | "
              f"{(t['due_on'] if t else '') or '空'} | {'○' if good else '★合わない'} |")
    s = store.one("SELECT s.*, v.state FROM plan_slot s JOIN plan_version v ON v.id=s.version_id WHERE s.id=?", (SLOT,))
    slot_ok = s is not None and s["product"] == NEW_NAME and (not s["project_id"] or (exists and s["project_id"] == exists["id"]))
    print(f"\n年間プランの枠 {SLOT}: " + (f"{s['launch_month']} {s['product']}・版 {s['version_id']}（{s['state']}）・結び {s['project_id'] or '無し'}"
                                         if s else "無い") + ("" if slot_ok else " ★合わない"))
    pg = store.one("SELECT labels FROM newprod_reg WHERE project_id=?", (PINS,))
    pins = json.loads(pg["labels"]) if pg and pg["labels"] else []
    print(f"国旗ピンズ: {PINS} のラベル {pins} → " + ("そのまま（もうある）" if PINS_LABEL in pins else f"「{PINS_LABEL}」を足す"))
    if not (ok and slot_ok and pg):
        print("\n★合わないものがあるので止めます")
        return 1
    if not apply:
        return 0
    now = store.now_s()
    why = WHY.replace("R04", "R03")
    if exists:
        pid = exists["id"]
    else:
        pid = project_m.create(by, expand=False, internal_name=NEW_NAME, stage="発売済", launch_date=NEW_LAUNCH,
                               summary=f"フルオーダー 目印チャーム。{why}。進捗管理シート 975 行の見出しの下の行が"
                                       "「リニューアル分」になっていたため、リニューアル分（f0bb）から移した")["id"]
    with store.tx() as c:
        c.execute("UPDATE project SET source_sheet=?, source_key=? WHERE id=? AND source_sheet IS NULL",
                  (ss.SHEET, NEW_NAME, pid))
        moved = []
        for seq, (tid, _head) in enumerate(MOVE, 1):
            r = c.execute("UPDATE task SET project_id=?, seq=? WHERE id=? AND project_id=?", (pid, seq, tid, F0BB)).rowcount
            if r:
                moved.append(tid)
        if moved:
            for p_, what in ((F0BB, f"タスク {len(moved)} 件を「{NEW_NAME}」（{pid}）へ移した"),
                             (pid, f"タスク {len(moved)} 件を「目印チャームに移行（リニューアル分）」（{F0BB}）から移した")):
                c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                          (p_, now, by, what, f"タスク {', '.join(map(str, moved))}。{why}"))
        ng = _migration_gates(c, pid, None, by, now, why)
    cur = store.one("SELECT state, labels FROM newprod_reg WHERE project_id=?", (pid,))
    if not (cur and cur["state"] == "登録済み" and set(NEW_LABELS) <= set(json.loads(cur["labels"] or "[]"))):
        newprod.register(pid, NEW_LABELS, by)
    if not store.val("SELECT project_id FROM plan_slot WHERE id=?", (SLOT,)):
        plan.link_slot(SLOT, pid, by)
    if PINS_LABEL not in pins:
        newprod.register(PINS, pins + [PINS_LABEL], by)
    store.audit(by, "migrate.r03", pid, {"moved": moved, "gates": ng, "slot": SLOT, "pins": PINS_LABEL})
    print(f"\n書きました: 新規分 {pid}・移したタスク {len(moved)} 件・ゲートの移行補正 {ng} 件・枠 {SLOT}・国旗ピンズ")
    return 0


# ── 簡単オーダー（石原さん 11-19 11:55: 976a とは別の案件）──────────────
KANTAN = "簡単オーダー 目印チャーム"
KANTAN_LAUNCH = "2026-12-04"    # 石原さん 11-19 10:59「発売予定日を12/4に」
KANTAN_TASKS = [(341, "簡単オーダー 目印チャーム　アプリ実装可否を確認", "2026-11-27"),
                (20, "簡単オーダー 目印チャーム　販売開始・全体に周知", "2026-12-04")]
SPORTS = ("剣道", "弓道", "空手", "柔道", "野球", "サッカー", "バスケ", "バレー")
KANTAN_LABELS = [f"フルオーダー 目印チャーム【{k}】" for k in SPORTS]   # 杉浦さんが改名したら新しい名前も足す


def kantan(apply: bool, by: str) -> int:
    print(f"# 簡単オーダー 目印チャーム（{'書く' if apply else '見るだけ'}）\n")
    ex = store.one("SELECT id FROM project WHERE internal_name=?", (KANTAN,))
    ok = True
    print(f"案件: {'作成済み ' + ex['id'] if ex else '作る'}（開発中・{KANTAN_LAUNCH}・枠とは結ばない）")
    print("| タスク | いまの案件 | 名前 | 状態 | → 期限 | 確認 |\n|---|---|---|---|---|---|")
    for tid, head, due in KANTAN_TASKS:
        t = store.one("SELECT project_id, title, status FROM task WHERE id=?", (tid,))
        good = t is not None and t["title"].startswith(head) and (t["project_id"] == F0BB or (ex and t["project_id"] == ex["id"]))
        ok &= bool(good)
        print(f"| {tid} | {t['project_id'] if t else '無し'} | {t['title'] if t else head} | {t['status'] if t else ''} | "
              f"{due} | {'○' if good else '★合わない'} |")
    used = {}
    for r in store.q("SELECT project_id, labels FROM newprod_reg WHERE labels IS NOT NULL"):
        for l in json.loads(r["labels"] or "[]"):
            used[l] = r["project_id"]
    clash = [l for l in KANTAN_LABELS if l in used and not (ex and used[l] == ex["id"])]
    print("ラベル: " + "、".join(KANTAN_LABELS) + (f"\n★ほかの案件に結ばれている: {clash}" if clash else ""))
    if not ok or clash:
        print("\n★合わないものがあるので止めます")
        return 1
    if not apply:
        return 0
    why = f"石原さん 2026-11-19 11:55（976a とは別の案件）・{WHY.replace('R04', 'R03')}"
    pid = ex["id"] if ex else project_m.create(
        by, expand=False, internal_name=KANTAN, stage="開発中", launch_date=KANTAN_LAUNCH,
        summary=f"競技別の簡単オーダー目印チャーム（8競技）。{why}。進捗管理シート 983・984 行をリニューアル分（f0bb）から移した")["id"]
    now = store.now_s()
    with store.tx() as c:
        moved = []
        for seq, (tid, _h, due) in enumerate(KANTAN_TASKS, 1):
            r = c.execute("UPDATE task SET project_id=?, seq=?, status='未着手', done_at=NULL, due_on=? "
                          "WHERE id=? AND project_id=?", (pid, seq, due, tid, F0BB)).rowcount
            if r:
                moved.append(tid)
        if moved:
            for p_, what in ((F0BB, f"タスク {len(moved)} 件を「{KANTAN}」（{pid}）へ移した"),
                             (pid, f"タスク {len(moved)} 件を「目印チャームに移行（リニューアル分）」（{F0BB}）から移し、未着手に戻した")):
                c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                          (p_, now, by, what, f"タスク {', '.join(map(str, moved))}（期限 11/27・12/4）。{why}"))
    cur = store.one("SELECT state, labels FROM newprod_reg WHERE project_id=?", (pid,))
    have = json.loads(cur["labels"] or "[]") if cur else []
    if not (cur and cur["state"] == "登録済み" and set(KANTAN_LABELS) <= set(have)):
        newprod.register(pid, have + [l for l in KANTAN_LABELS if l not in have], by)
    store.audit(by, "migrate.kantan", pid, {"moved": moved})
    print(f"\n書きました: {pid}・移したタスク {len(moved)} 件・ラベル {len(KANTAN_LABELS)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["r04", "r03", "kantan"])
    ap.add_argument("ledger", nargs="?")
    ap.add_argument("--sheet-date", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--by", default="")
    a = ap.parse_args()
    if a.apply and not a.by:
        print("--apply には --by（実施者）が要ります", file=sys.stderr)
        return 2
    seed.run()
    if a.what == "r04":
        if not a.ledger or not a.sheet_date:
            print("r04 にはシートの TSV と --sheet-date が要ります", file=sys.stderr)
            return 2
        return r04(a.ledger, a.sheet_date, a.apply, a.by)
    if a.what == "kantan":
        return kantan(a.apply, a.by)
    return r03(a.apply, a.by)


if __name__ == "__main__":
    sys.exit(main())
