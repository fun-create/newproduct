#!/usr/bin/env python3
"""
発売後の「試算 vs 確定」の突き合わせ（FR-93・F-7-4／F-7-8・ADR-095・2026-10-13）。

試算（この案件の試算原価の**最新の版**）と、seisan の確定原価を、**1個あたり・税抜**で比べる。

## 確定原価はどこから

seisan の口 `/api/svc/cost/actuals`（吉田さんへ依頼中）。中身は seisan の「売れ筋の分析」と同じ集計
（受注時点に受注行へ保存した原価・材料のロスの率・外注費）。**粗利・原価の規約は seisan の1か所**（FR-94）。

- **主に見るのは受注の原価**（発売日から60日の受注。確定・仮確定）。発売後に実際に売れた分の原価
- 受注がまだ無い・原価が出た行が無いときだけ、**いまのマスタの材料費**と比べる（そう書いて出す）
- 外注に出した行は、seisan の決まりで自社の材料費が 0（外注費に入る）。**直接費（材料＋外注）で比べる**のが主

## 守っていること

- **分からないものは 0 にしない。**試算に未確定の行がある・seisan で原価が出ない行がある、をそのまま出す
- 発売から60日が締まった時点の値を**1回だけ残す**（`cost_check`・書き換えない）。seisan は後から
  原価を計算し直せるので、取り直すだけだと「当時どれだけ外れたか」が残らない
- 60日が締まる前は**途中経過**として出す（残さない）
- 「外れすぎ」の線は引かない（基準が決まっていない）。差と比率を出し、比率の大きい順に並べる
"""
from __future__ import annotations

import datetime as _dt
import json

from app import cost, seisan, store

WINDOW_DAYS = 60
LAUNCHED = ("発売済", "追跡中", "評価完了")


def _d(s: str | None) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _targets(pid: str, p: dict) -> list[dict]:
    """登録済みの共通商品コードと、その発売日（バリエーションに日付があればそれ）。"""
    rs = store.q("SELECT r.product_code, r.variant_id, v.label, v.launch_date AS v_launch "
                 "FROM seisan_registration r LEFT JOIN project_variant v ON v.id = r.variant_id "
                 "WHERE r.project_id=? AND r.state='登録済' AND r.product_code IS NOT NULL "
                 "ORDER BY r.product_code", (pid,))
    return [{"code": r["product_code"], "variant": r["label"],
             "launch_date": r["v_launch"] or p["launch_date"]} for r in rs]


def estimate(pid: str) -> dict | None:
    """最新の版の1個あたり（税抜）。版が無ければ None。"""
    v = cost._latest(pid)
    if v is None:
        return None
    lines = store.rows(store.q("SELECT * FROM cost_line WHERE version_id=?", (v["id"],)))
    t = cost.totals(dict(v), lines)
    return {"version": v["version"], "confidence": v["confidence"], "made_at": v["created_at"],
            "material": t["material"]["yen"], "outsource": t["outsource"]["yen"],
            "direct": t["direct"]["yen"], "labor": t["labor"]["yen"],
            "unknown": t["direct"]["unknown"], "price_ex_tax": t["price_ex_tax"],
            "empty": t["direct"]["empty"]}


def _per(total, qty):
    if total is None or not qty:
        return None
    return round(float(total) / float(qty), 2)


