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
    if when == "next7":
        # 期限切れ（未完）＋7日先まで（未完）。ダッシュボードの「自分のやること」と同じ範囲（2026-10-09）
        return (f"{due} IS NOT NULL AND {due} <= ? AND {st} NOT IN ('完了','対象外')",
                [(t + _dt.timedelta(days=7)).isoformat()])
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
              "week", "next7", "none", "all"]:
        sql, pr = _bounds(w, t)
        n = store.val(f"SELECT COUNT(*) FROM task WHERE {sql}", pr, 0)
        n += store.val(f"SELECT COUNT(*) FROM work_item WHERE {sql}", pr, 0)
        out[w] = n
    sql, pr = _bounds(DEFAULT_WHEN, t)
    out[DEFAULT_WHEN] = (store.val(f"SELECT COUNT(*) FROM task WHERE {sql}", pr, 0)
                         + store.val(f"SELECT COUNT(*) FROM work_item WHERE {sql}",
                                     pr, 0))
    return out


def _mine(user_id: str, pfx: str):
    """「自分の分だけ」＝ 担当者が自分 ＋ 自分の業務ロール（ダッシュボードの「自分のやること」と同じ）。"""
    from . import gate
    roles = sorted(set(gate.roles_of(user_id)))
    cond, prm = [f"{pfx}assignee=?"], [user_id]
    if roles:
        cond.append(f"{pfx}role IN ({','.join('?' * len(roles))})")
        prm += roles
    return " AND (" + " OR ".join(cond) + ")", prm


def listing(when: str = DEFAULT_WHEN, tab: str = "project",
            role: str = "", assignee: str = "", mine: str = "") -> dict:
    t = store.today()
    ep: list = []
    if tab == "project":
        sql, pr = _bounds(when or DEFAULT_WHEN, t, "t.")
        extra = ""
        if role:
            extra += " AND t.role=?"; ep.append(role)
        if assignee:
            extra += " AND t.assignee=?"; ep.append(assignee)
        if mine:
            x, xp = _mine(mine, "t."); extra += x; ep += xp
        rs = store.q(
            "SELECT t.*, r.label AS role_label, r.external AS role_external, "
            "p.cat1,p.cat2,p.cat3,p.size,p.launch_date,p.internal_name "
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
            "project": _p.display_name(dict(r)),
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
        if mine:
            x, xp = _mine(mine, "w."); extra += x; ep += xp
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
            "created_at": r["created_at"], "received_at": r["received_at"],
            "days": handoff_days(dict(r)) if r["kind"] == "他部署依頼" else None,
            "ai_used": False, "ai_reduction_rate": None,
        } for r in rs]

    c = counts()
    return {
        "when": when or DEFAULT_WHEN, "tab": tab, "today": t.isoformat(), "mine": bool(mine),
        "rows": rows, "counts": c,
        "no_due_total": c["none"],
        "overdue_total": c["overdue"],
        "load": load_by_month_role(),
        "template_totals": template_totals(),
        "notes": [
            "既定は「期限切れ＋今日」です。今日だけにすると期限切れが見えません。",
            "期限なしは最後にまとめています。期限のある行と混ぜません。",
            "工数は発売月ではなくタスク実施月に積みます（期限の月、無ければ開始の月）。",
        ],
    }


