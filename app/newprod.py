#!/usr/bin/env python3
"""
新商品の売上（週次レポートから取り込む）— 2026-11-09 修正依頼書 H06 段階1・十文字さん「段階1を進める」・ADR-131。

**集計の正は週次レポート**（社内の Windows・`weekly_report.ps1`・毎週月曜 9:00・前週木曜締めの14日分・
`sales.csv` を新商品マスタのキーワードで拾う）。NEW PRODUCT は集計し直さない。分析 Excel の「新商品」シートの
**上の集計表と月別の表だけ**を読み、同じシートの下にある明細（商品名・メッセージ欄）は読まずに止める。ファイルは保存しない。

流れ:
1. G5 を通すと「登録待ち」に入る（`on_launched`）。新商品マスタに足す1行の案（商品ラベル・発売日・年間目標）を出す
2. 人が新商品マスタに1行足し（今の手順どおり。キーワードは sales.csv の実表記と照合）、ここで「登録済み」にして
   週次レポートの商品ラベルと結ぶ（`register`）。案件の「新商品売上レポートに計上するよう設定」のタスクは完了にする
3. 月曜の分析 Excel を取り込む（`ingest`）。案件と結んだラベルの数字がカルテと「売上・原価」に出る
"""
from __future__ import annotations

import datetime as dt
import json
import re

from app import store

PENDING, DONE = "登録待ち", "登録済み"
TASK_TITLE = "新商品売上レポートに計上するよう設定"
_HEAD = ["商品ラベル", "件数", "数量", "売上(税込)", "期間目標売上", "達成率(売上)", "期間目標数量", "達成率(数量)"]
_MONTH_ROWS = {"月間目標売上(円)": "target_sales", "売上金額(円)": "sales",
               "月間目標数量(個)": "target_qty", "販売数量(個)": "qty"}


def norm(s: str) -> str:
    return re.sub(r"[\s　（）()【】\[\]・/／※]", "", (s or "")).lower()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 取り込み ──────────────────────────────────────────
def period_of(file_name: str) -> tuple[str, str]:
    m = re.search(r"(\d{8})-(\d{8})", file_name or "")
    if not m:
        raise ValueError("ファイル名から期間が読めません（「週次売上分析_YYYYMMDD-YYYYMMDD.xlsx」の形のファイルを選んでください）")
    f = [dt.datetime.strptime(x, "%Y%m%d").date().isoformat() for x in m.groups()]
    return f[0], f[1]


def parse(rows: list[list[str]], year: int) -> dict:
    """「新商品」シートの行 → 集計表と月別の表。**明細の見出し（店名の列がある行）で止める。**"""
    if not rows or rows[0][:len(_HEAD)] != _HEAD:
        raise ValueError("「新商品」シートの1行目が週次レポートの形ではありません（商品ラベル・件数・数量・売上…）")
    out, i = [], 1
    total = None
    while i < len(rows):
        r = rows[i] + [""] * 8
        i += 1
        if not r[0]:
            break
        rec = {"label": r[0], "count": _num(r[1]), "qty": _num(r[2]), "sales": _num(r[3]),
               "target_sales": _num(r[4]), "target_qty": _num(r[6])}
        if r[0] == "合計":
            total = rec
            break
        out.append(rec)
    monthly = {}
    while i < len(rows):
        r = rows[i]
        i += 1
        if "店名" in r or "メッセージ" in r:
            break                                   # ここから下は明細。読まない
        if len(r) > 1 and r[1] == "1月":            # 月別の表の見出し: [ラベル, 1月, 2月, …]
            label = r[0]
            months = []
            for x in r[1:]:
                m = re.match(r"(\d{1,2})月", x or "")
                months.append(f"{year:04d}-{int(m.group(1)):02d}" if m else None)
            blk = {"months": [m for m in months if m]}
            for _k in range(6):
                if i >= len(rows):
                    break
                rr = rows[i]
                key = _MONTH_ROWS.get((rr[0] if rr else "").strip())
                if key is None and not (rr and rr[0].startswith("達成率")):
                    break
                i += 1
                if key:
                    blk[key] = [_num(rr[j + 1]) if j + 1 < len(rr) else None for j, m in enumerate(months) if m]
            monthly[label] = blk
    if not out:
        raise ValueError("「新商品」シートに商品の行がありません")
    return {"rows": out, "total": total, "monthly": monthly}


