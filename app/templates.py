#!/usr/bin/env python3
"""
標準タスクのひな形と開発タイプの係数を、設定ページで改訂する（2026-10-06 十文字さんの選択・ADR-061）。

- **版1は種データ専用**（起動のたびに seed が書き戻す）。画面で作る版は2から
- 流れ: 使用中の版を写して**下書き**を作る（⑦のように行が無ければ空から）→ 行を足す・直す・消す・並べ替える →
  いまの版との差を見て**使い始める**。それ以降に起こす案件から新しい版を使う
- **起こし済みの案件のタスクは変えない**（展開済みなら何もしない・F-5-6）
- 使い始めるには**実作業の全行に標準時間が要る**（空欄を 0 として合計しない・N-10）
- **予備時間の行は写すが、ここでは変えない**（半分・1人分は十文字さんの決定・ADR-028）
- 戻すときは消さずに「ひとつ前の版に戻す」（使用中の印を付け替えるだけ）
- 工数ポイント係数（⑤資材リニューアルは未確定）も、根拠つきでここで決める。決めた行は seed が書き戻さない
- 変えられるのは 商品開発部・管理者・社長 の業務ロール。記録は監査ログ（template.* / flow.*）に残す
"""
from __future__ import annotations

from app import store

EDITORS = ("devdept", "admin", "president")
RESERVE = "予備"


def can_edit(user_id: str) -> bool:
    from app import gate
    return bool(set(gate.roles_of(user_id)) & set(EDITORS))


def _need(user_id: str):
    if not can_edit(user_id):
        raise PermissionError("ひな形と係数を変えられるのは、商品開発部・管理者・社長の業務ロールの人です")


def _flow(code: str) -> dict:
    f = store.one("SELECT * FROM flow_type WHERE code=?", (code,))
    if f is None:
        raise LookupError("その開発タイプはありません")
    return dict(f)


def _rows(code: str, v: int) -> list[dict]:
    return store.rows(store.q("SELECT t.*, r.label AS role_label FROM task_template t LEFT JOIN role r ON r.code=t.role "
                              "WHERE t.flow_type=? AND t.template_version=? ORDER BY t.seq", (code, v)))


def _sum(rows):
    work = [r for r in rows if r["kind"] != RESERVE]
    return {"n": len(work), "work_hours": round(sum(r["standard_hours"] or 0 for r in work), 3),
            "reserve_hours": round(sum(r["standard_hours"] or 0 for r in rows if r["kind"] == RESERVE), 3),
            "unknown_rows": sum(1 for r in work if r["standard_hours"] is None)}


def _draft(code: str):
    return store.one("SELECT * FROM template_version WHERE flow_type=? AND state='下書き'", (code,))


def overview() -> dict:
    out = []
    for f in store.q("SELECT * FROM flow_type ORDER BY seq"):
        f = dict(f)
        cur = _rows(f["code"], f["active_template_version"])
        d = _draft(f["code"])
        out.append({"code": f["code"], "label": f["label"], "version": f["active_template_version"],
                    "has_template": bool(f["has_template"]), "effort_point": f["effort_point"],
                    "effort_point_note": f["effort_point_note"], "decided_by": f["decided_by"],
                    "decided_at": f["decided_at"], "summary": _sum(cur),
                    "draft": d["version"] if d else None,
                    "versions": store.rows(store.q("SELECT * FROM template_version WHERE flow_type=? ORDER BY version DESC",
                                                   (f["code"],)))})
    roles = store.rows(store.q("SELECT code, label FROM role ORDER BY sort"))
    return {"flows": out, "roles": roles}


def detail(code: str) -> dict:
    f = _flow(code)
    d = _draft(code)
    cur = _rows(code, f["active_template_version"])
    out = {"flow": f, "current": {"version": f["active_template_version"], "rows": cur, "summary": _sum(cur)}, "draft": None}
    if d:
        rows = _rows(code, d["version"])
        out["draft"] = {"version": d["version"], "rows": rows, "summary": _sum(rows), "diff": diff(cur, rows),
                        "created_by": d["created_by"], "created_at": d["created_at"]}
    out["roles"] = store.rows(store.q("SELECT code, label FROM role ORDER BY sort"))
    return out


