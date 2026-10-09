#!/usr/bin/env python3
"""
確定した販売計画を経営管理（keiei）へ渡す（FR-124・F-14-8・2026-10-09 十文字さんの選択・ADR-078）。

十文字さんの選択: 渡すのは**目標の金額**、**確定したら自動で渡す**。
- 目標を本数で割った1本あたりを、**発売月**（シミュレーションの月の配置）と**発売後の売れ方の形**
  （過去の新商品の 0〜11か月目の売上の割合）で月に配り、**部門の割合**（過去の売れ方・ADR-074）で部門に配る
- 月は年度をまたいでよい（年度の終わりに出した商品の売上は翌年度に入る）。行ごとに年度（fy）を付ける
- 経営管理の作法に合わせ、根拠（basis_json）を必ず付ける: どの確定か・本数・目標とその根拠・使った実績・配り方
- **渡せなくても消さない。**送る中身を `keiei_outbox` に残し、受け口ができたら送り直す（未送信／送信済／失敗）
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from app import store

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTAKE = os.environ.get("NEWPRODUCT_KEIEI_INTAKE") or ""          # 経営管理の受け口（できたら設定）
TOKEN_FILE = os.environ.get("NEWPRODUCT_KEIEI_TOKEN") or os.path.join(BASE, "config", "keiei_token")
METRIC = "revenue_newproduct"


def curve() -> list[float]:
    """発売月を 0 として 0〜11か月目の売上の割合（過去の新商品で12か月そろったもの）。合計 1。"""
    tot = [0.0] * 12
    for (mj,) in store.q("SELECT months_json FROM past_product WHERE complete=1 AND months_json IS NOT NULL"):
        for k, v in enumerate(json.loads(mj)[:12]):
            tot[k] += v or 0
    s = sum(tot)
    return [x / s for x in tot] if s else []


def _add_months(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def _fy(ym: str) -> int:
    y, m = int(ym[:4]), int(ym[5:7])
    return y if m >= 5 else y - 1


def build(sim_plan_id: str) -> dict:
    from app import simulate
    v = store.one("SELECT * FROM sim_plan WHERE id=?", (sim_plan_id,))
    if v is None:
        raise LookupError("その販売計画がありません")
    params, result = json.loads(v["params_json"]), json.loads(v["result_json"])
    target, n = params.get("target_yen"), params.get("n_releases")
    if not target or not n:
        raise ValueError("目標の金額が無い計画は渡しません（十文字さんの選択: 渡すのは目標の金額）")
    cv, shares = curve(), simulate.dept_shares()
    if not cv or not shares:
        raise ValueError("過去の新商品の売れ方（月ごと・部門ごと）が数えられないので、配れません")
    each = target / n
    by_month: dict[str, float] = {}
    for m in result.get("placement", {}).get("months", []):
        for k, w in enumerate(cv):
            ym = _add_months(m["month"], k)
            by_month[ym] = by_month.get(ym, 0) + m["n"] * each * w
    rows = []
    for ym in sorted(by_month):
        for sh in shares:
            if not sh.get("dept"):
                continue
            rows.append({"fy": _fy(ym), "month": ym, "dept_key": sh["dept"], "metric": METRIC,
                         "value": round(by_month[ym] * sh["share"])})
    basis = {"source": "newproduct", "sim_plan_id": sim_plan_id, "fiscal_year": v["fiscal_year"],
             "decided_by": v["decided_by"], "decided_at": v["decided_at"], "reason": v["note"],
             "n_releases": n, "target_yen": target, "target_basis": params.get("target_basis"),
             "past_data": v["data_range"],
             "spread": "1本あたり（目標 ÷ 本数）を、発売月の配置と、過去の新商品の発売後0〜11か月目の売上の割合で月に配り、"
                       "過去のチャネル別売上の割合で部門に配った（Amazon は自社発送と FBA を分けていない）",
             "curve": [round(x, 4) for x in cv], "dept_shares": {s["dept"]: s["share"] for s in shares if s.get("dept")}}
    return {"fiscal_year": v["fiscal_year"], "rows": rows, "basis_json": basis,
            "total": sum(r["value"] for r in rows)}


def enqueue(sim_plan_id: str) -> dict:
    """確定したときに呼ぶ。中身を残してから送る（送れなくても中身は残る）。"""
    try:
        pl = build(sim_plan_id)
    except ValueError as e:
        return {"queued": False, "why": str(e)}
    with store.tx() as c:
        oid = c.execute("INSERT INTO keiei_outbox (sim_plan_id,fiscal_year,payload,state,created_at) VALUES (?,?,?,?,?)",
                        (sim_plan_id, pl["fiscal_year"], json.dumps(pl, ensure_ascii=False), "未送信", store.now_s())).lastrowid
    return {"queued": True, "id": oid, **send(oid)}


def send(outbox_id: int, opener=None) -> dict:
    r = store.one("SELECT * FROM keiei_outbox WHERE id=?", (outbox_id,))
    if r is None:
        raise LookupError("その送り物がありません")
    if not INTAKE:
        _mark(outbox_id, "未送信", "経営管理の受け口がまだありません（依頼中）。できたら送り直します")
        return {"state": "未送信", "why": "経営管理の受け口がまだありません（依頼中）"}
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            tok = f.read().strip()
        req = urllib.request.Request(INTAKE, data=r["payload"].encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                                              "X-Actor": "newproduct/handoff"})
        with (opener or urllib.request.urlopen)(req, timeout=10) as res:
            res.read()
    except (OSError, urllib.error.URLError) as e:
        _mark(outbox_id, "失敗", f"送れませんでした: {e}")
        return {"state": "失敗", "why": str(e)}
    with store.tx() as c:
        c.execute("UPDATE keiei_outbox SET state='送信済', sent_at=?, tries=tries+1, last_error=NULL WHERE id=?",
                  (store.now_s(), outbox_id))
    return {"state": "送信済"}


def _mark(oid, state, err):
    with store.tx() as c:
        c.execute("UPDATE keiei_outbox SET state=?, tries=tries+1, last_error=? WHERE id=?", (state, err, oid))


def latest(fy: int) -> dict | None:
    r = store.one("SELECT o.* FROM keiei_outbox o JOIN sim_plan p ON p.id=o.sim_plan_id "
                  "WHERE o.fiscal_year=? AND p.state='確定' ORDER BY o.id DESC LIMIT 1", (fy,))
    if r is None:
        return None
    pl = json.loads(r["payload"])
    by_dept: dict = {}
    for x in pl["rows"]:
        k = (x["dept_key"], x["fy"])
        by_dept[k] = by_dept.get(k, 0) + x["value"]
    from app import simulate
    lab = simulate._dept_labels()
    return {"id": r["id"], "state": r["state"], "last_error": r["last_error"], "sent_at": r["sent_at"],
            "tries": r["tries"], "total": pl["total"], "rows_n": len(pl["rows"]),
            "by_dept": [{"dept": lab.get(k[0], k[0]), "fy": k[1], "value": v}
                        for k, v in sorted(by_dept.items(), key=lambda kv: (kv[0][1], -kv[1]))]}