def actual_view(a: dict) -> dict:
    """seisan の返事 → 1個あたり（税抜）。**どれから出したか**を basis に入れる。"""
    o, m = a.get("orders") or {}, a.get("master") or {}
    qc = o.get("qty_costed")
    if o.get("costed_lines") and qc:
        mat, oz = _per(o.get("material_costed"), qc), _per(o.get("outsource_costed"), qc)
        direct = round(mat + oz, 2) if mat is not None and oz is not None else None
        sales = _per(o.get("sales_costed"), qc)
        basis = "受注"
    else:
        mat = m.get("material_cost")
        mat = None if mat is None else round(float(mat), 2)
        oz, direct, sales = None, mat, None
        basis = "マスタ" if mat is not None else None
    return {"basis": basis, "material": mat, "outsource": oz, "direct": direct,
            "labor": _per(o.get("labor_yen"), o.get("qty_labor")) if o.get("labor_lines") else
            (None if m.get("labor_cost") is None else round(float(m["labor_cost"]), 2)),
            "sales_per_unit": sales,
            "lines": o.get("lines") or 0, "qty": o.get("qty") or 0,
            "costed_lines": o.get("costed_lines") or 0,
            "no_material_lines": o.get("no_material_lines") or 0,
            "no_oz_price_lines": o.get("no_oz_price_lines") or 0,
            "zero_material_lines": o.get("zero_material_lines") or 0,
            "outsourced_lines": o.get("outsourced_lines") or 0,
            "found": a.get("found", True)}


def diff(est: dict | None, act: dict) -> dict:
    """直接費（材料＋外注）の差。どちらかが分からなければ None（0 にしない）。"""
    e = est and est.get("direct")
    a = act.get("direct")
    if e is None or a is None or not est:
        return {"yen": None, "pct": None, "word": "比べられない"}
    y = round(a - e, 2)
    pct = round(y / e * 100, 1) if e else None
    word = "試算どおり" if abs(y) < 0.5 else ("試算より高い" if y > 0 else "試算より低い")
    return {"yen": y, "pct": pct, "word": word}


def _window(launch: str | None) -> tuple[str | None, str | None]:
    d = _d(launch)
    if d is None:
        return None, None
    return d.isoformat(), (d + _dt.timedelta(days=WINDOW_DAYS - 1)).isoformat()


def check(pid: str, user_id: str | None = None, today: _dt.date | None = None,
          record: bool = True) -> dict:
    """1案件。state は 発売前／発売日なし／コード未登録／試算なし／CIP の口なし／途中経過／60日分。"""
    today = today or store.today()
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    p = dict(p)
    est = estimate(pid)
    tg = _targets(pid, p)
    saved = {r["product_code"]: r for r in store.q("SELECT * FROM cost_check WHERE project_id=?", (pid,))}
    base = {"id": pid, "stage": p["stage"], "launch_date": p["launch_date"], "estimate": est,
            "window_days": WINDOW_DAYS, "items": [], "why": None}
    launch = _d(p["launch_date"])
    if p["stage"] not in LAUNCHED:
        return {**base, "state": "発売前", "why": "発売してから（G5 を通ってから）、発売日から60日の受注の原価と比べます"}
    if launch is None:
        return {**base, "state": "発売日なし", "why": "発売日が入っていないので、60日の期間を決められません"}
    if not tg:
        return {**base, "state": "コード未登録",
                "why": "CIP への登録（共通商品コード）がありません。登録すると、CIP の原価と比べられます"}
    if est is None or est["empty"]:
        return {**base, "state": "試算なし",
                "why": "試算原価の版（材料・外注の行）がありません。比べる相手が無いので、CIP の原価だけでは乖離を出せません"}
    # コードごとに期間が違い得る（バリエーションの発売日）。同じ期間のものはまとめて1回で聞く
    groups: dict[tuple, list[str]] = {}
    for t in tg:
        w = _window(t["launch_date"])
        if w[0] is None:
            continue
        to = min(_d(w[1]), today - _dt.timedelta(days=1))   # 今日の分は締まっていない
        if to < _d(w[0]):
            continue
        groups.setdefault((w[0], to.isoformat(), w[1]), []).append(t["code"])
    got: dict[str, dict] = {}
    err = None
    as_of = None
    for (fr, to, end), codes in groups.items():
        try:
            r = seisan.cost_actuals(codes, fr, to)
        except (seisan.NotConfigured, seisan.Refused) as e:
            err = str(e)
            break
        as_of = r.get("as_of")
        for c in codes:
            got[c] = {**(r["items"].get(c) or {"found": False, "orders": {}, "master": {}}),
                      "from": fr, "to": to, "complete": to == end}
    items = []
    for t in tg:
        s = saved.get(t["code"])
        row = {"code": t["code"], "variant": t["variant"], "launch_date": t["launch_date"]}
        if s is not None:
            sa = json.loads(s["actual_json"])
            se = json.loads(s["est_json"])
            row["recorded"] = {"at": s["recorded_at"], "by": s["recorded_by"],
                               "from": s["window_from"], "to": s["window_to"],
                               "estimate": se, "actual": actual_view(sa), "diff": diff(se, actual_view(sa))}
        a = got.get(t["code"])
        if a is not None:
            av = actual_view(a)
            row.update({"from": a["from"], "to": a["to"], "complete": a["complete"],
                        "actual": av, "diff": diff(est, av)})
            if record and a["complete"] and s is None and av["basis"] is not None:
                _record(pid, t["code"], a, est, user_id or "timer")
                row["recorded_now"] = True
                row["recorded"] = {"at": store.now_s(), "by": user_id or "timer", "from": a["from"],
                                   "to": a["to"], "estimate": est, "actual": av, "diff": diff(est, av)}
        items.append(row)
    if err and not any("recorded" in i for i in items):
        return {**base, "items": items, "state": "CIP の口なし",
                "why": f"CIP から確定原価を読めません（{err}）。口は吉田さんへ依頼中です"}
    complete = bool(items) and all(i.get("complete") or "recorded" in i for i in items)
    if not items or not any("actual" in i or "recorded" in i for i in items):
        return {**base, "items": items, "state": "途中経過",
                "why": "発売日の翌日から数えます。まだ締まった日がありません"}
    return {**base, "items": items, "as_of": as_of, "error": err,
            "state": "60日分" if complete else "途中経過",
            "why": None if complete else "発売から60日が締まるまでは途中経過です（記録はしません）"}


