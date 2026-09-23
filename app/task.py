#!/usr/bin/env python3
"""
タスク（§4-5・F-5・画面設計 3-10）。

**一覧は1画面＋期間フィルタ。**「今日のタスク」〜「6日後のタスク」の
7枚の複製をやめる（B-2）。

**既定は「期限切れ＋今日」。**「今日」だけにすると、期限切れ（実運用台帳で
未着手256件）が永久に見えない。

**「期限なし」は常設ボタン。**隠すと、期限を入れない運用が固定する（88件・22%）。

**工数は発売月ではなくタスク実施月に積む**（F-3-6）。積む月は due_on の月、
無ければ start_on の月。どちらも無い行は月に積まず「期限なし」として数える。
"""
from __future__ import annotations

import datetime as _dt

from . import store
from . import plan as _plan

STATUSES = ["未着手", "着手", "完了", "保留", "対象外"]
OPEN = ("未着手", "着手", "保留")        # 完了・対象外は「詰まっているもの」ではない

TABS = {"project": "案件タスク", "work": "案件外の仕事", "request": "他部署への依頼"}

WHENS = ["overdue", "today", "+1", "+2", "+3", "+4", "+5", "+6",
         "week", "none", "all"]
DEFAULT_WHEN = "overdue+today"


def _bounds(when: str, t: _dt.date, pfx: str = ""):
    """期間フィルタを SQL の条件にする。**境界はここだけで決める。**

    `pfx` は列の前置き（`t.` / `w.`）。JOIN したときに曖昧にならないよう、
    **文字列置換で後から直さない。**ここで組み立てる。
    戻り値は (SQL断片, パラメータ)。
    """
    d = t.isoformat()
    due, st = pfx + "due_on", pfx + "status"
    if when == "overdue":
        # **完了・対象外は除く。**済んだものは詰まっていない
        return (f"{due} IS NOT NULL AND {due} < ? AND "
                f"{st} IN ('未着手','着手','保留')", [d])
    if when == "today":
        return (f"{due} = ?", [d])
    if when == DEFAULT_WHEN or not when:
        # 既定。期限切れ（未完のみ）＋今日（状態を問わない）
        return (f"({due} IS NOT NULL AND {due} <= ? AND "
                f"({due} = ? OR {st} IN ('未着手','着手','保留')))", [d, d])
    if when.startswith("+") and when[1:].isdigit():
        n = int(when[1:])
        if n > 6:
            raise ValueError("期間フィルタは +6 まで")
        return (f"{due} = ?", [(t + _dt.timedelta(days=n)).isoformat()])
    if when == "week":
        return (f"{due} >= ? AND {due} <= ?",
                [d, (t + _dt.timedelta(days=6)).isoformat()])
    if when == "none":
        return (f"{due} IS NULL", [])
    if when == "all":
        return ("1=1", [])
    raise ValueError(f"知らない期間フィルタ {when!r}")


def counts() -> dict:
    """ボタンに出す件数。**数字を押せない画面にしない。**"""
    t = store.today()
    out = {}
    for w in ["overdue", "today", "+1", "+2", "+3", "+4", "+5", "+6",
              "week", "none", "all"]:
        sql, pr = _bounds(w, t)
        n = store.val(f"SELECT COUNT(*) FROM task WHERE {sql}", pr, 0)
        n += store.val(f"SELECT COUNT(*) FROM work_item WHERE {sql}", pr, 0)
        out[w] = n
    sql, pr = _bounds(DEFAULT_WHEN, t)
    out[DEFAULT_WHEN] = (store.val(f"SELECT COUNT(*) FROM task WHERE {sql}", pr, 0)
                         + store.val(f"SELECT COUNT(*) FROM work_item WHERE {sql}",
                                     pr, 0))
    return out


