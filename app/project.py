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
        ("D.cost", "原価算出のメモ（試算原価は「原価・調達」で作ります）"),
        ("D.price", "販売価格"),
        ("D.goal", "目標設定のメモ（年間目標は「年間目標」で決めます）"),
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
        ("F.code", "CIP の商品コード（登録は「CIP への登録」で）"),
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
def create(user_id: str, expand: bool = True, **f) -> dict:
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
        raise ValueError("売上に含めるなら、計上方式（全額／増分）を選んでください")
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
    # 移行（進捗管理シートから実タスクを持ち込む）では展開しない。
    # 展開すると、実際に消化した137件の完了と、雛形の未着手が二重に並ぶ
    n = expand_tasks(pid, user_id) if expand else 0
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
    # 開発タイプの**使用中の版**から展開する（版1＝種データ・2以降＝設定ページで改訂したもの・ADR-061）
    tpl = store.q("SELECT t.* FROM task_template t JOIN flow_type f ON f.code=t.flow_type "
                  "AND t.template_version=f.active_template_version WHERE t.flow_type=? ORDER BY t.seq",
                  (p["flow_type"],))
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
            "商品名は表示しません。分類（大・中・小）とサイズで表します。",
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
        # FR-101。**止めずに知らせる**（必須項目にはしない）。G3／G4 の手前で発注の締切を出す
        "warnings": _gate_warnings(p, nx),
        "stage_options": stage_options(p),
        "variant_states": VARIANT_STATES,
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


def _gate_warnings(p: dict, nx: dict | None) -> list[dict]:
    """次の関門が G3／G4 のとき、本番発注の締切が「間に合う」でなければ知らせる（FR-101）。"""
    if not nx or nx.get("gate") not in ("G3", "G4"):
        return []
    from app import cost
    d = cost.deadline(p, cost.candidates(p["id"]))
    if d["state"] == "間に合う":
        return []
    return [{"kind": "deadline", "state": d["state"],
             "text": (f"本番発注の締切に間に合いません。{d['why']}" if d["state"] == "間に合わない"
                      else f"本番発注の締切が未確定です（{d['why']}）"),
             "goto": "cost"}]


def save_section(project_id: str, key: str, body: str, user_id: str):
    if key not in SECTION_KEYS:
        raise ValueError(f"知らない節 {key!r}")
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (project_id,))
    if p is None:
        raise LookupError("案件がありません")
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
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


# ── ステージの遷移（FR-33・F-4-7）──────────────────────────────
# **1本の道。分岐は保留・中止の2つだけ。**関門と対応するステージは、その関門を通していなければ進めない
# （進んだのに関門が未通過、という食い違いを作らない）。開発タイプで対象外の関門は止めない。
MAIN_PATH = ["起票", "評価済", "候補", "年間プラン採択", "コンセプト承認",
             "開発中", "生産確定", "発売済", "追跡中", "評価完了"]
STAGE_GATE = {"評価済": "G1", "年間プラン採択": "G2", "コンセプト承認": "G3",
              "生産確定": "G4", "発売済": "G5", "評価完了": "G6"}


def stage_options(p: dict) -> dict:
    """いまのステージから行ける先と、行けない理由。"""
    from app import gate
    st = p["stage"]
    out = {"stage": st, "next": None, "next_why": None, "can_hold": False, "can_abort": False,
           "can_resume": False, "resume_to": p.get("stage_before_hold")}
    if st == "中止":
        return out
    if st == "保留":
        out["can_resume"] = bool(p.get("stage_before_hold"))
        out["can_abort"] = True
        return out
    out["can_hold"] = out["can_abort"] = True
    if st in MAIN_PATH and MAIN_PATH.index(st) + 1 < len(MAIN_PATH):
        nxt = MAIN_PATH[MAIN_PATH.index(st) + 1]
        out["next"] = nxt
        g = STAGE_GATE.get(nxt)
        if g:
            gd = next((x for x in gate.defs() if x["gate"] == g), None)
            if gd and gate.applies(gd, p.get("flow_type")):
                state, _r = gate.state_of(p["id"], g)
                if state != "通過":
                    out["next_why"] = f"{g}（{gd['name']}）を通してから進めます（いま: {state}）"
    return out


def move_stage(pid: str, action: str, user_id: str, reason_code: str = "", note: str = "") -> dict:
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    p = dict(p)
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
    o = stage_options(p)
    hold_from = p.get("stage_before_hold")
    if action == "next":
        if not o["next"]:
            raise ValueError("この先のステージはありません")
        if o["next_why"]:
            raise ValueError(o["next_why"])
        to = o["next"]
    elif action in ("hold", "abort"):
        if not (o["can_hold"] if action == "hold" else o["can_abort"]):
            raise ValueError("いまのステージからは選べません")
        table = "hold_reason" if action == "hold" else "abort_reason"
        if not reason_code or store.one(f"SELECT 1 FROM {table} WHERE code=?", (reason_code,)) is None:
            raise ValueError(("保留" if action == "hold" else "中止") + "の理由を選んでください（選択式）")
        to = "保留" if action == "hold" else "中止"
        if action == "hold":
            hold_from = p["stage"]
    elif action == "resume":
        if not o["can_resume"]:
            raise ValueError("保留中ではありません")
        to, hold_from = p["stage_before_hold"], None
    else:
        raise ValueError(f"知らない操作 {action!r}")
    with store.tx() as c:
        c.execute("UPDATE project SET stage=?,stage_before_hold=?,updated_at=?,updated_by=? WHERE id=?",
                  (to, hold_from, store.now_s(), user_id, pid))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) "
                  "VALUES (?,?,?,?,?)", (pid, store.now_s(), user_id, f"ステージ {p['stage']} → {to}",
                                         " ".join(x for x in (reason_code, (note or "").strip()) if x) or None))
    return {"ok": True, "stage": to}


