#!/usr/bin/env python3
"""
発売後の実績で採点を校正する（FR-111・F-10-6／F-10-10・ADR-101・2026-10-17）。

アイデアの採点時の軸の点（`idea_score.axes`）と、その案件の**発売から12か月の売上（税込）**を突き合わせ、
軸ごとに「点が高いほど売れたか」（順位相関）を出す。**当たった軸・外した軸を決めるのは人**で、
その判断を `calibration_note` に残す（rubric へのフィードバック）。アプリは重みを勝手に変えない。

守っていること:
- 突き合わせられるのは、発売済みで・アイデアにつながっていて・点があって・12か月の売上がそろった案件だけ。
  足りないものは**理由を数えて出す**（N-10）。件数が MIN_N に満たないうちは相関を出さず「未計測」
- 売上は `sales.project_sales`（CIP の共通商品コード → 店の商品番号 → 売上フィード）。Amazon は入っていない（そう書く）
- v1 と v2 の点は**混ぜない**。版ごとに分けて出す
"""
from __future__ import annotations

import datetime as dt
import json

from app import store

MIN_N = 8                      # これより少ないと順位相関は出さない（偶然で ±0.5 が出る）
LAUNCHED = ("発売済", "追跡中", "評価完了")
EDITORS = ("devdept", "admin", "president")
VERDICTS = ("当たった", "外した", "保留")


def can_note(user_id: str) -> bool:
    from app import gate
    return bool(set(gate.roles_of(user_id)) & set(EDITORS))


def _first12(ps: dict, launch: str, today: dt.date) -> dict:
    """発売から12か月の売上（全店の合計・税込）。12か月そろっていなければ complete=False。"""
    ld = dt.date.fromisoformat(launch)
    end = ld + dt.timedelta(days=365)
    months = set()
    m = f"{ld.year:04d}-{ld.month:02d}"
    for _ in range(12):
        months.add(m)
        y, mm = int(m[:4]), int(m[5:7]) + 1
        m = f"{y + (mm - 1) // 12:04d}-{(mm - 1) % 12 + 1:02d}"
    total = 0.0
    for s in ps.get("sites") or []:
        for x in s.get("months") or []:
            if x["month"] in months:
                total += float(x["revenue"] or 0)
    return {"yen": round(total), "complete": today >= end, "until": end.isoformat(),
            "days": max(0, min(365, (today - ld).days))}


def pairs(today: dt.date | None = None) -> dict:
    from app import sales
    today = today or store.today()
    rows, skipped = [], {"アイデアにつながっていない": 0, "点がない": 0, "売上が未計測": 0, "12か月に満たない": 0}
    for p in store.q("SELECT p.id, p.idea_id, p.internal_name, p.launch_date, p.stage, i.title AS idea_title "
                     "FROM project p LEFT JOIN idea i ON i.id=p.idea_id "
                     "WHERE p.stage IN (%s) AND p.launch_date IS NOT NULL ORDER BY p.launch_date"
                     % ",".join("?" * len(LAUNCHED)), LAUNCHED):
        if not p["idea_id"]:
            skipped["アイデアにつながっていない"] += 1
            continue
        sc = store.one("SELECT * FROM idea_score WHERE idea_id=? ORDER BY (rubric_version='v2') DESC, scored_at DESC LIMIT 1",
                       (p["idea_id"],))
        if sc is None:
            skipped["点がない"] += 1
            continue
        try:
            ps = sales.project_sales(p["id"])
        except LookupError:
            ps = {"why": "案件がありません"}
        if ps.get("why"):
            skipped["売上が未計測"] += 1
            rows.append({"project_id": p["id"], "name": p["internal_name"] or p["id"], "idea_id": p["idea_id"],
                         "idea_title": p["idea_title"], "launch_date": p["launch_date"], "version": sc["rubric_version"],
                         "axes": json.loads(sc["axes"] or "{}"), "total": sc["total"], "rank": sc["rank"],
                         "actual": None, "why": ps["why"]})
            continue
        a = _first12(ps, p["launch_date"], today)
        if not a["complete"]:
            skipped["12か月に満たない"] += 1
        rows.append({"project_id": p["id"], "name": p["internal_name"] or p["id"], "idea_id": p["idea_id"],
                     "idea_title": p["idea_title"], "launch_date": p["launch_date"], "version": sc["rubric_version"],
                     "axes": json.loads(sc["axes"] or "{}"), "total": sc["total"], "rank": sc["rank"],
                     "actual": a, "why": None if a["complete"] else f"発売から {a['days']} 日（12か月そろうのは {a['until']}）"})
    launched_n = store.val("SELECT COUNT(*) FROM project WHERE stage IN (%s)" % ",".join("?" * len(LAUNCHED)), LAUNCHED, 0)
    return {"rows": rows, "skipped": skipped, "launched": launched_n,
            "usable": sum(1 for r in rows if r["actual"] and r["actual"]["complete"])}