def _record(pid: str, code: str, a: dict, est: dict, by: str) -> None:
    keep = {"found": a.get("found"), "orders": a.get("orders"), "master": a.get("master")}
    with store.tx() as c:
        c.execute("INSERT OR IGNORE INTO cost_check (project_id,product_code,window_from,window_to,"
                  "est_json,actual_json,recorded_at,recorded_by) VALUES (?,?,?,?,?,?,?,?)",
                  (pid, code, a["from"], a["to"], json.dumps(est, ensure_ascii=False),
                   json.dumps(keep, ensure_ascii=False), store.now_s(), by))


def board(user_id: str | None = None, today: _dt.date | None = None) -> dict:
    """発売済みの案件を全部。**比率の大きい順**（比べられないものは後ろ）。"""
    today = today or store.today()
    out = []
    for p in store.q("SELECT id FROM project WHERE stage IN (%s) ORDER BY launch_date DESC"
                     % ",".join("?" * len(LAUNCHED)), LAUNCHED):
        r = check(p["id"], user_id, today)
        pr = dict(store.one("SELECT * FROM project WHERE id=?", (p["id"],)))
        from app import project as project_m
        r["product"] = project_m.display_name(pr)
        r["internal_name"] = pr["internal_name"]
        pcts = [abs(i["diff"]["pct"]) for i in r["items"] if i.get("diff") and i["diff"]["pct"] is not None]
        r["max_abs_pct"] = max(pcts) if pcts else None
        out.append(r)
    out.sort(key=lambda r: (r["max_abs_pct"] is None, -(r["max_abs_pct"] or 0), r["launch_date"] or ""))
    counts: dict[str, int] = {}
    for r in out:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"rows": out, "counts": counts, "window_days": WINDOW_DAYS}


def record_due(today: _dt.date | None = None) -> dict:
    """月次の自動実行から呼ぶ。60日が締まった分を残す（見られていなくても残るように）。"""
    b = board("timer", today)
    n = sum(1 for r in b["rows"] for i in r["items"] if i.get("recorded_now"))
    return {"projects": len(b["rows"]), "counts": b["counts"], "recorded_now": n}
