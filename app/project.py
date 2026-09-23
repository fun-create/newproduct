#!/usr/bin/env python3
"""
案件（§4-4・F-4）とカルテ（§5-4・画面設計 3-8）。

**17枚をタブにしない。判断の順で6節に畳む**（C→D→E→F が G1→G3→G4→G5 と同順）。
**現行の壊れた採番（⑤が2枚・⑥⑦欠番）は保存しない**（B-11）。

**商品名を表示しない**（N-6-2）。画面に出すのは `cat1 / cat2 / cat3 + size`。
`internal_name` は社内の呼び名であって顧客データではない。
"""
from __future__ import annotations

import datetime as _dt

from . import gate, store

# ── カルテの節（§5-4）──────────────────────────────────
# 17枚を6節に畳む。番号ではなく**問い**で並べる。
SECTIONS = [
    {"key": "A", "title": "ヘッダ", "asks": "何を・いつ・誰が・なぜその日か",
     "fields": []},
    {"key": "B", "title": "いま欠けているもの", "asks": "次のゲートに何が足りないか",
     "fields": []},
    {"key": "C", "title": "なぜ作るか", "asks": "売れる理由", "fields": [
        ("C.competitor", "競合調査"),
        ("C.target", "ターゲット選定・使用シーン"),
        ("C.needs", "ニーズまとめ"),
        ("C.diff", "差別化（1行1点。3点以上）"),
        ("C.concept", "コンセプトまとめ"),
    ]},
    {"key": "D", "title": "いくらで作るか", "asks": "儲かる理由", "fields": [
        ("D.material", "資材の検討・決定"),
        ("D.cost", "原価算出のメモ（試算原価の正本は第3段）"),
        ("D.price", "販売価格"),
        ("D.goal", "目標設定のメモ（年間目標の正本は第4段）"),
    ]},
    {"key": "E", "title": "どう作るか", "asks": "作れる理由", "fields": [
        ("E.method", "生産方法・生産の流れ"),
        ("E.sample", "サンプル検討・試作"),
        ("E.quality", "品質について"),
        ("E.data", "データ入稿"),
        ("E.compat", "対応確認"),
        ("E.risk", "主要リスクと対策"),
    ]},
    {"key": "F", "title": "どう出すか", "asks": "出せる理由", "fields": [
        ("F.name", "商品名（社内呼称）の検討"),
        ("F.lp", "LP依頼用"),
        ("F.channel", "販路別の掲載"),
        ("F.code", "Seisan 商品コード（正本は第3段の連携）"),
    ]},
    {"key": "G", "title": "履歴", "asks": "誰がいつ何を決めたか", "fields": []},
]

SECTION_KEYS = {k for s in SECTIONS for k, _ in s["fields"]}

STAGES = ["起票", "評価済", "候補", "年間プラン採択", "コンセプト承認",
          "開発中", "生産確定", "発売済", "追跡中", "評価完了", "保留", "中止"]

REVENUE_BASIS = ["全額", "増分"]


def product_label(p: dict) -> str:
    """**商品名を出さない**（N-6-2）。`cat1 / cat2 / cat3 + size` で表す。"""
    parts = [(p.get(k) or "—") for k in ("cat1", "cat2", "cat3")]
    s = " / ".join(parts)
    if p.get("size"):
        s += f"　{p['size']}"
    if s.strip(" /—") == "":
        return "分類 未設定"
    return s


def flow_types() -> list[dict]:
    return store.rows(store.q("SELECT * FROM flow_type ORDER BY seq"))


def flow(code: str | None) -> dict | None:
    if not code:
        return None
    r = store.one("SELECT * FROM flow_type WHERE code=?", (code,))
    return dict(r) if r else None