def listing(when: str = DEFAULT_WHEN, tab: str = "project",
            role: str = "", assignee: str = "") -> dict:
    t = store.today()
    ep: list = []
    if tab == "project":
        sql, pr = _bounds(when or DEFAULT_WHEN, t, "t.")
        extra = ""
        if role:
            extra += " AND t.role=?"; ep.append(role)
        if assignee:
            extra += " AND t.assignee=?"; ep.append(assignee)
        rs = store.q(
            "SELECT t.*, r.label AS role_label, r.external AS role_external, "
            "p.cat1,p.cat2,p.cat3,p.size,p.launch_date "
            "FROM task t LEFT JOIN role r ON r.code=t.role "
            "LEFT JOIN project p ON p.id=t.project_id "
            f"WHERE {sql}{extra} "
            # **期限なしは最後にまとめる。**混ぜない（画面設計 3-10）
            "ORDER BY (t.due_on IS NULL), t.due_on, r.sort, p.launch_date",
            pr + ep)
        from . import project as _p
        rows = [{
            "id": r["id"], "kind": "task", "status": r["status"],
            "due_on": r["due_on"], "start_on": r["start_on"],
            "project_id": r["project_id"],
            "project": _p.product_label(dict(r)),
            "title": r["title"], "role": r["role"],
            "role_label": r["role_label"] or "—",
            "role_external": bool(r["role_external"]),
            "assignee": r["assignee"],
            "hours": r["hours"], "ai_used": bool(r["ai_used"]),
            "ai_reduction_rate": r["ai_reduction_rate"],
        } for r in rs]
    else:
        sql, pr = _bounds(when or DEFAULT_WHEN, t, "w.")
        extra = ""
        if role:
            extra += " AND w.role=?"; ep.append(role)
        if assignee:
            extra += " AND w.assignee=?"; ep.append(assignee)
        kind = "案件外" if tab == "work" else "他部署依頼"
        rs = store.q(
            "SELECT w.*, r.label AS role_label, r.external AS role_external "
            "FROM work_item w LEFT JOIN role r ON r.code=w.role "
            f"WHERE {sql} AND w.kind=?{extra} "
            "ORDER BY (w.due_on IS NULL), w.due_on, r.sort",
            pr + [kind] + ep)
        rows = [{
            "id": r["id"], "kind": "work_item", "status": r["status"],
            "due_on": r["due_on"], "start_on": r["start_on"],
            "project_id": None, "project": "—",
            "title": r["title"], "role": r["role"],
            "role_label": r["role_label"] or "—",
            "role_external": bool(r["role_external"]),
            "assignee": r["assignee"], "hours": r["hours"],
            "dept": r["dept"], "accepted_at": r["accepted_at"],
            "ai_used": False, "ai_reduction_rate": None,
        } for r in rs]

    c = counts()
    return {
        "when": when or DEFAULT_WHEN, "tab": tab, "today": t.isoformat(),
        "rows": rows, "counts": c,
        "no_due_total": c["none"],
        "overdue_total": c["overdue"],
        "load": load_by_month_role(),
        "template_totals": template_totals(),
        "notes": [
            "既定は「期限切れ＋今日」です。今日だけにすると期限切れが見えません。",
            "期限なしは最後にまとめています。期限のある行と混ぜません。",
            "工数は発売月ではなく**タスク実施月**に積みます（期限の月、無ければ開始の月）。",
        ],
    }


def set_status(kind: str, task_id: int, status: str, user_id: str,
               ai_used: str | None = None):
    if status not in STATUSES:
        raise ValueError(f"知らない進捗 {status!r}")
    table = "task" if kind == "task" else "work_item"
    done = store.now_s() if status == "完了" else None
    with store.tx() as c:
        if table == "task" and ai_used is not None:
            c.execute("UPDATE task SET status=?, done_at=?, ai_used=? WHERE id=?",
                      (status, done, 1 if ai_used in ("1", "true", "on") else 0,
                       task_id))
        else:
            c.execute(f"UPDATE {table} SET status=?, done_at=? WHERE id=?",
                      (status, done, task_id))


