#!/usr/bin/env python3
"""
やること（ガイド）— 2026-11-09 十文字さん「Business Analysis に追加したガイドと同じような仕組みを NEW PRODUCT にも」・ADR-133。

Business Analysis の ADR-051（`app/guide.py`）と同じ組み立て:
- **アプリが自分で数えて出す。判定はこの1か所**（画面は描くだけ）
- いまやること（期限切れ／今やる／届いたら）・事前の準備（済／未）・これから・1年の入力カレンダー
- **見る人で出し分ける**（その人の業務ロール・承認資格に関係するものだけ。社長は全部）
- 入力が要る画面の上に「この画面でやること」（`screens` に載せた画面）
- アプリで確かめられないものだけ「済にする」（社長・商品開発部管理者。`guide_done`）
- 1つの判定が落ちても他は出し、落ちたことを出す

1件の形: {key, group(now/prep/soon), state(期限切れ/今やる/届いたら/これから/済/未), title, why, how(マニュアルの節),
          href(行き先), due(日付), roles(誰の), screens(この画面でやることに出す画面), count, manual(済にできるか)}
"""
from __future__ import annotations

import datetime as dt

from app import store

ALL = ("president", "admin", "member", "devdept", "webmkt", "prod", "se", "cs")
DEV = ("admin", "devdept", "president")
MANUAL_DONE_ROLES = ("president", "admin")


def _d(s):
    return dt.date.fromisoformat(s) if s else None


def _done(key: str) -> dict | None:
    r = store.one("SELECT * FROM guide_done WHERE key=?", (key,))
    return dict(r) if r else None


# ── 1つずつの判定（落ちても他を止めない）──────────────────────
def _my_tasks(uid, today, roles):
    from app import gate, task
    tr = gate.task_roles_of(uid)
    if not uid or not (tr or uid):
        return []
    out = []
    for kind, table, pfx in (("案件のタスク", "task t JOIN project p ON p.id=t.project_id", "t."), ("案件外の仕事", "work_item w", "w.")):
        cond, prm = task._mine_cond(uid, tr, pfx)
        extra = " AND p.stage NOT IN ('中止')" if pfx == "t." else ""
        late = store.val(f"SELECT COUNT(*) FROM {table} WHERE {cond} AND {pfx}status NOT IN ('完了','対象外') "
                         f"AND {pfx}due_on < ?{extra}", prm + [today.isoformat()], 0)
        now = store.val(f"SELECT COUNT(*) FROM {table} WHERE {cond} AND {pfx}status NOT IN ('完了','対象外') "
                        f"AND {pfx}due_on = ?{extra}", prm + [today.isoformat()], 0)
        tab = "work" if pfx == "w." else "project"
        if late:
            out.append({"key": f"mytask_late_{tab}", "group": "now", "state": "期限切れ", "count": late,
                        "title": f"期限切れの{kind}が {late} 件あります", "why": "期限が過ぎて、まだ完了・対象外になっていません",
                        "how": "タスク", "href": f"#/tasks?when=overdue&tab={tab}&mine=1", "roles": None, "screens": ["#/tasks", "#/"]})
        if now:
            out.append({"key": f"mytask_today_{tab}", "group": "now", "state": "今やる", "count": now, "due": today.isoformat(),
                        "title": f"今日が期限の{kind}が {now} 件あります", "why": "", "how": "タスク",
                        "href": f"#/tasks?when=today&tab={tab}&mine=1", "roles": None, "screens": ["#/tasks", "#/"]})
    return out


def _my_gates(uid, today, roles):
    from app import gate
    mine = set(gate.roles_of(uid))
    n, names = 0, []
    for p in store.q("SELECT * FROM project WHERE stage NOT IN ('中止','保留','発売済','追跡中','評価完了')"):
        g = gate.next_gate(dict(p))
        if g and g["state"] in ("判定待ち", "差戻し") and mine & set(g["approver_role"]):
            n += 1
            names.append(f"{p['internal_name'] or p['id']}（{g['gate']}）")
    if not n:
        return []
    return [{"key": "gate_mine", "group": "now", "state": "今やる", "count": n,
             "title": f"あなたが判定するゲートが {n} 件あります", "why": "、".join(names[:5]) + ("ほか" if n > 5 else ""),
             "how": "ゲート（G0〜G5）と発売済み", "href": "#/gates", "roles": None, "screens": ["#/gates", "#/projects", "#/"]}]


