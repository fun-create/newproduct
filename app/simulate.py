#!/usr/bin/env python3
"""
販売計画シミュレーション（F-14・FR-125〜134・2026-10-09 十文字さんの選択・ADR-072）。

「年間に何本出せば、新商品の売上目標に届くか」を、**過去の新商品の実績の分布**と**工数の上限**から試算する。
十文字さんの選択: 目標は当面は画面で入れる（経営管理に読む口を依頼）／実績の元は新商品売上状況の表／まず計算で複数案。

守ること（要件定義書 F-14・画面設計 app-ui）:
- **平均で置かない**（F-14-4）。過去の商品の「発売から12か月の売上」を**そのまま引き直して**（ブートストラップ）、
  本数ごとの年間売上の幅（下振れ P10・真ん中 P50・上振れ P90）と、目標に届く見込み（割合）を出す。
  乱数は種を固定するので、同じ入力なら必ず同じ答え
- 「目標 ÷ 本数」を単独で大きく出さない。**中央値の何倍が要るか**を並べる。0円見込みの本数も出す
- **工数を制約として解く**（F-14-5）。月ごとの枠（通常月は「月あたりの発売枠」、連休月は営業日から決めた上限）と
  月の工数ポイントの上限を超える案は、超える月を語で返す（色に意味を持たせない）
- **複数案を並べる**（F-14-6）。**確定は社長**（F-14-7）。確定した案は前提・使った実績の範囲・試算日時ごと版に残す
- 実績が無いのに埋めない（N-10）。12か月そろわない商品は分布に入れない
"""
from __future__ import annotations

import datetime as _dt
import json
import random

from app import store

DRAWS = 4000
SEED = 20261009
DEFAULT_CANDIDATES = (24, 36, 48, 52)
CONFIRMERS = ("president",)


# ── 実績の分布 ────────────────────────────────────────────
def distribution() -> dict:
    rows = store.rows(store.q("SELECT code, name, launch_date, first12_yen FROM past_product WHERE complete=1 "
                              "ORDER BY first12_yen DESC"))
    vals = sorted(r["first12_yen"] for r in rows)
    n = len(vals)
    if not n:
        return {"n": 0, "why": "過去の新商品の実績がまだ取り込まれていません（tools/import_past_products.py）"}
    total = sum(vals)

    def q(p):
        return vals[min(n - 1, int(p * (n - 1) + 0.5))]
    acc, top80 = 0.0, 0
    for v in sorted(vals, reverse=True):
        acc += v
        top80 += 1
        if acc >= total * 0.8:
            break
    incomplete = store.val("SELECT COUNT(*) FROM past_product WHERE complete=0", (), 0)
    fys = [r["launch_date"][:4] for r in rows if r["launch_date"]]
    return {"n": n, "median": q(0.5), "p25": q(0.25), "p75": q(0.75), "max": vals[-1], "zero": sum(1 for v in vals if v == 0),
            "zero_rate": round(sum(1 for v in vals if v == 0) / n, 3), "top80": top80, "total": total,
            "incomplete": incomplete, "range": f"{min(fys)}〜{max(fys)}年発売・発売から12か月がそろった {n} 商品",
            "top": rows[:5]}


def _draws(n_releases: int, vals: list[float]) -> list[float]:
    rnd = random.Random(SEED + n_releases)
    return sorted(sum(rnd.choice(vals) for _ in range(n_releases)) for _ in range(DRAWS))


# ── 工数と月の配置 ─────────────────────────────────────────
def _fy_months(fy: int) -> list[str]:
    return [f"{fy + (4 + k) // 12:04d}-{(4 + k) % 12 + 1:02d}" for k in range(12)]      # 5月〜翌4月


def default_effort_point() -> float | None:
    """1本あたりの工数ポイントの既定＝開発タイプの係数の平均（未確定のものは除く）。画面で変えられる。"""
    v = [r["effort_point"] for r in store.q("SELECT effort_point FROM flow_type WHERE effort_point IS NOT NULL")]
    return round(sum(v) / len(v), 2) if v else None


def capacities(fy: int) -> dict:
    """月ごとの枠の上限。通常月＝月あたりの発売枠、連休月＝営業日から決めた上限（ADR-066）。
    カレンダーとつながらない・登録の外の月は通常月として扱い、そう言い添える。"""
    from app import plan
    months = _fy_months(fy)
    slots = plan._num("plan.monthly_launch_slots")
    out = {"months": {m: {"cap": int(slots) if slots else None, "holiday": False} for m in months}, "note": None}
    try:
        hc = plan.holiday_caps(months)
        for m, c in hc["caps"].items():
            if m in out["months"]:
                out["months"][m] = {"cap": c["cap"], "holiday": True, "work": c["work"]}
        unk = [m for m in hc["unknown"] if m in out["months"]]
        if unk:
            out["note"] = f"カレンダーに休業日が登録されていない月（{'・'.join(unk)}）は、通常月の枠で数えています"
    except Exception as e:                              # つながらないときも試算は止めない（言い添える）
        out["note"] = f"連休月が分からないので、全部の月を通常月の枠で数えています（{e}）"
    return out