def ingest(file_name: str, data: bytes, user_id: str) -> dict:
    """分析 Excel（バイト列）から「新商品」シートだけ読んで保存する。**ファイルは保存しない。**"""
    import io
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import xlsx_sheet
    pf, pt = period_of(file_name)
    try:
        rows = xlsx_sheet.read_sheet(io.BytesIO(data), "新商品")
    except SystemExit as e:                         # 道具はシートが無いと SystemExit を出す
        raise ValueError(str(e)) from None
    except Exception as e:                          # noqa: BLE001 壊れたファイル
        raise ValueError(f"Excel を読めませんでした（{type(e).__name__}）") from None
    d = parse(rows, int(pt[:4]))
    with store.tx() as c:
        c.execute("DELETE FROM newprod_weekly WHERE period_from=? AND period_to=?", (pf, pt))
        c.execute("INSERT INTO newprod_weekly (period_from,period_to,file_name,rows,monthly,received_at,received_by) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (pf, pt, file_name[:200], json.dumps({"rows": d["rows"], "total": d["total"]}, ensure_ascii=False),
                   json.dumps(d["monthly"], ensure_ascii=False), store.now_s(), user_id))
    store.audit(user_id, "newprod.ingest", f"{pf}..{pt}", {"labels": len(d["rows"])})
    return {"ok": True, "period_from": pf, "period_to": pt, "labels": len(d["rows"]),
            "sales": (d["total"] or {}).get("sales")}


def latest() -> dict | None:
    r = store.one("SELECT * FROM newprod_weekly ORDER BY period_to DESC, id DESC LIMIT 1")
    if r is None:
        return None
    body = json.loads(r["rows"])
    return {"period_from": r["period_from"], "period_to": r["period_to"], "file_name": r["file_name"],
            "received_at": r["received_at"], "rows": body["rows"], "total": body.get("total"),
            "monthly": json.loads(r["monthly"])}


# ── 登録 ──────────────────────────────────────────────
def on_launched(pid: str) -> None:
    """G5 を通したら登録待ちに入れる（何度呼んでも1つ）。"""
    store.ex("INSERT OR IGNORE INTO newprod_reg (project_id,state,created_at) VALUES (?,?,?)",
             (pid, PENDING, store.now_s()))


def proposal(p: dict) -> dict:
    """新商品マスタに足す1行の案。キーワードは人が sales.csv の実表記と照合して決める（ここでは作らない）。"""
    from app import target
    t = target.get(p["id"]) or {}
    yen = t.get("annual_yen")
    return {"label": p.get("internal_name") or p["id"], "launch_date": p.get("launch_date"),
            "monthly_target_sales": round(yen / 12) if yen else None,
            "monthly_target_qty": None,                 # 年間の数量目標は持っていない（新商品マスタで人が入れる）
            "note": "キーワード（含む・除外）は sales.csv の商品名の実表記と照合して決める（統括手順.md §3）。"
                    "月別の目標は年間目標を12で割った目安"}


def register(pid: str, labels: list[str], user_id: str) -> dict:
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    labels = [x.strip()[:120] for x in labels if (x or "").strip()]
    if not labels:
        raise ValueError("週次レポートの商品ラベルを1つ以上選んでください（新商品マスタに足した名前）")
    now = store.now_s()
    with store.tx() as c:
        c.execute("INSERT INTO newprod_reg (project_id,state,labels,created_at,registered_at,registered_by) "
                  "VALUES (?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET state=excluded.state, "
                  "labels=excluded.labels, registered_at=excluded.registered_at, registered_by=excluded.registered_by",
                  (pid, DONE, json.dumps(labels, ensure_ascii=False), now, now, user_id))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                  (pid, now, user_id, "新商品の売上に登録", "週次レポートの商品ラベル: " + "、".join(labels)))
        n = c.execute("UPDATE task SET status='完了', done_at=? WHERE project_id=? AND title=? "
                      "AND status NOT IN ('完了','対象外')", (now, pid, TASK_TITLE)).rowcount
    store.audit(user_id, "newprod.register", pid, {"labels": labels, "task_done": n})
    return {"ok": True, "labels": labels, "task_done": n}