# ── 作る ──────────────────────────────────────────────
def create(user_id: str, **f) -> dict:
    """案件を1件。**テンプレートがあれば同時に展開する。**

    ⑦ページリニューアルは種データに標準タスクが1行も無い。
    **発明しない。**空のタスク一覧のまま作り、画面で「標準タスク未定義」と出す。
    """
    ft = (f.get("flow_type") or "").strip() or None
    fl = flow(ft)
    if ft and fl is None:
        raise ValueError(f"知らない開発タイプ {ft!r}")
    pid = store.new_id("project")
    # 既定は false（F-4-9）。ページリニューアルを黙って新商品売上に混ぜない
    rc = 1 if str(f.get("revenue_counted") or "0") in ("1", "true", "含める") else 0
    basis = (f.get("revenue_basis") or "").strip() or None
    if rc and basis not in REVENUE_BASIS:
        raise ValueError("売上に含めるなら、計上方式（全額／増分）を選んでください（F-4-9）")
    if not rc:
        basis = None
    ep = f.get("effort_point")
    if ep in (None, ""):
        ep = fl["effort_point"] if fl else None     # NULL なら NULL のまま
    with store.tx() as c:
        c.execute(
            "INSERT INTO project (id,code,internal_name,flow_type,area,launch_date,"
            "occasion,idea_id,summary,owner,stage,revenue_counted,revenue_basis,"
            "source_of_truth,cat1,cat2,cat3,size,effort_point,"
            "created_at,created_by,updated_at,updated_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, (f.get("code") or "").strip() or None,
             (f.get("internal_name") or "").strip() or None, ft,
             (f.get("area") or "").strip() or None,
             (f.get("launch_date") or "").strip() or None,
             (f.get("occasion") or "").strip() or None,
             (f.get("idea_id") or "").strip() or None,
             (f.get("summary") or "").strip() or None,
             (f.get("owner") or "").strip() or None,
             (f.get("stage") or "起票"), rc, basis,
             (f.get("source_of_truth") or "app"),
             (f.get("cat1") or "").strip() or None,
             (f.get("cat2") or "").strip() or None,
             (f.get("cat3") or "").strip() or None,
             (f.get("size") or "").strip() or None,
             float(ep) if ep not in (None, "") else None,
             store.now_s(), user_id, store.now_s(), user_id))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,"
                  "what) VALUES (?,?,?,?)",
                  (pid, store.now_s(), user_id, "起票"))
    n = expand_tasks(pid, user_id)
    return {"id": pid, "tasks_created": n,
            "template_defined": bool(fl and fl["has_template"])}


def expand_tasks(project_id: str, user_id: str) -> int:
    """テンプレートを展開する（F-5-2）。

    **期限の自動割付はまだしない。**営業日マスタは calfc が正本で、
    その連携は第2段の範囲外（全体設計書 §3-5・第11章 ⑧が未依頼）。
    **推測で日付を入れない。**入れたら「期限なし」が消えて、
    実運用の「期限空欄88件」が見えなくなる。期限は人が入れる。
    """
    p = store.one("SELECT * FROM project WHERE id=?", (project_id,))
    if p is None or not p["flow_type"]:
        return 0
    if store.val("SELECT COUNT(*) FROM task WHERE project_id=?", (project_id,), 0):
        return 0            # 既に展開済み。**改訂しても展開済みは保持する**（F-5-6）
    tpl = store.q("SELECT * FROM task_template WHERE flow_type=? AND "
                  "template_version=1 ORDER BY seq", (p["flow_type"],))
    with store.tx() as c:
        for t in tpl:
            c.execute(
                "INSERT INTO task (project_id,template_id,seq,title,role,hours,"
                "status,ai_category,ai_reduction_rate,created_at,kind) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, t["id"], t["seq"], t["title"], t["role"],
                 t["standard_hours"], "未着手", t["ai_category"],
                 t["ai_reduction_rate"], store.now_s(), t["kind"]))
    return len(tpl)


# ── 読む ──────────────────────────────────────────────
def _filters(args: dict):
    where, params = ["1=1"], []
    if args.get("stage"):
        where.append("stage=?"); params.append(args["stage"])
    if args.get("flow"):
        where.append("flow_type=?"); params.append(args["flow"])
    if args.get("owner"):
        where.append("owner=?"); params.append(args["owner"])
    if args.get("launch_month"):
        where.append("substr(launch_date,1,7)=?"); params.append(args["launch_month"])
    if args.get("source_of_truth"):
        where.append("source_of_truth=?"); params.append(args["source_of_truth"])
    return " AND ".join(where), params