def set_status(kind: str, task_id: int, status: str, user_id: str,
               ai_used: str | None = None):
    if status not in STATUSES:
        raise ValueError(f"知らない進捗 {status!r}")
    table = "task" if kind == "task" else "work_item"
    if table == "work_item" and status == "完了":
        r = store.one("SELECT kind, accepted_at FROM work_item WHERE id=?", (task_id,))
        if r is not None and r["kind"] == "他部署依頼" and not r["accepted_at"]:
            # **受け側の完了をもって完了**（F-11-3・FR-48）。送った側の操作だけで閉じない
            raise ValueError("他部署への依頼は、受け側の完了を記録して閉じます（「受け側が完了」）")
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
        "SUM(COALESCE(t.hours,0)) AS h, COUNT(*) AS n, SUM(t.hours IS NULL) AS hu, "
        # **実作業と予備を1つの数にしない**（F-5-7 ／ 2026-09-23）
        "SUM(CASE WHEN t.kind='予備' THEN COALESCE(t.hours,0) ELSE 0 END) AS rh "
        "FROM task t LEFT JOIN role r ON r.code=t.role "
        "WHERE COALESCE(t.due_on, t.start_on) IS NOT NULL "
        "AND t.status NOT IN ('完了','対象外') "
        "GROUP BY m, t.role ORDER BY m, r.sort")
    rs2 = store.q(
        "SELECT substr(COALESCE(w.due_on, w.start_on),1,7) AS m, "
        "w.role AS role, r.label AS role_label, r.external AS ext, r.sort AS sort, "
        "SUM(COALESCE(w.hours,0)) AS h, COUNT(*) AS n, SUM(w.hours IS NULL) AS hu, 0 AS rh "
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
                   "hours": 0.0, "reserve_hours": 0.0, "n": 0, "hours_unknown_n": 0,
                   "sort": r["sort"] or 99}
            m[bucket].append(row)
        row["hours"] += float(r["h"] or 0)
        row["reserve_hours"] += float(r["rh"] or 0)
        row["n"] += int(r["n"] or 0)
        # **時間の空いた行は 0h として合計に入っている。**何件あるかを別に持ち、画面で「合計に入っていない」と出す
        # （2026-10-09 app-ui 点検 1-3。案件外の仕事 2026-06〜08 は全行が空なのに「0h」と出ていた）
        row["hours_unknown_n"] += int(r["hu"] or 0)
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
        "caption": "工数はタスク実施月に積んでいます（発売月ではありません）。",
        "reserve_caption":
            "実作業と予備時間を分けて出しています（2026-09-23 十文字さんの決定）。"
            "予備時間は旧テンプレートの半分・1人分（名入れ 8.000h など）で、"
            "AI削減の試算には入れません（元の試算が予備を除外して作られているため）。",
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
        "SUM(CASE WHEN t.kind='予備' THEN COALESCE(t.standard_hours,0) ELSE 0 END) AS rh, "
        "SUM(t.standard_hours IS NULL) AS unknown "
        "FROM task_template t LEFT JOIN role r ON r.code=t.role "
        "JOIN flow_type fv ON fv.code=t.flow_type AND t.template_version=fv.active_template_version "
        "GROUP BY t.role ORDER BY r.sort")
    # **各開発タイプの使用中の版**で数える（ADR-061）。時間が空欄の行は 0 にせず「未定 n 行」と数える
    per_flow = store.q(
        "SELECT t.flow_type, f.label, COUNT(*) AS n, f.active_template_version AS v, "
        "SUM(COALESCE(t.standard_hours,0)) AS h, SUM(t.standard_hours IS NULL) AS unknown, "
        "SUM(CASE WHEN t.kind='予備' THEN COALESCE(t.standard_hours,0) ELSE 0 END) AS rh "
        "FROM task_template t JOIN flow_type f ON f.code=t.flow_type "
        "AND t.template_version=f.active_template_version GROUP BY t.flow_type ORDER BY f.seq")
    pf = [{"flow_type": r["flow_type"], "label": r["label"], "n": r["n"], "version": r["v"],
           "unknown_rows": r["unknown"] or 0,
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
            f"1本あたり 実作業 {min(hs):.2f}〜{max(hs):.2f}h（フローにより幅）。"
            "単一の代表値は出しません。予備時間は別に数えています。",
        "reserve_caption":
            "予備時間は旧テンプレートの半分・1人分（2026-09-23 十文字さんの決定）。"
            "AI削減の試算には入れません（元の試算が予備を除外して作られているため）。",
        "template_version": "開発タイプごとの使用中の版",
    }


# ── ダッシュボード（§10-2 ①）────────────────────────────
def _pipeline(waiting_all=None, launched_no_gate: int = 0) -> dict:
    """アイデア → 年間プランの枠 → 案件 → 発売。**割合は出さない**（つながりが記録されていないため・点検 §4）。"""
    from . import gate, idea as _idea, plan as _plan, project as _p, simulate
    ideas = {r[0]: r[1] for r in store.q("SELECT stage, COUNT(*) FROM idea GROUP BY stage")}
    fy = simulate.current_fy()
    v = _plan.current(fy)
    slots_n = store.val("SELECT COUNT(*) FROM plan_slot WHERE version_id=?", (v["id"],), 0) if v else None
    conv = store.val("SELECT COUNT(*) FROM plan_slot WHERE version_id=? AND project_id IS NOT NULL", (v["id"],), 0) if v else None
    by_stage = {r[0]: r[1] for r in store.q("SELECT stage, COUNT(*) FROM project GROUP BY stage")}
    gates: dict[str, dict] = {}
    for g in gate.defs():
        gates[g["gate"]] = {"gate": g["gate"], "name": g["name"], "n": 0}
    for r in store.q("SELECT * FROM project WHERE stage NOT IN ('中止','評価完了','発売済','追跡中')"):
        g = gate.next_gate(dict(r))
        if g and g["gate"] in gates:
            gates[g["gate"]]["n"] += 1
    launched = sum(by_stage.get(k, 0) for k in ("発売済", "追跡中", "評価完了"))
    active = sum(n for k, n in by_stage.items() if k not in ("発売済", "追跡中", "評価完了", "中止"))
    return {
        "ideas": {"total": sum(ideas.values()), "by_stage": {k: ideas.get(k, 0) for k in _idea.STAGES}},
        "slots": {"fy": fy, "version": v["label"] if v else None, "state": v["state"] if v else None,
                  "n": slots_n, "converted": conv},
        "projects": {"total": sum(by_stage.values()), "active": active, "launched": launched,
                     "by_stage": by_stage, "by_next_gate": list(gates.values()), "launched_no_gate": launched_no_gate},
        "links": {"from_idea": store.val("SELECT COUNT(*) FROM project WHERE idea_id IS NOT NULL", (), 0),
                  "projects": sum(by_stage.values()), "slot_converted": conv, "slots": slots_n},
    }


def _launch_outlook() -> dict:
    """発売の見通し: 年度の12か月の枠（発売本数に数えるもの・数えないもの・案件化）と、案件の発売予定日の入り具合。"""
    from . import idea as _idea, plan as _plan, simulate
    fy = simulate.current_fy()
    v = _plan.current(fy)
    det = _plan.detail(v["id"]) if v else None
    by = {m["month"]: m for m in (det["months"] if det else [])}
    months = []
    for k in range(12):
        y, mo = fy + (4 + k) // 12, (4 + k) % 12 + 1
        ym = f"{y:04d}-{mo:02d}"
        m = by.get(ym)
        months.append({"month": ym, "launch_n": m["launch_n"] if m else 0, "other_n": (m["n"] - m["launch_n"]) if m else 0,
                       "converted_n": m["converted_n"] if m else 0, "slots": m["n"] if m else 0,
                       "effort": m["effort"] if m else None})
    # 目標の本数は、その版を判定する値にそろえる（承認済みなら承認時の値・ADR-069）。年間プランの画面と食い違わないように
    tgt = None
    if v is not None:
        b = _plan._basis(v["id"])
        if b.get("snapshot") and b["snapshot"].get("plan.monthly_launch_slots") not in (None, ""):
            tgt = float(b["snapshot"]["plan.monthly_launch_slots"])
    if tgt is None:
        tgt = _plan._num("plan.monthly_launch_slots")
    dated = store.val("SELECT COUNT(*) FROM project WHERE launch_date IS NOT NULL AND stage NOT IN ('中止')", (), 0)
    undated = store.val("SELECT COUNT(*) FROM project WHERE launch_date IS NULL AND stage NOT IN ('中止')", (), 0)
    return {"fy": fy, "months": months, "target_per_month": tgt,
            "version": v["label"] if v else None, "state": v["state"] if v else None,
            "projects_dated": dated, "projects_undated": undated,
            "concept_stock": _idea.concept_stock(), "today_month": store.today().strftime("%Y-%m"),
            "source": f"年間プラン {fy}年度（{v['label'] if v else '版なし'}・{v['state'] if v else '—'}）"}


def _newproduct_sales() -> dict:
    """新商品（発売から12か月以内）の月別売上と発売本数。**HUB に送る値と同じ関数から**（hubmetrics.series）。"""
    from . import hubmetrics
    sr = hubmetrics.series()
    end = sr["data_end"]
    last = hubmetrics._add(sr["this_month"], -1)
    out = []
    if end:
        for ym in hubmetrics._months(hubmetrics.REVENUE_FROM, max(end, last)):
            ok = ym <= end
            out.append({"month": ym, "revenue": round(sr["rev"].get(ym, 0.0)) if ok else None,
                        "launches": sr["launches"].get(ym, 0) if ok else None})
    app_counted = store.val("SELECT COUNT(*) FROM project WHERE revenue_counted=1", (), 0)
    return {"months": out, "data_end": end, "tax": "税込",
            "source": "「新商品売上状況」の表（発売から12か月以内の新商品・税込の商品代）",
            "app_counted": app_counted}


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
    launched_no_gate = []
    for r in store.q("SELECT * FROM project WHERE stage NOT IN ('中止','評価完了')"):
        p = dict(r)
        # 発売済なのにアプリでゲートを1つも通していない（移行した案件）は、起票の判定待ちに数えない（2026-10-09 点検 1-4）
        if p["stage"] in ("発売済", "追跡中") and not store.val(
                "SELECT COUNT(*) FROM gate_review WHERE project_id=?", (p["id"],), 0):
            launched_no_gate.append(p["id"])
            continue
        g = gate.next_gate(p)
        if not g or g["state"] not in ("判定待ち", "差戻し"):
            continue
        for rc in g["approver_role"]:
            by_role[rc] = by_role.get(rc, 0) + 1
        if my_roles & set(g["approver_role"]):
            waiting.append({"project_id": p["id"], "product": _p.display_name(p),
                            "gate": g["gate"], "name": g["name"],
                            "state": g["state"], "missing_n": len(g["missing"])})
    role_label = {x["code"]: x["label"] for x in store.rows(
        store.q("SELECT code,label FROM role"))}

    # ── 0段目: 自分のやること（2026-10-06 十文字さん選択「ダッシュボードを自分のやること中心に」）──
    # **タスクは人ではなく業務ロールに付いている**（移行した 310 件はすべて担当者が空）ので、
    # 「自分の」＝ 自分が担当者のもの ＋ 自分の業務ロールのもの。期限切れ（未完）と、7日先まで
    my_tasks, my_n = [], 0
    if user_id:
        cond = ["t.assignee=?"]
        cp = [user_id]
        if my_roles:
            cond.append(f"t.role IN ({','.join('?' * len(my_roles))})")
            cp += sorted(my_roles)
        until = (t + _dt.timedelta(days=7)).isoformat()
        where = (f"t.status NOT IN ('完了','対象外') AND t.due_on IS NOT NULL AND t.due_on <= ? "
                 f"AND ({' OR '.join(cond)})")
        my_n = store.val(f"SELECT COUNT(*) FROM task t WHERE {where}", [until] + cp, 0)
        for r in store.q(f"SELECT t.*, p.cat1, p.cat2, p.cat3, p.size, p.internal_name FROM task t "
                         f"LEFT JOIN project p ON p.id=t.project_id WHERE {where} "
                         f"ORDER BY t.due_on, t.project_id, t.seq LIMIT 12", [until] + cp):
            my_tasks.append({"project_id": r["project_id"], "product": _p.display_name(dict(r)),
                             "seq": r["seq"], "title": r["title"], "due_on": r["due_on"],
                             "status": r["status"], "overdue": r["due_on"] < t.isoformat(),
                             "role_label": role_label.get(r["role"], r["role"] or "—"),
                             "by": "担当" if r["assignee"] == user_id else "ロール"})

    # 自分＝担当＋自分の業務ロール。**「自分のやること」と同じ数え方で数える**（2026-10-09 点検 1-4。下のカードは担当だけで 0 件と出ていた）
    my_counts = {"overdue": None, "today": None, "next7": None}
    if user_id:
        cond = ["assignee=?"] + ([f"role IN ({','.join('?' * len(my_roles))})"] if my_roles else [])
        cp = [user_id] + sorted(my_roles)
        base = f"status NOT IN ('完了','対象外') AND ({' OR '.join(cond)})"
        my_counts = {
            "overdue": store.val(f"SELECT COUNT(*) FROM task WHERE {base} AND due_on < ?", cp + [t.isoformat()], 0),
            "today": store.val(f"SELECT COUNT(*) FROM task WHERE {base} AND due_on = ?", cp + [t.isoformat()], 0),
            "next7": store.val(f"SELECT COUNT(*) FROM task WHERE {base} AND due_on > ? AND due_on <= ?",
                               cp + [t.isoformat(), (t + _dt.timedelta(days=7)).isoformat()], 0)}
    return {
        "today": t.isoformat(),
        "counts": c,
        "pipeline": _pipeline(waiting_all=None, launched_no_gate=len(launched_no_gate)),
        "launch_outlook": _launch_outlook(),
        "newproduct_sales": _newproduct_sales(),
        "my": {"tasks": my_tasks, "tasks_n": my_n, "gates": waiting[:10], "gates_n": len(waiting),
               "counts": my_counts, "roles": [role_label.get(x, x) for x in sorted(my_roles)]},
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
            post_launch_pending(),
        ],
        "upcoming": _p.upcoming(4),
        "attention": {
            "tasks_total": store.val("SELECT COUNT(*) FROM task", (), 0),
            "projects_total": store.val("SELECT COUNT(*) FROM project WHERE stage != '中止'", (), 0),
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


# ── 案件外の仕事・他部署への依頼の起票（FR-47・FR-48）────────────────────
WORK_KINDS = ("案件外", "他部署依頼")


def create_work_item(f: dict, user_id: str) -> dict:
    kind = (f.get("kind") or "案件外").strip()
    if kind not in WORK_KINDS:
        raise ValueError("種類は 案件外／他部署依頼 から選んでください")
    title = (f.get("title") or "").strip()[:200]
    if not title:
        raise ValueError("何をするかを入れてください")
    dept = (f.get("dept") or "").strip()[:60] or None
    if kind == "他部署依頼" and not dept:
        raise ValueError("依頼先の部署を入れてください")
    role = (f.get("role") or "").strip() or None
    if role and store.one("SELECT 1 FROM role WHERE code=?", (role,)) is None:
        raise ValueError("知らないロールです")
    due = (f.get("due_on") or "").strip() or None
    if due:
        try:
            _dt.date.fromisoformat(due)
        except ValueError:
            raise ValueError("期限は YYYY-MM-DD で入れてください") from None
    hours = (f.get("hours") or "").strip()
    try:
        hours = float(hours) if hours else None
    except ValueError:
        raise ValueError("時間は数字で入れてください") from None
    if hours is not None and hours < 0:
        raise ValueError("時間は0以上で入れてください")
    with store.tx() as c:
        wid = c.execute("INSERT INTO work_item (kind,title,category,dept,role,assignee,due_on,hours,"
                        "status,created_at) VALUES (?,?,?,?,?,?,?,?,'未着手',?)",
                        (kind, title, (f.get("category") or "").strip()[:60] or None, dept, role,
                         (f.get("assignee") or "").strip()[:60] or None, due, hours,
                         store.now_s())).lastrowid
    return {"ok": True, "id": wid}


def receive_work_item(wid: int, user_id: str) -> dict:
    """他部署への依頼を、**受け側が受け取った**と記録する（FR-117・ADR-079）。完了とは別。二度押しても最初の日時のまま。"""
    r = store.one("SELECT kind, received_at FROM work_item WHERE id=?", (wid,))
    if r is None:
        raise LookupError("その仕事はありません")
    if r["kind"] != "他部署依頼":
        raise ValueError("他部署への依頼だけに使います")
    if r["received_at"]:
        return {"ok": True, "received_at": r["received_at"]}
    now = store.now_s()
    with store.tx() as c:
        c.execute("UPDATE work_item SET received_at=?, received_by=? WHERE id=?", (now, user_id, wid))
    return {"ok": True, "received_at": now}


def handoff_days(r: dict) -> dict:
    """依頼から受領まで・受領から完了までの日数。まだなら今日までの日数（「n日たっても受け取られていない」を見せる）。"""
    import datetime as _d
    def day(s):
        return _d.date.fromisoformat(s[:10]) if s else None
    c, rv, a = day(r.get("created_at")), day(r.get("received_at")), day(r.get("accepted_at"))
    t = store.today()
    return {"to_receive": ((rv or t) - c).days if c else None, "received": bool(rv),
            "to_finish": ((a or t) - (rv or c)).days if (rv or c) else None, "finished": bool(a)}


def accept_work_item(wid: int, user_id: str) -> dict:
    """他部署への依頼を、**受け側が完了した**と記録して閉じる（FR-48）。"""
    r = store.one("SELECT kind FROM work_item WHERE id=?", (wid,))
    if r is None:
        raise LookupError("その仕事はありません")
    if r["kind"] != "他部署依頼":
        raise ValueError("他部署への依頼だけに使います")
    now = store.now_s()
    with store.tx() as c:
        # 受領を押さずに完了したときは、完了日を受領日にも入れる（受け取らずに終わることは無いため）
        c.execute("UPDATE work_item SET accepted_at=?, status='完了', done_at=?, "
                  "received_at=COALESCE(received_at, ?), received_by=COALESCE(received_by, ?) WHERE id=?",
                  (now, now, now, user_id, wid))
    return {"ok": True}


def post_launch_pending() -> dict:
    """FR-63「発売後チェックの未処理」。G5 通過で起票した発売後タスク（FR-104/105）のうち、
    **期限が来たのに完了していない**もの。

    G5 を通した案件がまだ無いなら「対象なし」（0 とは書かない。積み上がっていないのではなく、
    数える相手がいない）。移行した発売済の案件はアプリで G5 を通していないので数えない。"""
    from app import gate
    titles = [t for t, _d, _r in gate.POST_LAUNCH_TASKS]
    marks = ",".join("?" * len(titles))
    base = {"label": "発売後チェックの未処理", "link": "#/tasks?when=overdue"}
    total = store.val(f"SELECT COUNT(*) FROM task WHERE title IN ({marks})", titles, 0)
    if not total:
        return {**base, "value": None, "state": "対象なし",
                "why": "アプリで G5（発売可）を通した案件がまだありません。G5 を通すと、発売+2週・+2か月の確認タスクが起票され、ここで数えます"}
    today = store.today_s()
    n = store.val(f"SELECT COUNT(*) FROM task WHERE title IN ({marks}) AND status NOT IN ('完了','対象外') "
                  "AND due_on IS NOT NULL AND due_on <= ?", (*titles, today), 0)
    return {**base, "value": f"{n} 件", "count": n, "state": "未処理あり" if n else "なし",
            "why": f"発売後の確認タスク {total} 件のうち、期限が来て完了していないもの"}