def _ranks(vals: list[float]) -> list[float]:
    """同点は平均順位。"""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None                      # 全部同点＝並べられない
    return round(sxy / (sxx * syy) ** 0.5, 3)


def word(rho: float | None) -> str:
    if rho is None:
        return "並べられない"
    if rho >= 0.5:
        return "当たっている（点が高いほど売れた）"
    if rho >= 0.2:
        return "弱い"
    if rho > -0.2:
        return "関係が見えない"
    return "逆（点が高いほど売れていない）"


def axis_check(pr: dict | None = None) -> dict:
    """版ごと・軸ごとの順位相関。MIN_N に満たない版は未計測。"""
    from app import idea
    pr = pr or pairs()
    usable = [r for r in pr["rows"] if r["actual"] and r["actual"]["complete"]]
    out = []
    labels = {}
    for rb in idea.rubrics():
        labels[rb["version"]] = {a["code"]: a["label"] for a in rb["axes"]}
    for ver in sorted({r["version"] for r in usable}):
        rs = [r for r in usable if r["version"] == ver]
        ys = [float(r["actual"]["yen"]) for r in rs]
        axes = sorted({k for r in rs for k in r["axes"]})
        rec = {"version": ver, "n": len(rs), "state": "計測" if len(rs) >= MIN_N else "未計測",
               "why": None if len(rs) >= MIN_N else f"{len(rs)} 件。{MIN_N} 件そろうまで相関は出しません（少ない件数では偶然で大きく出ます）",
               "axes": []}
        for code in axes + ["total"]:
            xs = [float(r["axes"].get(code) if code != "total" else r["total"] or 0) for r in rs]
            rho = spearman(xs, ys) if len(rs) >= MIN_N else None
            rec["axes"].append({"code": code, "label": "総合点" if code == "total" else labels.get(ver, {}).get(code, code),
                                "rho": rho, "word": word(rho) if len(rs) >= MIN_N else "未計測"})
        out.append(rec)
    return {"versions": out, "min_n": MIN_N}


def notes() -> list[dict]:
    return store.rows(store.q("SELECT * FROM calibration_note ORDER BY id DESC LIMIT 100"))


def add_note(f: dict, user_id: str) -> dict:
    if not can_note(user_id):
        raise PermissionError("採点の校正を記録できるのは、商品開発部・管理者・社長の業務ロールの人です")
    ver = (f.get("rubric_version") or "").strip()
    if not store.one("SELECT 1 FROM rubric WHERE version=?", (ver,)):
        raise ValueError("採点の版を選んでください")
    axis = (f.get("axis") or "").strip()
    if axis != "total" and not store.one("SELECT 1 FROM rubric_axis WHERE rubric_version=? AND code=?", (ver, axis)):
        raise ValueError("軸を選んでください")
    verdict = (f.get("verdict") or "").strip()
    if verdict not in VERDICTS:
        raise ValueError("判断は 当たった／外した／保留 から選んでください")
    reason = (f.get("reason") or "").strip()[:300]
    if not reason:
        raise ValueError("理由を書いてください（後で見返すため・必須）")
    n = f.get("n_pairs")
    rho = f.get("rho")
    with store.tx() as c:
        nid = c.execute("INSERT INTO calibration_note (rubric_version,axis,verdict,reason,n_pairs,rho,noted_by,noted_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (ver, axis, verdict, reason, int(n) if str(n or "").isdigit() else None,
                         float(rho) if rho not in (None, "") else None, user_id, store.now_s())).lastrowid
    return {"ok": True, "id": nid}


def view(user_id: str = "") -> dict:
    pr = pairs()
    return {"pairs": pr, "check": axis_check(pr), "notes": notes(), "can_note": bool(user_id) and can_note(user_id),
            "verdicts": list(VERDICTS)}