def _plan_draft(uid, today, roles):
    from app import plan
    out = []
    for v in store.q("SELECT * FROM plan_version WHERE state='策定中' ORDER BY created_at DESC"):
        out.append({"key": f"plan_draft_{v['id']}", "group": "now", "state": "今やる",
                    "title": f"年間プランの改訂下書き（{v['label']}）を確かめて承認する",
                    "why": "承認するまで、ダッシュボードの目標や枠の判定は前の承認版のままです。承認の直前に、シートの対象範囲が変わっていないかを確かめます",
                    "how": "年間プラン", "href": "#/plan", "roles": ("president",), "screens": ["#/plan", "#/"]})
    return out


def _weekly(uid, today, roles):
    """月曜の週次レポート（前週木曜締めの14日分）を取り込んだか"""
    from app import newprod
    mon = today - dt.timedelta(days=today.weekday())                  # 今週の月曜
    want_to = mon - dt.timedelta(days=4)                               # その前の木曜
    snap = newprod.latest()
    if snap and snap["period_to"] >= want_to.isoformat():
        return []
    due = mon + dt.timedelta(days=2)
    return [{"key": f"weekly_{want_to.isoformat()}", "group": "now", "state": "期限切れ" if today > due else "今やる",
             "due": due.isoformat(), "title": f"今週の週次レポート（〜{want_to.month}/{want_to.day}）を取り込む",
             "why": "月曜に ChatWork に届く「週次売上分析_….xlsx」を「売上・原価」の「新商品の売上（週次レポート）」で取り込みます"
                    + ("" if snap else "（まだ1度も取り込んでいません）"),
             "how": "新商品の売上（週次レポート）— 2026-11-09 から", "href": "#/sales", "roles": DEV, "screens": ["#/sales", "#/"]}]


def _newprod_pending(uid, today, roles):
    from app import newprod
    v = newprod.view()
    n = len(v["pending"])
    if not n:
        return []
    return [{"key": "newprod_pending", "group": "now", "state": "今やる", "count": n,
             "title": f"新商品の売上の登録待ちが {n} 件あります",
             "why": "新商品マスタに1行足したら、週次レポートの商品ラベルを選んで「登録済みにする」を押します",
             "how": "新商品の売上（週次レポート）— 2026-11-09 から", "href": "#/sales", "roles": DEV, "screens": ["#/sales"]}]


def _improve(uid, today, roles):
    out = []
    unset = store.val("SELECT COUNT(*) FROM improve_request WHERE status IN ('受付','検討中') AND priority='未設定'", (), 0)
    nobody = store.val("SELECT COUNT(*) FROM improve_request WHERE status IN ('受付','検討中','対応中') "
                       "AND (role IS NULL OR role='') AND (assignee IS NULL OR assignee='')", (), 0)
    late = store.val("SELECT COUNT(*) FROM improve_request WHERE status IN ('受付','検討中','対応中') AND due_on < ?",
                     (today.isoformat(),), 0)
    if late:
        out.append({"key": "improve_late", "group": "now", "state": "期限切れ", "count": late,
                    "title": f"期限切れの改善要望が {late} 件あります", "why": "", "how": "改善要望",
                    "href": "#/improve", "roles": DEV, "screens": ["#/improve", "#/"]})
    if unset or nobody:
        out.append({"key": "improve_triage", "group": "now", "state": "今やる", "count": max(unset, nobody),
                    "title": "改善要望の優先度・担当を決める",
                    "why": f"優先度が未設定 {unset} 件・担当部署も担当者も無い {nobody} 件。決めたら「タスクにする」でカレンダーに出せます",
                    "how": "改善要望", "href": "#/improve", "roles": DEV, "screens": ["#/improve"]})
    return out