def place(n_releases: int, fy: int, effort_point: float | None) -> dict:
    """本数を12か月に配る。**いちばん少ない月から1本ずつ**（同じなら早い月から・偏らせない）。
    月の枠を超える分は配らず「入りきらない」と返す。"""
    from app import plan
    cap = capacities(fy)
    months = list(cap["months"])
    lo, hi = plan._num("plan.effort_min"), plan._num("plan.effort_max")
    count = {m: 0 for m in months}
    left = n_releases
    while left > 0:
        room = [m for m in months if cap["months"][m]["cap"] is None or count[m] < cap["months"][m]["cap"]]
        if not room:
            break
        room.sort(key=lambda m: (count[m], months.index(m)))
        count[room[0]] += 1
        left -= 1
    rows, over = [], []
    for m in months:
        c = cap["months"][m]
        eff = round(count[m] * effort_point, 2) if effort_point is not None else None
        state = "未計測" if eff is None or hi is None else ("工数の上限を超える" if eff > hi else
                                                         ("工数の下限に届かない" if lo is not None and eff < lo else "収まる"))
        if state == "工数の上限を超える":
            over.append(m)
        rows.append({"month": m, "n": count[m], "cap": c["cap"], "holiday": c["holiday"], "effort": eff, "state": state})
    return {"months": rows, "unplaced": left, "over_months": over, "effort_min": lo, "effort_max": hi,
            "note": cap["note"], "capacity": sum((c["cap"] or 0) for c in cap["months"].values())}


# ── 案を並べる ─────────────────────────────────────────────
def scenario(n_releases: int, target_yen: float | None, fy: int, effort_point: float | None) -> dict:
    d = distribution()
    vals = [r["first12_yen"] for r in store.q("SELECT first12_yen FROM past_product WHERE complete=1")]
    out = {"n": n_releases, "placement": place(n_releases, fy, effort_point)}
    if not vals:
        out["why"] = d.get("why")
        return out
    s = _draws(n_releases, vals)

    def q(p):
        return round(s[min(len(s) - 1, int(p * (len(s) - 1)))])
    out.update({"p10": q(0.1), "p50": q(0.5), "p90": q(0.9),
                "zero_expected": round(n_releases * d["zero_rate"], 1)})
    if target_yen:
        out["reach_rate"] = round(sum(1 for x in s if x >= target_yen) / len(s) * 100)
        out["need_each"] = round(target_yen / n_releases)
        out["need_x_median"] = round(target_yen / n_releases / d["median"], 1) if d["median"] else None
    pl = out["placement"]
    out["feasible"] = pl["unplaced"] == 0 and not pl["over_months"]
    out["why_not"] = (([f"枠に入りきらない {pl['unplaced']} 本"] if pl["unplaced"] else [])
                      + ([f"工数の上限を超える月 {len(pl['over_months'])} か月（{'・'.join(pl['over_months'])}）"]
                         if pl["over_months"] else []))
    return out


def compare(fy: int, target_yen: float | None, candidates=None, effort_point=None) -> dict:
    cands = sorted({int(x) for x in (candidates or DEFAULT_CANDIDATES) if 0 < int(x) <= 200})
    if not cands:
        raise ValueError("本数の候補を1つ以上入れてください（例 24,36,48）")
    ep = effort_point if effort_point is not None else default_effort_point()
    return {"fy": fy, "target_yen": target_yen, "effort_point": ep, "effort_point_default": default_effort_point(),
            "distribution": distribution(), "scenarios": [scenario(n, target_yen, fy, ep) for n in cands],
            "draws": DRAWS, "computed_at": store.now_s(),
            "method": ("過去の新商品（発売から12か月がそろったもの）の売上を、本数ぶん無作為に引き直して足す計算を "
                       f"{DRAWS} 回くり返し、年間売上の幅を出しています。平均は使っていません。"
                       "発売月によって12か月目が翌年度にかかる分も、そのまま1本の12か月として数えています")}


# ── 確定（社長）と版 ───────────────────────────────────────────
def can_confirm(user_id: str) -> bool:
    from app import gate
    return bool(set(gate.roles_of(user_id)) & set(CONFIRMERS))


def confirm(fy: int, n_releases: int, target_yen, effort_point, note: str, user_id: str, ip: str = "") -> dict:
    """案を確定して版に残す（FR-131）。**確定は社長だけ。**前提・実績の範囲・試算日時ごと残す。"""
    if not can_confirm(user_id):
        raise PermissionError("販売計画を確定できるのは、社長の業務ロールの人だけです")
    note = (note or "").strip()
    if not note:
        raise ValueError("確定の理由（なぜこの本数か）を書いてください")
    ep = effort_point if effort_point is not None else default_effort_point()
    sc = scenario(n_releases, target_yen, fy, ep)
    d = distribution()
    pid = store.new_id("sim_plan")
    params = {"fiscal_year": fy, "n_releases": n_releases, "target_yen": target_yen, "effort_point": ep,
              "draws": DRAWS, "seed": SEED + n_releases}
    with store.tx() as c:
        c.execute("UPDATE sim_plan SET state='取消' WHERE fiscal_year=? AND state='確定'", (fy,))
        c.execute("INSERT INTO sim_plan (id,fiscal_year,label,params_json,result_json,data_range,state,decided_by,decided_at,note) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (pid, fy, f"{fy}年度 {n_releases}本", json.dumps(params, ensure_ascii=False),
                   json.dumps(sc, ensure_ascii=False), d.get("range", "—"), "確定", user_id, store.now_s(), note[:500]))
    store.audit(user_id, "plan.sim.confirm", pid, {"label": f"販売計画を確定（{fy}年度 {n_releases}本）", "reason": note,
                                                   "after": n_releases}, ip)
    return {"ok": True, "id": pid}


def versions(fy: int | None = None) -> list[dict]:
    sql, p = "SELECT * FROM sim_plan", []
    if fy:
        sql += " WHERE fiscal_year=?"
        p.append(fy)
    out = []
    for r in store.q(sql + " ORDER BY decided_at DESC, rowid DESC", p):
        x = dict(r)
        x["params"] = json.loads(x.pop("params_json"))
        x["result"] = json.loads(x.pop("result_json"))
        out.append(x)
    return out


def current_fy() -> int:
    t = store.today()
    return t.year if t.month >= 5 else t.year - 1