# ── ロール別の負荷（F-5-5）───────────────────────────────
def load_by_month_role() -> dict:
    """月 × ロール。**管理者を先頭に。**他部署は別列（自部署と混ぜない）。

    月は **タスク実施月**（F-3-6）。発売月ではない。
    現行は発売月に全工数を計上しているため、2027年発売分のコンセプト工数が
    2026年秋に発生しているのに月次負荷に載っていない。
    """
    rs = store.q(
        "SELECT substr(COALESCE(t.due_on, t.start_on),1,7) AS m, "
        "t.role AS role, r.label AS role_label, r.external AS ext, r.sort AS sort, "
        "SUM(COALESCE(t.hours,0)) AS h, COUNT(*) AS n, "
        # **実作業と予備を1つの数にしない**（F-5-7 ／ 2026-09-23）
        "SUM(CASE WHEN t.kind='予備' THEN COALESCE(t.hours,0) ELSE 0 END) AS rh "
        "FROM task t LEFT JOIN role r ON r.code=t.role "
        "WHERE COALESCE(t.due_on, t.start_on) IS NOT NULL "
        "AND t.status NOT IN ('完了','対象外') "
        "GROUP BY m, t.role ORDER BY m, r.sort")
    rs2 = store.q(
        "SELECT substr(COALESCE(w.due_on, w.start_on),1,7) AS m, "
        "w.role AS role, r.label AS role_label, r.external AS ext, r.sort AS sort, "
        "SUM(COALESCE(w.hours,0)) AS h, COUNT(*) AS n, 0 AS rh "
        "FROM work_item w LEFT JOIN role r ON r.code=w.role "
        "WHERE COALESCE(w.due_on, w.start_on) IS NOT NULL "
        "AND w.status NOT IN ('完了','対象外') "
        "GROUP BY m, w.role ORDER BY m, r.sort")
    months: dict[str, dict] = {}
    for r in list(rs) + list(rs2):
        m = months.setdefault(r["m"], {"month": r["m"], "own": [], "external": []})
        bucket = "external" if r["ext"] else "own"
        row = next((x for x in m[bucket] if x["role"] == r["role"]), None)
        if row is None:
            row = {"role": r["role"], "role_label": r["role_label"] or "—",
                   "hours": 0.0, "reserve_hours": 0.0, "n": 0,
                   "sort": r["sort"] or 99}
            m[bucket].append(row)
        row["hours"] += float(r["h"] or 0)
        row["reserve_hours"] += float(r["rh"] or 0)
        row["n"] += int(r["n"] or 0)
    for m in months.values():
        for b in ("own", "external"):
            m[b].sort(key=lambda x: x["sort"])
            for x in m[b]:
                # hours は合計。**実作業は引き算ではなく、両方を並べて出す**
                x["reserve_hours"] = round(x["reserve_hours"], 3)
                x["work_hours"] = round(x["hours"] - x["reserve_hours"], 3)
                x["hours"] = round(x["hours"], 3)
    no_due = (store.val("SELECT COUNT(*) FROM task WHERE due_on IS NULL "
                        "AND start_on IS NULL", (), 0)
              + store.val("SELECT COUNT(*) FROM work_item WHERE due_on IS NULL "
                          "AND start_on IS NULL", (), 0))
    return {
        "months": [months[k] for k in sorted(months)],
        "no_month": no_due,
        "limit": None,          # 月間工数ポイントの上限は第1段（年間プラン）で入る
        "limit_label": "—（未設定）",
        "caption": "工数は**タスク実施月**に積んでいます（発売月ではありません・F-3-6）。",
        "reserve_caption":
            "**実作業と予備時間を分けて出しています**（2026-09-23 十文字さんの決定）。"
            "予備時間は旧テンプレートの半分・1人分（名入れ 8.000h など）で、"
            "**AI削減の試算には入れません**（元の試算が予備を除外して作られているため）。",
        "external_caption": "他部署（試算対象外）。0h は「実際に0時間」ではなく、"
                            "商品開発部の削減試算から意図的に除外した値です。",
        "no_month_caption": f"期限も開始日も無い行 {no_due} 件は、どの月にも積んでいません。",
    }


def template_totals() -> dict:
    """テンプレートの標準工数。

    **「43.25h／27.28h」を出すときは必ず「6フロー合算（1本あたりではない）」を添える。**
    要件定義書自身が一度これを取り違えて訂正を入れている（§5-6）。
    """
    rs = store.q(
        "SELECT t.role, r.label AS role_label, r.sort, r.external AS ext, "
        "COUNT(*) AS n, SUM(COALESCE(t.standard_hours,0)) AS h, "
        "SUM(COALESCE(t.ai_reduction_hours,0)) AS ai, "
        "SUM(CASE WHEN t.kind='予備' THEN COALESCE(t.standard_hours,0) ELSE 0 END) AS rh "
        "FROM task_template t LEFT JOIN role r ON r.code=t.role "
        "WHERE t.template_version=1 GROUP BY t.role ORDER BY r.sort")
    per_flow = store.q(
        "SELECT t.flow_type, f.label, COUNT(*) AS n, "
        "SUM(COALESCE(t.standard_hours,0)) AS h, "
        "SUM(CASE WHEN t.kind='予備' THEN COALESCE(t.standard_hours,0) ELSE 0 END) AS rh "
        "FROM task_template t LEFT JOIN flow_type f ON f.code=t.flow_type "
        "WHERE t.template_version=1 GROUP BY t.flow_type ORDER BY f.seq")
    pf = [{"flow_type": r["flow_type"], "label": r["label"], "n": r["n"],
           "hours": round(float(r["h"] or 0), 3),
           "reserve_hours": round(float(r["rh"] or 0), 3),
           "work_hours": round(float(r["h"] or 0) - float(r["rh"] or 0), 3)}
          for r in per_flow]
    # **1本あたりの幅は実作業で出す。**予備を混ぜると幅が倍近くに見える
    hs = [x["work_hours"] for x in pf] or [0]
    return {
        "by_role": [{"role": r["role"], "role_label": r["role_label"],
                     "n": r["n"], "hours": round(float(r["h"] or 0), 3),
                     "reserve_hours": round(float(r["rh"] or 0), 3),
                     "work_hours": round(float(r["h"] or 0)
                                         - float(r["rh"] or 0), 3),
                     "ai_hours": round(float(r["ai"] or 0), 2),
                     "external": bool(r["ext"])} for r in rs],
        "by_flow": pf,
        # **ここを間違えると意味が反転する。**見出しに必ず付ける
        "caption": "6フロー合算（1本あたりではない）",
        "per_project_caption":
            f"1本あたり **実作業** {min(hs):.2f}〜{max(hs):.2f}h（フローにより幅）。"
            "単一の代表値は出しません。**予備時間は別**に数えています。",
        "reserve_caption":
            "予備時間は旧テンプレートの半分・1人分（2026-09-23 十文字さんの決定）。"
            "**AI削減の試算には入れません**（元の試算が予備を除外して作られているため）。",
        "template_version": 1,
    }