def diff(old: list, new: list) -> dict:
    """足した行・消した行・時間の増減（名前とロールで突き合わせる）。"""
    key = lambda r: (r["title"], r["role"])      # noqa: E731
    o = {key(r): r for r in old if r["kind"] != RESERVE}
    n = {key(r): r for r in new if r["kind"] != RESERVE}
    changed = [{"title": k[0], "role": k[1], "before": o[k]["standard_hours"], "after": n[k]["standard_hours"]}
               for k in o.keys() & n.keys() if o[k]["standard_hours"] != n[k]["standard_hours"]]
    by_role = {}
    for side, rows in (("before", o.values()), ("after", n.values())):
        for r in rows:
            by_role.setdefault(r["role_label"] or r["role"], {"before": 0.0, "after": 0.0})[side] += r["standard_hours"] or 0
    return {"added": sorted(k[0] for k in n.keys() - o.keys()), "removed": sorted(k[0] for k in o.keys() - n.keys()),
            "changed": sorted(changed, key=lambda x: x["title"]),
            "by_role": [{"role": k, "before": round(v["before"], 3), "after": round(v["after"], 3)} for k, v in by_role.items()]}


def new_draft(code: str, user_id: str, ip: str = "") -> dict:
    """使用中の版を写して下書きを作る。**1つの開発タイプに下書きは1つだけ。**"""
    _need(user_id)
    f = _flow(code)
    if _draft(code):
        raise ValueError("この開発タイプには、もう下書きがあります（続きを直すか、捨ててから作ってください）")
    v = max(2, (store.val("SELECT MAX(template_version) FROM task_template WHERE flow_type=?", (code,), 1) or 1) + 1,
            (store.val("SELECT MAX(version) FROM template_version WHERE flow_type=?", (code,), 1) or 1) + 1)
    now = store.now_s()
    with store.tx() as c:
        c.execute("INSERT INTO template_version (flow_type,version,state,created_by,created_at) VALUES (?,?,?,?,?)",
                  (code, v, "下書き", user_id, now))
        c.execute("INSERT INTO task_template (flow_type,template_version,seq,title,role,standard_hours,ai_category,"
                  "ai_reduction_rate,ai_reduction_hours,ai_howto,source,kind) "
                  "SELECT flow_type,?,seq,title,role,standard_hours,ai_category,ai_reduction_rate,ai_reduction_hours,"
                  "ai_howto,?,kind FROM task_template WHERE flow_type=? AND template_version=?",
                  (v, f"版{f['active_template_version']}から写した（{user_id}）", code, f["active_template_version"]))
    store.audit(user_id, "template.draft", code, {"version": v, "from": f["active_template_version"]}, ip)
    return {"ok": True, "version": v}


def _draft_v(code: str) -> int:
    d = _draft(code)
    if d is None:
        raise ValueError("下書きがありません（「改訂版を作る」から始めてください）")
    return d["version"]


def _hours(v):
    s = str(v if v is not None else "").strip()
    if s == "":
        return None
    try:
        x = float(s)
    except ValueError:
        raise ValueError("標準時間は数字（h）で入れてください") from None
    if not 0 < x <= 200:
        raise ValueError("標準時間は 0 より大きく 200 以下の h で入れてください")
    return x