def _followups(uid, today, roles):
    out = []
    for r in store.q("SELECT role, COUNT(*) n FROM work_item WHERE origin IS NOT NULL AND origin NOT LIKE 'improve-%' "
                     "AND status NOT IN ('完了','対象外') AND due_on < ? GROUP BY role", (today.isoformat(),)):
        out.append({"key": f"followup_late_{r['role']}", "group": "now", "state": "期限切れ", "count": r["n"],
                    "title": f"期限切れの発売後の仕事（生産確認・モール出品など）が {r['n']} 件あります", "why": "",
                    "how": "ゲート（G0〜G5）と発売済み", "href": "#/tasks?when=overdue&tab=work",
                    "roles": (r["role"],) if r["role"] else DEV, "screens": ["#/tasks"]})
    return out


def _launch_prep(uid, today, roles):
    """発売が近い案件の準備（CIP 登録・年間目標・発売予定日）"""
    out = []
    soon = (today + dt.timedelta(days=30)).isoformat()
    for p in store.q("SELECT p.*, t.annual_yen FROM project p LEFT JOIN sales_target t ON t.project_id=p.id "
                     "WHERE p.stage NOT IN ('中止','保留','発売済','追跡中','評価完了') AND p.launch_date IS NOT NULL "
                     "AND p.launch_date <= ? ORDER BY p.launch_date", (soon,)):
        name = p["internal_name"] or p["id"]
        days = (_d(p["launch_date"]) - today).days
        reg = store.one("SELECT 1 FROM seisan_registration WHERE project_id=? AND state='登録済'", (p["id"],))
        if not reg and days <= 14:
            out.append({"key": f"cip_{p['id']}", "group": "now", "state": "期限切れ" if days < 0 else "今やる",
                        "due": p["launch_date"], "title": f"{name}: CIP に商品を登録する（発売 {p['launch_date']}）",
                        "why": "G5 を通すには CIP の商品コードが要ります（カルテの「CIP への登録」）",
                        "how": "ゲート（G0〜G5）と発売済み", "href": f"#/projects/{p['id']}", "roles": DEV,
                        "screens": [f"#/projects/{p['id']}", "#/projects", "#/"]})
        if p["annual_yen"] is None:
            out.append({"key": f"target_{p['id']}", "group": "now" if days <= 14 else "soon",
                        "state": "今やる" if days <= 14 else "これから", "due": p["launch_date"],
                        "title": f"{name}: 年間目標を入れる（発売 {p['launch_date']}）",
                        "why": "G5 を通すには年間目標（方式と根拠）が要ります。新商品の売上の目標の目安にも使います",
                        "how": "ゲート（G0〜G5）と発売済み", "href": f"#/projects/{p['id']}", "roles": DEV,
                        "screens": [f"#/projects/{p['id']}"]})
    n = store.val("SELECT COUNT(*) FROM project WHERE stage IN ('開発中','生産確定','コンセプト承認') "
                  "AND launch_date IS NULL", (), 0)
    if n:
        out.append({"key": "no_launch_date", "group": "now", "state": "今やる", "count": n,
                    "title": f"発売予定日が空の開発中の案件が {n} 件あります",
                    "why": "空のままだと、タスクの日程・発売後の仕事・新商品の売上の目安が出ません", "how": "案件",
                    "href": "#/projects", "roles": DEV, "screens": ["#/projects"]})
    return out