# ── ダッシュボード（§10-2 ①）────────────────────────────
def dashboard(user_id: str) -> dict:
    """**1段目は「いま詰まっているもの」。**

    毎朝開く人が最初に要るのは「何をすればいいか」。
    コンセプト在庫月数は捨てずに2段目へ置く（経営の問いとして残す）。
    """
    from . import gate, idea as _idea, project as _p
    t = store.today()
    c = counts()

    mine_sql = " AND assignee=?" if user_id else ""
    mine = [user_id] if user_id else []
    od, odp = _bounds("overdue", t)
    td, tdp = _bounds("today", t)

    # 自分のゲート待ち。**人ごとに出さないと「誰かがやる」になる**（画面設計 3-1）
    my_roles = set(gate.roles_of(user_id)) if user_id else set()
    waiting, by_role = [], {}
    for r in store.q("SELECT * FROM project WHERE stage NOT IN ('中止','評価完了')"):
        p = dict(r)
        g = gate.next_gate(p)
        if not g or g["state"] not in ("判定待ち", "差戻し"):
            continue
        for rc in g["approver_role"]:
            by_role[rc] = by_role.get(rc, 0) + 1
        if my_roles & set(g["approver_role"]):
            waiting.append({"project_id": p["id"], "product": _p.product_label(p),
                            "gate": g["gate"], "name": g["name"],
                            "state": g["state"], "missing_n": len(g["missing"])})
    role_label = {x["code"]: x["label"] for x in store.rows(
        store.q("SELECT code,label FROM role"))}

    return {
        "today": t.isoformat(),
        # ── 1段目: いま詰まっているもの ──
        "stuck": {
            "overdue": {
                "n": c["overdue"], "mine": store.val(
                    f"SELECT COUNT(*) FROM task WHERE {od}{mine_sql}",
                    odp + mine, 0) if user_id else None,
                "link": "#/tasks?when=overdue"},
            "gate_waiting": {
                "mine": len(waiting), "rows": waiting[:10],
                "by_role": [{"role": k, "label": role_label.get(k, k), "n": v}
                            for k, v in sorted(by_role.items())],
                "link": "#/gates"},
            "today": {
                "n": c["today"], "mine": store.val(
                    f"SELECT COUNT(*) FROM task WHERE {td}{mine_sql}",
                    tdp + mine, 0) if user_id else None,
                "link": "#/tasks?when=today"},
        },
        # ── 2段目: 経営の問い ──
        # コンセプト在庫月数は第1段で実装した（F-1-14）。残り2つは第1段の枠と
        # 第4段が未実装なので **「未計測」と出す**。
        # **未計測と0を区別する**（§5-9 の7）。0 と書くと「積み上がっていない」と読まれる
        "monthly": [
            # F-1-14。**定義＝ G3（コンセプト承認）通過・未発売 ÷ 月間発売目標本数。**
            # 月間目標本数が未確定なら「未計測」のまま理由を出す（N-10）
            _idea.concept_stock(),
            # FR-63。**承認済み版の今月の枠のうち、案件化した数**（F-3）。
            # 策定中の版は数えない（下書きを分母にすると枠を足すほど達成率が下がる）
            _plan.slot_consumption(),
            {"label": "発売後チェックの未処理", "value": None, "state": "未計測",
             "why": "発売後評価（post_launch_review・第4段）が未実装です。"
                    "seisan の商品コード×月×販路のエクスポートが前提で、未依頼です。"},
        ],
        "upcoming": _p.upcoming(4),
        "attention": {
            "no_due": c["none"],
            "no_hours": store.val(
                "SELECT COUNT(*) FROM task WHERE hours IS NULL", (), 0),
            "pagerenew_no_template": store.val(
                "SELECT COUNT(*) FROM project WHERE flow_type='pagerenew'", (), 0),
            "effort_unknown": store.val(
                "SELECT COUNT(*) FROM project WHERE effort_point IS NULL", (), 0),
        },
        "my_roles": sorted(my_roles),
    }