# ── 表示 ──────────────────────────────────────────────
def _sum_label_rows(rows: list[dict], labels: list[str]) -> dict | None:
    hit = [r for r in rows if r["label"] in labels]
    if not hit:
        return None
    s = lambda k: sum(r[k] or 0 for r in hit) if any(r[k] is not None for r in hit) else None  # noqa: E731
    out = {k: s(k) for k in ("count", "qty", "sales", "target_sales", "target_qty")}
    out["rate"] = (out["sales"] / out["target_sales"]) if out["sales"] is not None and out["target_sales"] else None
    return out


def _sum_monthly(monthly: dict, labels: list[str]) -> dict | None:
    blks = [monthly[x] for x in labels if x in monthly]
    if not blks:
        return None
    months = sorted({m for b in blks for m in b.get("months", [])})
    out = {"months": months}
    for k in ("sales", "qty", "target_sales", "target_qty"):
        vals = []
        for m in months:
            v = [b[k][b["months"].index(m)] for b in blks if k in b and m in b["months"]
                 and b["months"].index(m) < len(b[k]) and b[k][b["months"].index(m)] is not None]
            vals.append(sum(v) if v else None)
        out[k] = vals
    out["cumulative_sales"] = sum(x for x in out["sales"] if x) if any(out["sales"]) else 0
    return out


def view(within_days: int = 365) -> dict:
    """「売上・原価」の新商品の売上。発売から1年以内の案件と、週次レポートの最新の数字。"""
    snap = latest()
    today = store.today()
    since = (today - dt.timedelta(days=within_days)).isoformat()
    regs = {r["project_id"]: dict(r) for r in store.q("SELECT * FROM newprod_reg")}
    projects = store.rows(store.q(
        "SELECT id, internal_name, launch_date, stage FROM project WHERE stage IN ('発売済','追跡中','評価完了') "
        "AND launch_date IS NOT NULL AND launch_date >= ? ORDER BY launch_date DESC", (since,)))
    ids = {p["id"] for p in projects}
    for pid in regs:                                  # G5 を通したが発売日が1年より前、は出さない。発売日の無い登録待ちは出す
        if pid not in ids:
            p = store.one("SELECT id, internal_name, launch_date, stage FROM project WHERE id=?", (pid,))
            if p is not None and (not p["launch_date"] or p["launch_date"] >= since):
                projects.append(dict(p))
    labels_used, rows, pending = set(), [], []
    for p in projects:
        g = regs.get(p["id"])
        labels = json.loads(g["labels"]) if g and g["labels"] else []
        if not g or g["state"] != DONE:
            pr = proposal(dict(store.one("SELECT * FROM project WHERE id=?", (p["id"],))))
            cand = [r["label"] for r in (snap["rows"] if snap else [])
                    if norm(r["label"]) and (norm(r["label"]) in norm(p["internal_name"] or "")
                                             or norm(p["internal_name"] or "") in norm(r["label"]))]
            pending.append({**p, "proposal": pr, "candidates": cand})
            continue
        labels_used.update(labels)
        rows.append({**p, "labels": labels,
                     "period": _sum_label_rows(snap["rows"], labels) if snap else None,
                     "monthly": _sum_monthly(snap["monthly"], labels) if snap else None,
                     "elapsed_days": (today - dt.date.fromisoformat(p["launch_date"])).days if p["launch_date"] else None})
    others = [r for r in (snap["rows"] if snap else []) if r["label"] not in labels_used]
    return {"snapshot": ({k: snap[k] for k in ("period_from", "period_to", "file_name", "received_at", "total")}
                         if snap else None),
            "rows": rows, "pending": pending, "unlinked_labels": others,
            "all_labels": [r["label"] for r in (snap["rows"] if snap else [])],
            "within_days": within_days}


def of_project(pid: str) -> dict:
    """カルテに出す1行。"""
    g = store.one("SELECT * FROM newprod_reg WHERE project_id=?", (pid,))
    if g is None:
        return {"state": None}
    labels = json.loads(g["labels"]) if g["labels"] else []
    snap = latest()
    out = {"state": g["state"], "labels": labels}
    if g["state"] == DONE and snap:
        out["period"] = _sum_label_rows(snap["rows"], labels)
        m = _sum_monthly(snap["monthly"], labels)
        out["cumulative_sales"] = m["cumulative_sales"] if m else None
        out["source"] = f"週次レポート {snap['period_from']}〜{snap['period_to']}"
    return out