def save_row(code: str, f: dict, user_id: str) -> dict:
    _need(user_id)
    v = _draft_v(code)
    title = (f.get("title") or "").strip()[:120]
    role = (f.get("role") or "").strip()
    if not title:
        raise ValueError("タスク名を入れてください")
    if store.one("SELECT 1 FROM role WHERE code=?", (role,)) is None:
        raise ValueError("担当の業務ロールを選んでください")
    h = _hours(f.get("standard_hours"))
    rid = f.get("id")
    with store.tx() as c:
        if rid:
            r = c.execute("SELECT kind FROM task_template WHERE id=? AND flow_type=? AND template_version=?",
                          (int(rid), code, v)).fetchone()
            if r is None:
                raise LookupError("その行は下書きにありません")
            if r["kind"] == RESERVE:
                raise ValueError("予備時間の行はここでは変えません（半分・1人分は十文字さんの決定）")
            c.execute("UPDATE task_template SET title=?, role=?, standard_hours=? WHERE id=?", (title, role, h, int(rid)))
        else:
            seq = c.execute("SELECT COALESCE(MAX(seq),0)+1 FROM task_template WHERE flow_type=? AND template_version=? "
                            "AND kind!=?", (code, v, RESERVE)).fetchone()[0]
            # 予備の行は最後に置いてあるので、足す行はその前へ（番号を1つずつずらす）
            c.execute("UPDATE task_template SET seq=seq+1000 WHERE flow_type=? AND template_version=? AND seq>=?",
                      (code, v, seq))
            c.execute("INSERT INTO task_template (flow_type,template_version,seq,title,role,standard_hours,source,kind) "
                      "VALUES (?,?,?,?,?,?,?,'実作業')", (code, v, seq, title, role, h, f"設定ページ（{user_id}）"))
            _renumber(c, code, v)
    return {"ok": True}


def _renumber(c, code, v):
    rows = c.execute("SELECT id FROM task_template WHERE flow_type=? AND template_version=? ORDER BY seq, id",
                     (code, v)).fetchall()
    for i, r in enumerate(rows, 1):
        c.execute("UPDATE task_template SET seq=? WHERE id=?", (10000 + i, r["id"]))
    for i, r in enumerate(rows, 1):
        c.execute("UPDATE task_template SET seq=? WHERE id=?", (i, r["id"]))


def delete_row(code: str, rid, user_id: str) -> dict:
    _need(user_id)
    v = _draft_v(code)
    with store.tx() as c:
        r = c.execute("SELECT kind FROM task_template WHERE id=? AND flow_type=? AND template_version=?",
                      (int(rid), code, v)).fetchone()
        if r is None:
            raise LookupError("その行は下書きにありません")
        if r["kind"] == RESERVE:
            raise ValueError("予備時間の行はここでは消しません")
        c.execute("DELETE FROM task_template WHERE id=?", (int(rid),))
        _renumber(c, code, v)
    return {"ok": True}


def move_row(code: str, rid, up: bool, user_id: str) -> dict:
    _need(user_id)
    v = _draft_v(code)
    with store.tx() as c:
        rows = c.execute("SELECT id, kind FROM task_template WHERE flow_type=? AND template_version=? ORDER BY seq",
                         (code, v)).fetchall()
        ids = [r["id"] for r in rows if r["kind"] != RESERVE]
        i = ids.index(int(rid)) if int(rid) in ids else -1
        if i < 0:
            raise LookupError("その行は下書きにありません")
        j = i - 1 if up else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        order = ids + [r["id"] for r in rows if r["kind"] == RESERVE]
        for k, x in enumerate(order, 1):
            c.execute("UPDATE task_template SET seq=? WHERE id=?", (10000 + k, x))
        for k, x in enumerate(order, 1):
            c.execute("UPDATE task_template SET seq=? WHERE id=?", (k, x))
    return {"ok": True}


def discard(code: str, user_id: str, ip: str = "") -> dict:
    _need(user_id)
    v = _draft_v(code)
    with store.tx() as c:
        c.execute("DELETE FROM task_template WHERE flow_type=? AND template_version=?", (code, v))
        c.execute("DELETE FROM template_version WHERE flow_type=? AND version=?", (code, v))
    store.audit(user_id, "template.discard", code, {"version": v}, ip)
    return {"ok": True}