def _upcoming(uid, today, roles):
    out = []
    to = (today + dt.timedelta(days=30)).isoformat()
    for p in store.q("SELECT * FROM project WHERE stage NOT IN ('中止','保留','発売済','追跡中','評価完了') "
                     "AND launch_date > ? AND launch_date <= ? ORDER BY launch_date", (today.isoformat(), to)):
        out.append({"key": f"launch_{p['id']}", "group": "soon", "state": "これから", "due": p["launch_date"],
                    "title": f"{p['internal_name'] or p['id']} の発売（{p['launch_date']}）",
                    "why": "G4（生産可否）と G5（発売可）を通すと発売済みになり、発売後の仕事が案件外に作られます",
                    "how": "ゲート（G0〜G5）と発売済み", "href": f"#/projects/{p['id']}", "roles": None, "screens": []})
    # 来年度の年間プラン（2〜4月）
    if today.month in (1, 2, 3, 4):
        fy = today.year if today.month >= 5 else today.year
        if not store.one("SELECT 1 FROM plan_version WHERE fiscal_year=? AND state='承認済'", (fy,)):
            out.append({"key": f"plan_next_{fy}", "group": "soon" if today.month == 1 else "now",
                        "state": "これから" if today.month == 1 else "今やる",
                        "title": f"{fy}年度（{fy}年5月〜）の年間プランを作って承認する",
                        "why": "年度は5月〜翌4月です。2〜4月のうちに作ります", "how": "年間プラン", "href": "#/plan",
                        "roles": DEV, "screens": ["#/plan"]})
    return out


# 事前の準備（修正依頼書 O01〜O05 と H06）。アプリで数えられるものは自動で済・未
def _prep(uid, today, roles):
    out = []

    def add(key, title, ok, why, how, href, roles=DEV, manual=False):
        d = _done(key) if manual else None
        out.append({"key": key, "group": "prep", "state": "済" if (ok or d) else "未", "title": title, "why": why,
                    "how": how, "href": href, "roles": roles, "screens": [href] if href != "#/" else [],
                    "manual": manual, "done_by": (d or {}).get("done_by"), "done_at": (d or {}).get("done_at")})
    n_flow = store.val("SELECT COUNT(*) FROM project WHERE (flow_type IS NULL OR flow_type='') "
                       "AND stage NOT IN ('中止','発売済','追跡中','評価完了')", (), 0)
    add("prep_flow", "未発売の案件すべてに開発タイプを入れる", n_flow == 0,
        f"空が {n_flow} 件。空だと日程の計算し直し・発売後の仕事の既定が使えません", "案件", "#/projects")
    n_unassigned = store.val("SELECT COUNT(*) FROM task t JOIN project p ON p.id=t.project_id WHERE t.status NOT IN ('完了','対象外') "
                             "AND (t.assignee IS NULL OR t.assignee='') AND p.stage NOT IN ('中止','保留') AND t.due_on IS NOT NULL "
                             "AND t.due_on <= ?", ((today + dt.timedelta(days=14)).isoformat(),), 0)
    add("prep_assign", "2週間以内が期限のタスクに担当者を入れる", n_unassigned == 0,
        f"担当者が空 {n_unassigned} 件。担当者と期限が入るとカレンダーに出ます", "タスク", "#/tasks/assign")
    n_routine = store.val("SELECT COUNT(*) FROM routine WHERE active=1", (), 0)
    add("prep_routine", "使う繰り返しの仕事（ルーティン）を選んで有効にする", n_routine > 0,
        f"有効 {n_routine} 件。使うものの時期と担当を決めて有効にします", "タスク", "#/routines")
    n_left = store.val("SELECT COUNT(*) FROM task t JOIN project p ON p.id=t.project_id WHERE p.stage IN ('発売済','追跡中') "
                       "AND t.status NOT IN ('完了','対象外')", (), 0)
    add("prep_launched_left", "発売済みの案件に残った未完了タスクを片付ける", n_left == 0,
        f"残り {n_left} 件。済んでいれば完了、要らなければ対象外にします", "案件", "#/projects?stage=_launched")
    from app import newprod
    n_np = len(newprod.view()["pending"])
    add("prep_newprod", "発売済みの新商品を、週次レポートの商品ラベルと結ぶ（1回）", n_np == 0,
        f"登録待ち {n_np} 件", "新商品の売上（週次レポート）— 2026-11-09 から", "#/sales")
    add("prep_cutover", "正本の切り替えを宣言する（スプシの更新を止めた日と、更新先をみんなに伝える）", False,
        "2026-11-09 からアプリが正本です。スプシを二重に更新しないように、止めた日と更新先を周知します。済んだら「済にする」",
        "移行の手順", "#/", roles=("president", "admin"), manual=True)
    return out