def listing(args: dict | None = None) -> dict:
    args = args or {}
    w, params = _filters(args)
    rs = store.q(
        "SELECT * FROM project WHERE " + w +
        " ORDER BY (launch_date IS NULL), launch_date, id", params)
    fl = {f["code"]: f for f in flow_types()}
    out = []
    for r in rs:
        p = dict(r)
        b = gate.board(p)
        nx = next((g for g in b if g["state"] in ("判定待ち", "差戻し", "保留")), None)
        f = fl.get(p["flow_type"] or "")
        out.append({
            "id": p["id"], "stage": p["stage"],
            "gates": [{"gate": g["gate"], "state": g["state"],
                       "glyph": gate.STATES[g["state"]]["glyph"],
                       "word": gate.STATES[g["state"]]["word"]} for g in b],
            "next_gate": (f"{nx['gate']} {nx['name']}" if nx else "—"),
            "launch_date": p["launch_date"],
            "product": product_label(p),
            "internal_name": p["internal_name"],
            "flow_type": p["flow_type"],
            "flow_label": f["label"] if f else "—",
            "revenue": ("含めない" if not p["revenue_counted"]
                        else f"含める（{p['revenue_basis'] or '方式未選択'}）"),
            "effort_point": p["effort_point"],
            "effort_point_note": (None if p["effort_point"] is not None
                                  else "未確定"),
            "owner": p["owner"],
            "missing_n": sum(len(g["missing"]) for g in b
                             if g["state"] in ("判定待ち", "差戻し", "保留")),
            # R-2。**移行期間の正本を列で出す**（§4-4）
            "source_of_truth": p["source_of_truth"],
            # 第3段で入る。**いまは「未算出」ではなく「未実装」と言う**
            "cost_estimate": None,
        })
    return {
        "rows": out,
        "filters": {
            "stage": STAGES, "flow": flow_types(),
            "owner": [r[0] for r in store.q(
                "SELECT DISTINCT owner FROM project WHERE owner IS NOT NULL "
                "ORDER BY owner")],
            "launch_month": [r[0] for r in store.q(
                "SELECT DISTINCT substr(launch_date,1,7) FROM project "
                "WHERE launch_date IS NOT NULL ORDER BY 1")],
        },
        "notes": [
            "商品名は表示しません。分類（cat1/cat2/cat3）とサイズで表します（N-6-2）。",
            "`試算原価` の列は第3段で入ります。いまは未実装です。",
        ],
    }


