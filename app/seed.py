#!/usr/bin/env python3
"""
種データ。**何度流しても同じ結果**（起動のたびに流す）。

    python3 -m app.seed            # 流す
    python3 -m app.seed --report   # 件数と、採用しなかった差分だけを出す

**推測で埋めない。**分からないものは NULL のまま入れ、画面に「未確定」と出す。
⑤資材リニューアルの工数ポイント係数がそれで、年間プラン2026年度に1件も
使われておらず実測できない。0 で埋めると「工数ゼロの安いフロー」に見えて
逆に危ないので、**NULL のままにする**。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from . import idea as idea_m
from . import automation as auto_m
from . import plan as plan_m
from . import store

BASE = Path(__file__).resolve().parent.parent
# **`data/` の下に置かない。**`.gitignore` が `data/` を丸ごと外すので、
# 種データが版管理から落ちる。種は成果物ではなく入力なので、`seed/` に置く。
SEED_DIR = BASE / "seed"

TEMPLATE_TSV = SEED_DIR / "03_商品開発フローAI効率化分析__タスク別AI適用一覧.tsv"
OLD_TEMPLATE_TSV = SEED_DIR / "01_進捗管理商品開発__テンプレート.tsv"

TEMPLATE_VERSION = 1

# ── 開発タイプ（§4-1）──────────────────────────────────
# 係数は実測で確定したもの。**⑤だけが None。**
FLOW_TYPES = [
    ("meire",     1, "名入れ商品",               "①名入れ商品",                 5.5,  None, 1),
    ("freecut",   2, "フリーカット商品",         "②フリーカット商品",           6.0,  None, 1),
    ("webdeco",   3, "Webdeco商品",              "③Webdeco商品",                5.5,  None, 1),
    ("newmodel",  4, "ニューモデル追加(スキンシール)",
     "④ニューモデル追加(スキンシール)",                                        3.0,  None, 1),
    ("material",  5, "資材リニューアル",         "⑤資材リニューアル",           None,
     "年間プラン2026年度で1件も使われておらず実測できない。**推測で埋めない。**"
     "商品開発部の確認待ち（全体設計書 第11章）", 1),
    ("readymade", 6, "既成デザイン商品",         "⑥既成デザイン商品",           3.0,  None, 1),
    # ⑦は標準タスクが存在しない。**発明しない**（第11章 ⑪の未決事項）
    ("pagerenew", 7, "ページリニューアル",       None,                          2.5,  None, 0),
]

FLOW_NOTE = {
    "material": "工数ポイント係数が未確定です。年間プラン2026年度に1件も使われて"
                "おらず、実測できませんでした。商品開発部の確認を待っています。",
    "pagerenew": "**標準タスク未定義。**178行の種データに⑦のタスクが1行もありません。"
                 "この開発タイプで案件を作ると、タスク一覧は空になります。"
                 "計画の38%を占めながら定義が無い状態で、商品開発部の定義待ちです"
                 "（全体設計書 第11章 ⑪）。",
    "newmodel": "**G2 → G5 の簡易フロー。**G1・G3・G4 は `対象外` です（F-6-4）。"
                "既存商品の機種追加で判断事項が少ないため、現行フローにも"
                "「社長の確認」がありません。",
}

# ── 業務ロール（§10-2 ②）────────────────────────────────
# external=1 は「他部署（AI削減の試算対象外）」。
# **0h は「実際に0時間」ではなく「試算から意図的に除外した」値。**自部署と足さない。
ROLES = [
    ("president", "社長",       1, 0, "ゲートの承認者（G2・G3）。**アプリ権限の admin とは別**"),
    ("admin",     "管理者",     2, 0, "6フロー合算で 43.25h（うち AI削減可能 27.28h）。ボトルネック"),
    ("member",    "メンバー",   3, 0, "6フロー合算で 44.50h（うち AI削減可能 18.36h）"),
    ("devdept",   "商品開発部", 4, 0, None),
    ("webmkt",    "Webマーケ",  5, 1, "他部署。AI削減の試算対象外（0h は「除外」であって「0時間」ではない）"),
    ("prod",      "生産部",     6, 1, "他部署。G4 の承認者"),
    ("part",      "パート",     7, 0, None),
]

# 種データTSV の「担当」列 → role.code
ROLE_BY_LABEL = {
    "社長": "president", "管理者": "admin", "メンバー": "member",
    "商品開発部": "devdept", "Webマーケ": "webmkt", "生産部": "prod",
    "パート": "part",
}

ALL_FLOWS = [f[0] for f in FLOW_TYPES]
# ④は G2 → G5 の簡易フロー（F-6-4・§5-6）。G1/G3/G4 は対象外
NOT_NEWMODEL = [c for c in ALL_FLOWS if c != "newmodel"]


def _item(key, label, check, goto, stage=None, hint=None):
    d = {"key": key, "label": label, "check": check, "goto": goto}
    if stage:
        d["stage"] = stage      # このデータを持つ段。いまは手で確認する
    if hint:
        d["hint"] = hint
    return d


# ── ゲート（§4-6・F-6 の表のとおり）───────────────────────
# check の書式:
#   project:<列>        … project の列が空でない
#   section:<節キー>    … カルテの節が空でない
#   lines:<節キー>:<n>  … その節に空でない行が n 行以上ある
#   variants:<n>        … バリエーションが n 件以上ある
#   manual              … **このアプリがまだ持っていないデータ。**人が確認した記録を見る
GATES = [
    ("G0", 0, "起票", ["devdept", "admin", "member", "president"], ALL_FLOWS, [
        _item("internal_name", "商品案名（社内呼称）", "project:internal_name", "A"),
        _item("summary", "概要・仕様", "project:summary", "A"),
        _item("target_scene", "想定ターゲットと使用シーン", "section:C.target", "C"),
        _item("origin", "起票経路", "manual", "A", "第1段",
              "アイデア台帳（第1段）ができたら idea.origin から自動で埋まります"),
    ], "起票は軽いままにする。**これだけ。**"),

    ("G1", 1, "評価通過", ["admin"], NOT_NEWMODEL, [
        _item("scored", "採点済み", "manual", "C", "第1段",
              "アイデア採点（第1段）ができたら idea_score から自動で埋まります"),
        _item("feasibility", "生産方法(1-5) 判定済み", "manual", "E", "第1段"),
        _item("research", "調査ノート 1件以上", "manual", "C", "第3段",
              "research_note（第3段以降）ができたら件数で自動判定します"),
        _item("dedup", "重複チェック済み", "manual", "C", "第1段"),
    ], None),

    ("G2", 2, "年間プラン採択", ["admin", "president"], ALL_FLOWS, [
        _item("occasion", "機会（なぜその日か）", "project:occasion", "A"),
        _item("flow_type", "商品タイプ", "project:flow_type", "A"),
        _item("area", "作成エリア", "project:area", "A"),
        _item("effort_point", "工数ポイント", "project:effort_point", "A", None,
              "⑤資材リニューアルは係数が未確定のため、ここは自動では埋まりません"),
        _item("launch_date", "発売予定日", "project:launch_date", "A"),
        _item("plan_check", "月次工数ポイント10〜15 と 推し活:うちわ=3:1 を崩していない",
              "manual", "A", "第1段",
              "年間プラン（第1段）ができたら挿入ルールの検査で自動判定します"),
    ], "**承認者は2ロール。**どちらの資格でも記録できるようにしてある"),

    ("G3", 3, "コンセプト承認", ["president"], NOT_NEWMODEL, [
        _item("concept", "コンセプト文", "section:C.concept", "C"),
        _item("diff3", "差別化 3点以上", "lines:C.diff:3", "C"),
        _item("cost_v1", "試算原価 v1", "manual", "D", "第3段",
              "原価・調達（第3段）ができたら cost_estimate から自動で埋まります"),
        _item("price", "販売価格", "section:D.price", "D"),
        _item("goal", "年間目標（根拠つき）", "manual", "D", "第4段",
              "発売後評価（第4段）ができたら sales_target の方式と根拠で判定します"),
        _item("material", "資材の調達見通し", "section:D.material", "D"),
        _item("risk", "主要リスクと対策", "section:E.risk", "E"),
    ], "**社長だけ。**枠を取る判断（G2）と中身の判断（G3）を分ける（F-6-1）"),

    ("G4", 4, "生産可否確定", ["prod"], NOT_NEWMODEL, [
        _item("method", "作成方法の詳細・生産の流れ確定", "section:E.method", "E"),
        _item("sample", "試作・調整完了", "section:E.sample", "E"),
        _item("order", "本番発注済み（または発注可能日が確定）", "manual", "E", "第3段"),
        _item("sim", "完成シミュレーション確認済み", "manual", "E"),
    ], "**生産部の判断。**社長の承認とは別人格（§10-2 ②）"),

    ("G5", 5, "発売可", ["admin"], ALL_FLOWS, [
        _item("testorder", "テスト注文チェック済み", "manual", "F"),
        _item("lp", "商品ページ公開確認", "section:F.lp", "F"),
        _item("product_code", "Seisan の商品コード確定", "manual", "F", "第3段",
              "F-6-7。口頭ではなく連携（F-12）の完了をもって判定します"),
        _item("goal_entered", "年間目標が入力済み", "manual", "D", "第4段"),
        _item("announce", "全体周知済み", "manual", "F"),
        _item("profc", "PRO FUN-CREATOR 共有済み", "manual", "F"),
    ], None),

    ("G6", 6, "発売後評価", ["admin"], NOT_NEWMODEL, [
        _item("d14", "14日チェック完了", "manual", "G", "第4段"),
        _item("d60", "60日チェック完了", "manual", "G", "第4段"),
        _item("m12", "12か月チェック完了", "manual", "G", "第4段"),
    ], None),
]

# ── 保留・中止の理由（F-6-5）───────────────────────────
HOLD_REASONS = [
    ("material_moq",   "資材が確保できない（MOQ が発売日に間に合わない）", 1),
    ("material_lead",  "資材が確保できない（リードタイムが発売日に間に合わない）", 2),
    ("method_new",     "生産方法が「1：新設備が必要」または外注のみ", 3),
    ("effort_over",    "月次工数ポイント超過", 4),
    ("ip_risk",        "意匠権・商標の懸念", 5),
]

ABORT_REASONS = [
    ("competitor",  "競合が同等品を先行発売し、差別化3点が成立しない", 1, "中止"),
    ("margin",      "試算原価が目標粗利を満たせない", 2, "中止"),
    ("hold_twice",  "2回保留を繰り返した", 3, "中止"),
    ("no_sales",    "過去3か月の売上が0（本店＋主要モール）かつ在庫が無い／処分可能",
     4, "発売後の撤退"),
]


# ══════════════════════════════════════════════════════════
def seed_masters():
    for code, seq, label, tsv, pt, note, has_tpl in FLOW_TYPES:
        store.ex(
            "INSERT INTO flow_type (code,seq,label,tsv_label,effort_point,"
            "effort_point_note,has_template,note) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET seq=excluded.seq,label=excluded.label,"
            "tsv_label=excluded.tsv_label,effort_point=excluded.effort_point,"
            "effort_point_note=excluded.effort_point_note,"
            "has_template=excluded.has_template,note=excluded.note",
            (code, seq, label, tsv, pt, note, has_tpl, FLOW_NOTE.get(code)))

    for code, label, sort, ext, note in ROLES:
        store.ex(
            "INSERT INTO role (code,label,sort,external,note) VALUES (?,?,?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET label=excluded.label,sort=excluded.sort,"
            "external=excluded.external,note=excluded.note",
            (code, label, sort, ext, note))

    for gate, seq, name, approvers, flows, items, note in GATES:
        store.ex(
            "INSERT INTO gate_def (gate,seq,name,approver_role,required_items,"
            "applies_to_flow_types,note) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(gate) DO UPDATE SET seq=excluded.seq,name=excluded.name,"
            "approver_role=excluded.approver_role,"
            "required_items=excluded.required_items,"
            "applies_to_flow_types=excluded.applies_to_flow_types,note=excluded.note",
            (gate, seq, name,
             json.dumps(approvers, ensure_ascii=False),
             json.dumps(items, ensure_ascii=False),
             json.dumps(flows, ensure_ascii=False), note))

    for code, label, sort in HOLD_REASONS:
        store.ex("INSERT INTO hold_reason (code,label,sort) VALUES (?,?,?) "
                 "ON CONFLICT(code) DO UPDATE SET label=excluded.label,"
                 "sort=excluded.sort", (code, label, sort))
    for code, label, sort, kind in ABORT_REASONS:
        store.ex("INSERT INTO abort_reason (code,label,sort,kind) VALUES (?,?,?,?) "
                 "ON CONFLICT(code) DO UPDATE SET label=excluded.label,"
                 "sort=excluded.sort,kind=excluded.kind", (code, label, sort, kind))
    store.conn().commit()


def _pct(s: str) -> float | None:
    s = (s or "").strip().rstrip("%")
    if not s:
        return None
    try:
        return float(s) / 100.0
    except ValueError:
        return None


def _num(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_template_tsv(path: Path = TEMPLATE_TSV) -> list[dict]:
    """種データの178行。**ここが正。**

    列: 開発フロー／タスク／担当／実作業(h)／AI適用カテゴリ／削減率／
        AI削減可能(h)／AI・エージェント活用の具体策
    """
    by_tsv = {f[3]: f[0] for f in FLOW_TYPES if f[3]}
    out, seqs = [], {}
    with path.open(encoding="utf-8", newline="") as f:
        for lineno, row in enumerate(csv.reader(f, delimiter="\t"), start=1):
            if lineno == 1 or not row or not (row[0] or "").strip():
                continue
            flow = by_tsv.get(row[0].strip())
            if flow is None:
                raise ValueError(f"{path.name}:{lineno} 知らない開発フロー {row[0]!r}")
            role = ROLE_BY_LABEL.get((row[2] or "").strip())
            if role is None:
                raise ValueError(f"{path.name}:{lineno} 知らない担当 {row[2]!r}")
            seqs[flow] = seqs.get(flow, 0) + 1
            out.append({
                "flow_type": flow, "seq": seqs[flow],
                "title": (row[1] or "").strip(), "role": role,
                "standard_hours": _num(row[3]),
                "ai_category": (row[4] or "").strip() or None,
                "ai_reduction_rate": _pct(row[5] if len(row) > 5 else ""),
                "ai_reduction_hours": _num(row[6] if len(row) > 6 else ""),
                "ai_howto": (row[7].strip() if len(row) > 7 else "") or None,
                "source": f"{path.name}:{lineno}",
            })
    return out


def seed_templates() -> int:
    """`template_version = 1` として入れる。**版を上書きしない。**"""
    rows = read_template_tsv()
    for r in rows:
        store.ex(
            "INSERT INTO task_template (flow_type,template_version,seq,title,role,"
            "standard_hours,ai_category,ai_reduction_rate,ai_reduction_hours,"
            "ai_howto,source) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(flow_type,template_version,seq) DO UPDATE SET "
            "title=excluded.title,role=excluded.role,"
            "standard_hours=excluded.standard_hours,"
            "ai_category=excluded.ai_category,"
            "ai_reduction_rate=excluded.ai_reduction_rate,"
            "ai_reduction_hours=excluded.ai_reduction_hours,"
            "ai_howto=excluded.ai_howto,source=excluded.source",
            (r["flow_type"], TEMPLATE_VERSION, r["seq"], r["title"], r["role"],
             r["standard_hours"], r["ai_category"], r["ai_reduction_rate"],
             r["ai_reduction_hours"], r["ai_howto"], r["source"]))
    store.conn().commit()
    return len(rows)


# ── 採用しなかった古い版との差分（記録だけ）───────────────
# `01_進捗管理商品開発__テンプレート.tsv` は**古い。採用しない。**
# 6フローが横に並んだ表で、列は 担当者／進捗状況／開始／期限／所要時間（h）。
# タスク名の列は 1, 8, 15, 22, 29, 36。所要時間はその +5。
OLD_COLS = [(1, "meire"), (8, "freecut"), (15, "webdeco"),
            (22, "newmodel"), (29, "material"), (36, "readymade")]
OLD_FIRST_TASK_ROW = 6      # 0..5 は見出しと「商品名」の行


def _old_grid(path: Path = OLD_TEMPLATE_TSV):
    if not path.is_file():
        return None
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.reader(f, delimiter="\t"))


def old_template_counts(path: Path = OLD_TEMPLATE_TSV) -> dict:
    """**件数だけ数えて差分を記録する**（どちらが正かの判断は商品開発部）。"""
    grid = _old_grid(path)
    if grid is None:
        return {}
    out = {}
    for idx, code in OLD_COLS:
        out[code] = len([r for r in grid[OLD_FIRST_TASK_ROW:]
                         if len(r) > idx and (r[idx] or "").strip()])
    return out


def old_reserve_hours(path: Path = OLD_TEMPLATE_TSV) -> dict:
    """**古い表にしか無い「予備時間」の行**（F-5-7）。

    要件定義 F-5-7 は「予備時間（フローごとに17〜20h）を工数の内訳として
    明示的に持つ」と言っている。**採用した178行にはこの行が1つも無い。**
    ここで見つけた値を記録し、`task_template` には入れない
    （古い表が正かどうかの判断は商品開発部）。
    """
    grid = _old_grid(path)
    if grid is None:
        return {}
    out = {}
    for idx, code in OLD_COLS:
        rows = []
        for r in grid[OLD_FIRST_TASK_ROW:]:
            if len(r) > idx + 5 and (r[idx] or "").strip() == "予備時間":
                rows.append({"role": ROLE_BY_LABEL.get((r[idx + 1] or "").strip()),
                             "hours": _num(r[idx + 5])})
        out[code] = {"rows": rows,
                     "total_hours": round(sum(x["hours"] or 0 for x in rows), 2)}
    return out


def title_diff(path: Path = OLD_TEMPLATE_TSV) -> dict:
    """タスク名の差。**記録だけ。**"""
    grid = _old_grid(path)
    if grid is None:
        return {}
    new = read_template_tsv()
    by_new: dict[str, list[str]] = {}
    for r in new:
        by_new.setdefault(r["flow_type"], []).append(r["title"])
    out = {}
    for idx, code in OLD_COLS:
        old = [(r[idx] or "").strip() for r in grid[OLD_FIRST_TASK_ROW:]
               if len(r) > idx and (r[idx] or "").strip()]
        n = by_new.get(code, [])
        out[code] = {"only_in_new": [x for x in n if x not in old],
                     "only_in_old": [x for x in old if x not in n]}
    return out


def diff_report() -> dict:
    new = read_template_tsv()
    new_counts: dict[str, int] = {}
    for r in new:
        new_counts[r["flow_type"]] = new_counts.get(r["flow_type"], 0) + 1
    old = old_template_counts()
    return {
        "adopted": {"file": TEMPLATE_TSV.name, "rows": len(new), "by_flow": new_counts},
        "not_adopted": {"file": OLD_TEMPLATE_TSV.name,
                        "task_rows": sum(old.values()), "by_flow": old,
                        "note": "行数を『196』と数えると、各フローの「商品名」の"
                                "見出し行6件を含む。タスクの行は190件"},
        "delta": {k: new_counts.get(k, 0) - old.get(k, 0)
                  for k in sorted(set(new_counts) | set(old))},
        # **ここが差分の中身。**件数だけでは何が違うのか分からない
        "titles": title_diff(),
        "reserve_hours_only_in_old": old_reserve_hours(),
        "note": "01_…テンプレート.tsv は古いため**採用しない**。差分は記録のみ。"
                "ただし「予備時間」の行は古い表にしかなく、F-5-7 が明示的に"
                "持てと言っている項目なので、値を記録してある。"
                "採用するかどうかは商品開発部の確認が要る。",
    }


# ── 予備時間（F-5-7 ／ 2026-09-23 十文字さんの決定）────────
# **量**: 旧テンプレートの役割ごとの値 ÷2 を、フロー全体の合計とする。
# 十文字さんの言葉「予備時間が多すぎるので、現在の半分の時間にして1人分にしてほしい。
# 例16時間×2人であれば、8時間だけ」。名入れなら 32.0h → **8.0h**。
#
# **担当**: 旧表は 管理者・メンバーへ**同額**を置いていた。その比をそのまま保ち、
# 合計 8.0h を 4.0h + 4.0h に等分する。
# **片方へ寄せない。**試しに全部を管理者へ寄せたところ、管理者だけ
# 43.25h → 88.25h となり、6フロー合算のボトルネック（管理者 43.25h ／
# メンバー 44.50h）が逆転した。**元の表に無い偏りを、こちらで作らない。**
# 寄せたくなったら `RESERVE_SPLIT` を変えるだけ。
RESERVE_SEQ0 = 998          # **必ず最後。**実作業の seq と衝突させない
RESERVE_SPLIT = ["admin", "member"]
RESERVE_TITLE = "予備時間"


def seed_reserve() -> dict:
    """旧テンプレートから予備時間を読み、**半分・1人分**を担当で等分して入れる。

    **旧ファイルが無ければ何も入れない（0件と報告する）。**
    推測で埋めない。⑦ページリニューアルは旧表に行が無いので対象外。
    """
    try:
        old = old_reserve_hours()
    except FileNotFoundError:
        return {"rows": 0, "missing": f"{OLD_TEMPLATE_TSV} がありません"}
    out, n = {}, 0
    for flow, v in old.items():
        rows = v["rows"]
        if not rows:
            continue
        per_role = float(rows[0]["hours"])        # 管理者・メンバーとも同額
        total = per_role / 2                      # **丸めない**（19.75→9.875）
        each = total / len(RESERVE_SPLIT)
        for i, role in enumerate(RESERVE_SPLIT):
            store.ex(
                "INSERT INTO task_template (flow_type,template_version,seq,title,"
                "role,standard_hours,ai_category,ai_reduction_rate,"
                "ai_reduction_hours,ai_howto,source,kind) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(flow_type,template_version,seq) DO UPDATE SET "
                "title=excluded.title,role=excluded.role,"
                "standard_hours=excluded.standard_hours,source=excluded.source,"
                "kind=excluded.kind",
                (flow, TEMPLATE_VERSION, RESERVE_SEQ0 + i, RESERVE_TITLE, role,
                 each, None, None, None, None,
                 f"旧テンプレートの予備時間 {per_role}h（役割ごと）÷2＝{total}h を"
                 f"{len(RESERVE_SPLIT)}名で等分。2026-09-23 十文字さんの決定", "予備"))
            n += 1
        out[flow] = total
    store.conn().commit()
    return {"rows": n, "hours_per_flow": out,
            "split": RESERVE_SPLIT,
            "note": "合計は旧表の半分（1人分）。担当は旧表の比（同額）のまま等分"}


def run() -> dict:
    """**種ファイルが無くても落ちない。ただし黙らない。**

    落ちると全画面が出なくなる（認証も含め）。
    代わりに `task_template: 0` と `missing` を返し、`/api/health` に出す。
    **0 と「ファイルが無い」を区別する。**
    """
    store.migrate()
    seed_masters()
    # 第1段のマスタ（§4-2）。**テーマ・rubric の版・設定。**
    # rubric は v1（移行したそのままの点）と v2（新しい軸）を併存させる（F-1-10）
    idea_m.seed_themes()
    idea_m.seed_rubrics()
    idea_m.seed_settings()
    # 第1段の残り（§4-3）。**商品タイプと挿入ルールの設定**（F-3）
    plan_m.seed()
    # 自動化依頼の渡し先（F-15-6）。**未設定のまま出荷する**（送り先は人が決める）
    auto_m.seed_settings()
    store.conn().commit()
    missing = None
    try:
        n = seed_templates()
    except FileNotFoundError:
        n = 0
        missing = f"{TEMPLATE_TSV} がありません。標準タスクを1件も持っていません"
    # **予備時間は実作業のあと。**seq=999 で必ず最後に置く（F-5-7）
    reserve = seed_reserve()
    return {
        "reserve": reserve,
        "missing": missing,
        "flow_type": store.val("SELECT COUNT(*) FROM flow_type"),
        "role": store.val("SELECT COUNT(*) FROM role"),
        "gate_def": store.val("SELECT COUNT(*) FROM gate_def"),
        "hold_reason": store.val("SELECT COUNT(*) FROM hold_reason"),
        "abort_reason": store.val("SELECT COUNT(*) FROM abort_reason"),
        "task_template": n,
        "theme": store.val("SELECT COUNT(*) FROM theme"),
        "rubric": store.val("SELECT COUNT(*) FROM rubric"),
        "setting": store.val("SELECT COUNT(*) FROM setting"),
        "product_kind": store.val("SELECT COUNT(*) FROM product_kind"),
        # **アイデアは種データではない。**移行は tools/import_ideas.py で明示的に流す
        "idea": store.val("SELECT COUNT(*) FROM idea"),
    }


if __name__ == "__main__":
    if "--report" in sys.argv:
        print(json.dumps(diff_report(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(run(), ensure_ascii=False, indent=2))
        print(json.dumps(diff_report(), ensure_ascii=False, indent=2))