# ── バリエーション（FR-37・F-4-5）。**40本の複製を作らない**（案件に内包する）──────
VARIANT_STATES = ("未対応", "対応中", "対応済", "見送り")


def save_variant(pid: str, f: dict, user_id: str) -> dict:
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
    label = (f.get("label") or "").strip()[:80]
    if not label:
        raise ValueError("本体モデル（バリエーション名）を入れてください")
    state = (f.get("state") or "未対応").strip()
    if state not in VARIANT_STATES:
        raise ValueError("状態は " + "／".join(VARIANT_STATES) + " から選んでください")
    ld = (f.get("launch_date") or "").strip() or None
    if ld:
        try:
            _dt.date.fromisoformat(ld)
        except ValueError:
            raise ValueError("発売日は YYYY-MM-DD で入れてください") from None
    spec = (f.get("spec") or "").strip()[:500] or None
    vid = f.get("id")
    with store.tx() as c:
        if vid:
            n = c.execute("UPDATE project_variant SET label=?,spec=?,state=?,launch_date=? "
                          "WHERE id=? AND project_id=?", (label, spec, state, ld, int(vid), pid)).rowcount
            if not n:
                raise LookupError("そのバリエーションはこの案件にありません")
        else:
            # 案件そのもので seisan に登録済みなら、バリエーションを後から足すと登録の単位が変わる
            reg = c.execute("SELECT product_code FROM seisan_registration WHERE project_id=? "
                            "AND variant_id IS NULL AND state='登録済'", (pid,)).fetchone()
            if reg and not c.execute("SELECT 1 FROM project_variant WHERE project_id=?", (pid,)).fetchone():
                raise ValueError(f"この案件は案件そのものとして CIP に登録済みです（{reg['product_code']}）。"
                                 "バリエーションを足すと、CIP への登録をバリエーションごとにやり直すことになります。"
                                 "先に CIP への登録の記録を取り消してください")
            if c.execute("SELECT 1 FROM project_variant WHERE project_id=? AND label=?", (pid, label)).fetchone():
                raise ValueError(f"「{label}」は既にあります")
            vid = c.execute("INSERT INTO project_variant (project_id,label,spec,state,launch_date) "
                            "VALUES (?,?,?,?,?)", (pid, label, spec, state, ld)).lastrowid
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) "
                  "VALUES (?,?,?,?,?)", (pid, store.now_s(), user_id, "バリエーション", f"{label} {state}"))
    return {"ok": True, "id": int(vid)}


def delete_variant(pid: str, vid, user_id: str) -> dict:
    """**seisan に登録済みのバリエーションは消さない**（「見送り」にする）。"""
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
    with store.tx() as c:
        v = c.execute("SELECT * FROM project_variant WHERE id=? AND project_id=?", (int(vid), pid)).fetchone()
        if v is None:
            raise LookupError("そのバリエーションはこの案件にありません")
        if c.execute("SELECT 1 FROM seisan_registration WHERE variant_id=? AND state='登録済'",
                     (int(vid),)).fetchone() or v["product_code"]:
            raise ValueError("CIP に登録済みのバリエーションは消せません。やめるときは状態を「見送り」にしてください")
        c.execute("DELETE FROM project_variant WHERE id=?", (int(vid),))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) "
                  "VALUES (?,?,?,?,?)", (pid, store.now_s(), user_id, "バリエーション削除", v["label"]))
    return {"ok": True}


# ── 売上計上（F-4-9・FR-112）。**既定は含めない**（ページリニューアルを黙って新商品売上に混ぜない）──
def set_revenue(pid: str, counted: bool, basis: str, user_id: str) -> dict:
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
    basis = (basis or "").strip() or None
    if counted and basis not in REVENUE_BASIS:
        raise ValueError("含めるときは「全額」か「増分」を選んでください（リニューアルは増分が目安）")
    if not counted:
        basis = None
    with store.tx() as c:
        c.execute("UPDATE project SET revenue_counted=?, revenue_basis=?, updated_at=?, updated_by=? WHERE id=?",
                  (1 if counted else 0, basis, store.now_s(), user_id, pid))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                  (pid, store.now_s(), user_id, "売上計上", f"含める（{basis}）" if counted else "含めない"))
    return {"ok": True}