def detail(project_id: str, user_id: str = "") -> dict | None:
    r = store.one("SELECT * FROM project WHERE id=?", (project_id,))
    if r is None:
        return None
    p = dict(r)
    b = gate.board(p)
    nx = next((g for g in b if g["state"] in ("判定待ち", "差戻し", "保留")), None)
    secs = {x["section_key"]: dict(x) for x in store.q(
        "SELECT * FROM project_section WHERE project_id=?", (project_id,))}
    fl = flow(p["flow_type"])

    body = []
    for s in SECTIONS:
        if not s["fields"]:
            continue
        filled = sum(1 for k, _ in s["fields"]
                     if (secs.get(k, {}).get("body") or "").strip())
        # この節に紐づく未充足項目（B節から飛んでくる先）
        need = [m for g in b if g["state"] in ("判定待ち", "差戻し", "保留")
                for m in g["missing"] if m.get("goto") == s["key"]]
        body.append({
            "key": s["key"], "title": s["title"], "asks": s["asks"],
            # **パーセントにしない**（画面設計 3-8）。何が足りないか分からなくなる
            "progress": f"{filled} / {len(s['fields'])} 入力済",
            "gate_pending": (f"{nx['gate']}必須 {len(need)}件未入力"
                             if nx and need else None),
            "fields": [{"key": k, "label": lb,
                        "body": (secs.get(k, {}).get("body") or ""),
                        "updated_by": secs.get(k, {}).get("updated_by"),
                        "updated_at": secs.get(k, {}).get("updated_at")}
                       for k, lb in s["fields"]],
        })

    return {
        "id": p["id"],
        "header": {
            "product": product_label(p),
            "internal_name": p["internal_name"],
            "flow_type": p["flow_type"],
            "flow_label": fl["label"] if fl else "—",
            "flow_note": fl["note"] if fl else None,
            "effort_point": p["effort_point"],
            "effort_point_note": (fl or {}).get("effort_point_note")
            if p["effort_point"] is None else None,
            "launch_date": p["launch_date"], "occasion": p["occasion"],
            "owner": p["owner"], "stage": p["stage"], "area": p["area"],
            "summary": p["summary"],
            "revenue": ("含めない" if not p["revenue_counted"]
                        else f"含める（{p['revenue_basis'] or '方式未選択'}）"),
            "source_of_truth": p["source_of_truth"],
            "editable": p["source_of_truth"] == "app",
        },
        "gates": [{**g, "glyph": gate.STATES[g["state"]]["glyph"],
                   "word": gate.STATES[g["state"]]["word"]} for g in b],
        "next_gate": nx,
        # B節。**最上段に置くのがこの画面の設計そのもの**（画面設計 3-8）
        "missing": (nx["missing"] if nx else []),
        "sections": body,
        "variants": store.rows(store.q(
            "SELECT * FROM project_variant WHERE project_id=? ORDER BY label",
            (project_id,))),
        "tasks": store.rows(store.q(
            "SELECT t.*, r.label AS role_label FROM task t "
            "LEFT JOIN role r ON r.code=t.role "
            "WHERE t.project_id=? ORDER BY t.seq", (project_id,))),
        "template_defined": bool(fl and fl["has_template"]),
        "revisions": store.rows(store.q(
            "SELECT * FROM project_revision WHERE project_id=? "
            "ORDER BY id DESC LIMIT 50", (project_id,))),
        "can_approve": ({nx["gate"]: gate.can_approve(user_id, nx["gate"])}
                        if nx and user_id else {}),
        "my_roles": gate.roles_of(user_id) if user_id else [],
    }


def save_section(project_id: str, key: str, body: str, user_id: str):
    if key not in SECTION_KEYS:
        raise ValueError(f"知らない節 {key!r}")
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (project_id,))
    if p is None:
        raise LookupError("案件がありません")
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません（R-2）")
    with store.tx() as c:
        c.execute("INSERT INTO project_section (project_id,section_key,body,"
                  "updated_by,updated_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(project_id,section_key) DO UPDATE SET "
                  "body=excluded.body,updated_by=excluded.updated_by,"
                  "updated_at=excluded.updated_at",
                  (project_id, key, body, user_id, store.now_s()))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,"
                  "what) VALUES (?,?,?,?)",
                  (project_id, store.now_s(), user_id, f"節 {key} を更新"))


def save_check(project_id: str, item_key: str, done: bool, note: str, user_id: str):
    """**第3段/第4段でしか自動化できない項目**を、人が確認したと記録する。

    「無い」を「有る」に化けさせないために、誰がいつ確認したかを必ず持つ。
    """
    with store.tx() as c:
        c.execute("INSERT INTO project_check (project_id,item_key,done,note,"
                  "updated_by,updated_at) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT(project_id,item_key) DO UPDATE SET "
                  "done=excluded.done,note=excluded.note,"
                  "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                  (project_id, item_key, 1 if done else 0, note or None,
                   user_id, store.now_s()))


def upcoming(weeks: int = 4) -> list[dict]:
    t = store.today()
    end = (t + _dt.timedelta(days=weeks * 7)).isoformat()
    rs = store.q("SELECT * FROM project WHERE launch_date IS NOT NULL "
                 "AND launch_date >= ? AND launch_date <= ? "
                 "ORDER BY launch_date", (t.isoformat(), end))
    return [{"id": r["id"], "launch_date": r["launch_date"],
             "product": product_label(dict(r)), "stage": r["stage"]} for r in rs]