def activate(code: str, user_id: str, reason: str, ip: str = "") -> dict:
    """下書きを使い始める。**実作業の全行に標準時間が要る。**以後に起こす案件から効く。"""
    _need(user_id)
    v = _draft_v(code)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("改訂の理由を書いてください")
    rows = _rows(code, v)
    work = [r for r in rows if r["kind"] != RESERVE]
    if not work:
        raise ValueError("実作業の行が1つもありません")
    miss = [r["title"] for r in work if r["standard_hours"] is None]
    if miss:
        raise ValueError(f"標準時間が空欄の行があります（{len(miss)}行: {'、'.join(miss[:3])}…）。全行に入れてから使い始めてください")
    f = _flow(code)
    now = store.now_s()
    with store.tx() as c:
        c.execute("UPDATE template_version SET state='過去' WHERE flow_type=? AND state='使用中'", (code,))
        c.execute("UPDATE template_version SET state='使用中', note=?, activated_by=?, activated_at=? "
                  "WHERE flow_type=? AND version=?", (reason[:300], user_id, now, code, v))
        c.execute("UPDATE flow_type SET active_template_version=?, has_template=1, decided_by=?, decided_at=? WHERE code=?",
                  (v, user_id, now, code))
    store.audit(user_id, "template.activate", code, {"before": f["active_template_version"], "after": v,
                                                     "reason": reason, "label": f["label"] + " のひな形の版"}, ip)
    return {"ok": True, "version": v}


def revert(code: str, user_id: str, reason: str, ip: str = "") -> dict:
    """ひとつ前の版に戻す（消さない。使用中の印を付け替えるだけ）。"""
    _need(user_id)
    f = _flow(code)
    cur = f["active_template_version"]
    if cur <= 1:
        raise ValueError("いまは種データの版（版1）です。戻す先がありません")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("戻す理由を書いてください")
    prev = store.val("SELECT MAX(version) FROM template_version WHERE flow_type=? AND state='過去' AND version<?",
                     (code, cur), None)
    to = prev if prev else 1
    now = store.now_s()
    with store.tx() as c:
        c.execute("UPDATE template_version SET state='過去' WHERE flow_type=? AND version=?", (code, cur))
        if prev:
            c.execute("UPDATE template_version SET state='使用中' WHERE flow_type=? AND version=?", (code, prev))
        has = 1 if c.execute("SELECT COUNT(*) FROM task_template WHERE flow_type=? AND template_version=? AND kind!=?",
                             (code, to, RESERVE)).fetchone()[0] else 0
        c.execute("UPDATE flow_type SET active_template_version=?, has_template=?, decided_by=?, decided_at=? WHERE code=?",
                  (to, has, user_id, now, code))
    store.audit(user_id, "template.revert", code, {"before": cur, "after": to, "reason": reason,
                                                   "label": f["label"] + " のひな形の版"}, ip)
    return {"ok": True, "version": to}


def set_effort(code: str, value, reason: str, user_id: str, ip: str = "") -> dict:
    """工数ポイント係数を決める（⑤資材リニューアルは未確定・FR-87）。**根拠が必須。**空欄は未確定に戻す。"""
    _need(user_id)
    f = _flow(code)
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("根拠を書いてください（2026年度は⑤の実績が無く、実測できていないため）")
    s = str(value if value is not None else "").strip()
    if s == "":
        x = None
    else:
        try:
            x = float(s)
        except ValueError:
            raise ValueError("係数は数字で入れてください") from None
        if not 0 < x <= 20:
            raise ValueError("係数は 0 より大きく 20 以下で入れてください")
    now = store.now_s()
    with store.tx() as c:
        c.execute("UPDATE flow_type SET effort_point=?, effort_point_note=?, decided_by=?, decided_at=? WHERE code=?",
                  (x, reason[:300] if x is not None else f"未確定に戻した（{user_id}）: {reason[:200]}", user_id, now, code))
    store.audit(user_id, "flow.effort_point", code, {"before": f["effort_point"], "after": x, "reason": reason,
                                                     "label": f["label"] + " の工数ポイント係数"}, ip)
    return {"ok": True, "effort_point": x}