CHECKS = (_my_tasks, _my_gates, _plan_draft, _weekly, _newprod_pending, _improve, _followups, _launch_prep, _upcoming, _prep)

# 1年の入力カレンダー（いつ・何を・どこで）
CALENDAR = [
    ("毎週月曜", "週次レポート（新商品の売上）を取り込む", "売上・原価 → 新商品の売上（週次レポート）"),
    ("毎朝", "自分のやること（期限切れ・今日）を片付ける", "ダッシュボード・タスク"),
    ("発売の2か月前", "年間プランの枠を案件にする（タスクの設定期限）", "年間プラン"),
    ("発売の2週間前まで", "CIP への登録・年間目標・G4（生産可否）", "案件のカルテ"),
    ("発売日", "G5（発売可）を通す → 発売済み・発売後の仕事が案件外に", "案件のカルテ・ゲート盤"),
    ("G5 のあと", "新商品マスタに1行足して「登録済みにする」", "売上・原価"),
    ("発売の2か月後", "生産問題のヒアリングと対応（生産部）", "タスク（案件外の仕事）"),
    ("届いたら", "改善要望を受け付け、優先度・担当・期限を決めてタスクにする", "改善要望"),
    ("毎月2日（自動）", "月次レポートができる", "ダッシュボード → 月次レポート"),
    ("2〜4月", "来年度（5月〜翌4月）の年間プランを作って承認する", "年間プラン"),
]


def _visible(item, roles: set, is_president: bool) -> bool:
    if item.get("roles") is None or is_president:
        return True
    return bool(roles & set(item["roles"]))


def build(uid: str) -> dict:
    """見る人ごとのやること。**数えるのはここだけ。**"""
    from app import gate
    today = store.today()
    roles = set(gate.roles_of(uid)) if uid else set()
    is_pres = "president" in roles
    items, failed = [], []
    for f in CHECKS:
        try:
            items += f(uid, today, roles)
        except Exception as e:                      # noqa: BLE001 1つ落ちても他は出す
            failed.append(f"{f.__name__.lstrip('_')}: {type(e).__name__}")
    items = [x for x in items if _visible(x, roles, is_pres)]
    order = {"期限切れ": 0, "今やる": 1, "届いたら": 2, "未": 3, "これから": 4, "済": 5}
    items.sort(key=lambda x: (order.get(x["state"], 9), x.get("due") or "9999"))
    now = [x for x in items if x["group"] == "now"]
    prep = [x for x in items if x["group"] == "prep"]
    soon = [x for x in items if x["group"] == "soon"]
    n = len(now) + sum(1 for x in prep if x["state"] == "未")
    return {"today": today.isoformat(), "count": n, "now": now, "prep": prep, "soon": soon,
            "calendar": [{"when": a, "what": b, "where": c} for a, b, c in CALENDAR],
            "failed": failed, "can_mark": bool(roles & set(MANUAL_DONE_ROLES))}


def for_screen(uid: str, path: str) -> list[dict]:
    """「この画面でやること」"""
    b = build(uid)
    return [x for x in b["now"] + [p for p in b["prep"] if p["state"] == "未"] if path in (x.get("screens") or [])]


def mark(uid: str, key: str, done: bool) -> dict:
    from app import gate
    if not set(gate.roles_of(uid)) & set(MANUAL_DONE_ROLES):
        raise PermissionError("「済にする」は社長・商品開発部管理者の業務ロールの人です")
    with store.tx() as c:
        if done:
            c.execute("INSERT OR REPLACE INTO guide_done (key,done_at,done_by) VALUES (?,?,?)", (key[:80], store.now_s(), uid))
        else:
            c.execute("DELETE FROM guide_done WHERE key=?", (key,))
    store.audit(uid, "guide.mark", key, {"done": done})
    return {"ok": True}
