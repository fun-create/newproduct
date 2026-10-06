#!/usr/bin/env python3
"""
年間目標（第4段・F-10-8／F-10-9・FR-109/110）。

- **自由入力にしない。**3方式のどれかを選び、**根拠を必須**にする
  （実績の分布は中央値 ¥69,135・上位23商品で80%。平均で置くとほぼ全商品が未達になる）
- **未設定は「目標未設定」**。0 と書かない
- 単位は**税込の商品代**（実績＝売上フィードと同じ土台）。目標は「発売から1年」
"""
from __future__ import annotations

import datetime as _dt

from app import store

METHODS = {
    "類似商品法": "似ている既存商品の実績から（どの商品の、どの期間の実績か）",
    "積み上げ法": "サイト×月の想定を積み上げて（どのサイトで月いくつ・単価いくら）",
    "逆算法": "新商品枠の配分から逆算して（年間プランの枠・配分の出どころ）",
}
BASIS_MIN = 10     # 根拠が一言で済まないように


def get(pid: str) -> dict | None:
    r = store.one("SELECT * FROM sales_target WHERE project_id=?", (pid,))
    return dict(r) if r else None


def save(pid: str, f: dict, user_id: str) -> dict:
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
    method = (f.get("method") or "").strip()
    if method not in METHODS:
        raise ValueError("方式は " + "／".join(METHODS) + " から選んでください（自由入力にしない）")
    basis = (f.get("basis") or "").strip()[:2000]
    if len(basis) < BASIS_MIN:
        raise ValueError(f"根拠を書いてください（{METHODS[method]}）")
    raw = str(f.get("annual_yen") or "").replace(",", "").strip()
    if not raw:
        raise ValueError("年間目標の金額を入れてください（決まらないなら保存しない＝「目標未設定」のまま）")
    try:
        yen = float(raw)
    except ValueError:
        raise ValueError("年間目標は数字で入れてください") from None
    if yen <= 0:
        raise ValueError("年間目標は 0 より大きい金額で入れてください（未設定を 0 と書かない）")
    with store.tx() as c:
        c.execute("INSERT INTO sales_target (project_id,annual_yen,method,basis,updated_by,updated_at) "
                  "VALUES (?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
                  "annual_yen=excluded.annual_yen,method=excluded.method,basis=excluded.basis,"
                  "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                  (pid, yen, method, basis, user_id, store.now_s()))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) "
                  "VALUES (?,?,?,?,?)", (pid, store.now_s(), user_id, "年間目標",
                                         f"{method} {yen:,.0f}円"))
    return {"ok": True}


def gate_state(pid: str) -> tuple[bool, str]:
    t = get(pid)
    if not t or t["annual_yen"] is None:
        return False, "目標未設定"
    return True, f"{t['method']} {t['annual_yen']:,.0f}円（根拠あり）"


def progress(pid: str, launch_date: str | None, actual: float | None,
             today: _dt.date | None = None) -> dict:
    """発売からの実績 ÷ 年間目標。**経過日数で按分した目安も出す**（発売直後に低く見えるため）。"""
    t = get(pid)
    if not t or t["annual_yen"] is None:
        return {"state": "目標未設定", "methods": METHODS}
    out = {"state": "設定済", "annual_yen": t["annual_yen"], "method": t["method"],
           "basis": t["basis"], "updated_at": t["updated_at"], "updated_by": t["updated_by"],
           "methods": METHODS}
    if actual is None or not launch_date:
        return {**out, "rate": None, "pace_rate": None}
    today = today or store.today()
    days = max(0, min(365, (today - _dt.date.fromisoformat(launch_date)).days))
    expected = t["annual_yen"] * days / 365 if days else None
    return {**out, "actual": actual, "days": days,
            "rate": round(actual / t["annual_yen"] * 100, 1),
            "pace_rate": round(actual / expected * 100, 1) if expected else None}
