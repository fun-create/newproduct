"""ラベル×日の売上（金額だけ・ADR-161）。

アシロボの PC の `deploy/windows/sync_newprod_master.ps1`（月曜 8:30）が、sales.csv を新商品マスタの
「含む／除外キーワード」で当て（週次レポートと同じ当て方）、ラベルごと・日ごとの金額を送ってくる。
5月からの年度累計で、発売から1年になる月を日で区切るのに使う（石原さんの依頼書 R01・十文字さんの選択 11/19）。

- **商品名は受け取らない**（ラベル・日付・金額だけ）
- **数えた範囲の終わりは data_to**（明細が実際にあった最新日・ADR-165）。送る側は to を「今日」にするが、
  sales.csv は毎日は更新されないので、to まで数えたことにすると抜けた日が 0 円で「確定」になる。
  送る側が data_to（sales.csv の最新の売上日）を名乗ればそれを、名乗らなければ届いた行の最新日を使う
  （全ラベルが売上 0 の日が最後にあると1日短く出る＝控えめな側に倒す）
- 送られた期間（from〜to）の行を、**送られたラベルだけ**入れ替える（足し合わせない・ADR-166）。
  送られなかったラベル（マスタで「有効」を外した・名前を変えた）の行は残す。年度の途中で外しても年度累計から落とさないため。
  名前を変えたときは古い名前の行も残るので、同じ案件に結んだ新しい名前と同じ日に金額があれば `health()` が「二重のおそれ」と出す
- 届いたら、週次レポートの最新の月別の表と月ごとの合計を比べる。金額の列の取り違え・当て方のずれは、ここで差として出る
  （比べるのは、最新の週次の期間の終わりの月より前の、期間にまるごと入っている月だけ）
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re

from app import store

MAX_LABELS = 300
MAX_ROWS = 60000
TOLERANCE = 1.0          # 円。週次レポートの月別と、この円以内の差なら合っているとみなす


def _day(s) -> str:
    s = str(s or "").strip()
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", s)
    if not m:
        raise ValueError(f"日付が読めません: {s[:20]!r}（YYYY-MM-DD か YYYYMMDD）")
    return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()


def receive(d: dict, source: str = "feed:windows") -> dict:
    if not isinstance(d, dict):
        raise ValueError("送られた形が違います")
    f, t = _day(d.get("from")), _day(d.get("to"))
    if f > t:
        raise ValueError(f"期間が逆です（{f}〜{t}）")
    labels = d.get("labels")
    if not isinstance(labels, dict):
        raise ValueError("labels（{ラベル: {日付: 金額}}）がありません")
    if len(labels) > MAX_LABELS:
        raise ValueError(f"ラベルが多すぎます（{len(labels)}）")
    rows = []
    for lab, days in labels.items():
        lab = str(lab or "").strip()
        if not lab or len(lab) > 120 or re.search(r"[\t\r\n]", lab):
            raise ValueError(f"ラベルの名前が読めません: {lab[:40]!r}")
        if not isinstance(days, dict):
            raise ValueError(f"{lab}: 日付と金額の組がありません")
        for k, v in days.items():
            day = _day(k)
            if not f <= day <= t:
                raise ValueError(f"{lab}: {day} は期間（{f}〜{t}）の外です")
            try:
                a = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{lab} {day}: 金額が数字ではありません") from None
            if not math.isfinite(a):
                raise ValueError(f"{lab} {day}: 金額が読めません")
            if a:
                rows.append((lab, day, a))
    if len(rows) > MAX_ROWS:
        raise ValueError(f"行が多すぎます（{len(rows)}）")
    col = str(d.get("amount_col") or "")[:40] or None
    if d.get("data_to"):
        dto = _day(d.get("data_to"))
        if not f <= dto <= t:
            raise ValueError(f"data_to {dto} は期間（{f}〜{t}）の外です")
        late = max((r[1] for r in rows), default=None)
        if late and late > dto:
            raise ValueError(f"data_to {dto} より後の日（{late}）の行があります")
    else:
        dto = max((r[1] for r in rows), default=None)
    with store.tx() as c:
        sent = sorted({str(k or "").strip() for k in labels})
        for i in range(0, len(sent), 500):
            part = sent[i:i + 500]
            c.execute(f"DELETE FROM newprod_daily WHERE day BETWEEN ? AND ? AND label IN ({','.join('?' * len(part))})",
                      (f, t, *part))
        c.executemany("INSERT INTO newprod_daily (label, day, amount) VALUES (?,?,?) "
                      "ON CONFLICT(label, day) DO UPDATE SET amount=amount+excluded.amount", rows)
    chk = check(f, t)
    total = sum(a for _l, _d, a in rows)
    with store.tx() as c:
        rid = c.execute("INSERT INTO newprod_daily_run (received_at, source, day_from, day_to, data_to, amount_col, labels, days, "
                        "total, check_json, sent_labels) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (store.now_s(), source, f, t, dto, col, len({r[0] for r in rows}), len(rows), total,
                         json.dumps(chk, ensure_ascii=False), json.dumps(sent, ensure_ascii=False))).lastrowid
    store.audit(source, "newprod.daily", f"{f}..{t}", {"run": rid, "labels": len(labels), "rows": len(rows),
                                                        "amount_col": col, "mismatch": len(chk["mismatch"])})
    return {"ok": True, "run": rid, "from": f, "to": t, "data_to": dto, "labels": len({r[0] for r in rows}), "rows": len(rows),
            "mismatch": len(chk["mismatch"]), "compared": chk["pairs"]}


def _month_end(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - dt.timedelta(days=1)).isoformat()


def check(f: str, t: str) -> dict:
    """週次レポートの最新の月別の表と、月ごとの合計を比べる。"""
    from app import newprod
    snap = newprod.latest()
    out = {"snapshot": None, "compared_months": [], "pairs": 0, "mismatch": [], "only_weekly": [], "only_daily": []}
    if snap is None:
        return out
    out["snapshot"] = f"{snap['period_from']}〜{snap['period_to']}"
    stop = snap["period_to"][:7]
    sums = {(r["label"], r["ym"]): r["s"] for r in store.q(
        "SELECT label, substr(day,1,7) AS ym, SUM(amount) AS s FROM newprod_daily WHERE day BETWEEN ? AND ? "
        "GROUP BY label, ym", (f, t))}
    months = set()
    seen_weekly = set()
    for lab, blk in (snap.get("monthly") or {}).items():
        for i, ym in enumerate(blk.get("months") or []):
            if not (ym < stop and f <= ym + "-01" and _month_end(ym) <= t):
                continue
            w = (blk.get("sales") or [None] * (i + 1))[i] if i < len(blk.get("sales") or []) else None
            w = float(w or 0)
            dd = float(sums.get((lab, ym)) or 0)
            months.add(ym)
            seen_weekly.add(lab)
            out["pairs"] += 1
            if abs(w - dd) > TOLERANCE:
                out["mismatch"].append({"label": lab, "month": ym, "weekly": round(w), "daily": round(dd)})
    daily_labels = {k[0] for k in sums}
    out["only_daily"] = sorted(daily_labels - set((snap.get("monthly") or {}).keys()))[:50]
    out["only_weekly"] = sorted(set((snap.get("monthly") or {}).keys()) - daily_labels)[:50]
    out["compared_months"] = sorted(months)
    out["mismatch"] = out["mismatch"][:100]
    return out


def status() -> dict | None:
    r = store.one("SELECT * FROM newprod_daily_run ORDER BY id DESC LIMIT 1")
    if r is None:
        return None
    d = dict(r)
    d["check"] = json.loads(d.pop("check_json") or "{}")
    d.pop("sent_labels", None)
    d["health"] = health()
    return d


def health() -> dict:
    """日ごとの記録の見張り（ADR-166・十文字さんの選択 11/26）。

    - kept: 最後の回で送られなかったのに行が残っているラベル（マスタで「有効」を外した・名前を変えた）
    - double: kept のうち、同じ案件に結んだ別のラベル（最後の回で送られた）と**同じ日に**金額があるもの。
      名前を変えた古い名前の残りとみられ、年度累計で二重に数えるおそれがある
    - no_hits: 案件に結んだ（登録済み）・発売日が数えた範囲の中なのに、発売日から1行も無いラベル。
      新商品マスタのキーワードが当たっていないおそれ（週次レポートの月別が全部0のものと同じ見え方）
    """
    out = {"kept": [], "double": [], "no_hits": []}
    r = store.one("SELECT sent_labels, data_to FROM newprod_daily_run ORDER BY id DESC LIMIT 1")
    if r is None or r["sent_labels"] is None:
        return out
    sent = set(json.loads(r["sent_labels"] or "[]"))
    for x in store.q("SELECT label, MIN(day) AS f, MAX(day) AS t, SUM(amount) AS s FROM newprod_daily GROUP BY label ORDER BY label"):
        if x["label"] not in sent:
            out["kept"].append({"label": x["label"], "from": x["f"], "to": x["t"], "amount": round(x["s"] or 0)})
    projects: dict[str, list[str]] = {}
    launch: dict[str, str] = {}
    for g in store.q("SELECT g.project_id, g.labels, p.launch_date FROM newprod_reg g JOIN project p ON p.id=g.project_id "
                     "WHERE g.state='登録済み'"):
        labs = [str(l) for l in json.loads(g["labels"] or "[]")]
        projects[g["project_id"]] = labs
        for l in labs:
            if g["launch_date"] and (l not in launch or g["launch_date"] < launch[l]):
                launch[l] = g["launch_date"]
    kept = {k["label"] for k in out["kept"]}
    seen = set()
    for pid, labs in projects.items():
        for old in labs:
            if old not in kept:
                continue
            for new in labs:
                if new == old or new not in sent or (old, new) in seen:
                    continue
                n = store.val("SELECT COUNT(*) FROM newprod_daily a JOIN newprod_daily b ON a.day=b.day "
                              "WHERE a.label=? AND b.label=?", (old, new), 0)
                if n:
                    seen.add((old, new))
                    out["double"].append({"old": old, "new": new, "project_id": pid, "days": n})
    have = {x["label"]: x["f"] for x in store.q("SELECT label, MIN(day) AS f FROM newprod_daily GROUP BY label")}
    end = r["data_to"]
    for l, ld in sorted(launch.items()):
        if not end or ld > end:
            continue                                   # まだ発売前（数えた範囲の外）
        if l not in have:
            out["no_hits"].append({"label": l, "launch": ld})
    out["kept"] = out["kept"][:100]
    out["double"] = out["double"][:50]
    out["no_hits"] = out["no_hits"][:100]
    return out


def coverage() -> dict | None:
    """日ごとの記録が覆っている範囲 {f, t}。t は**明細が実際にあった最新日**（data_to・ADR-165）。無ければ None。"""
    # t は**最後の回**の data_to。回ごとに期間をまるごと入れ替えるので、前の回の最新日は残っていない
    r = store.one("SELECT (SELECT MIN(day_from) FROM newprod_daily_run) AS f, "
                  "(SELECT data_to FROM newprod_daily_run ORDER BY id DESC LIMIT 1) AS t")
    if r is None or r["f"] is None or r["t"] is None:
        return None
    return {"f": r["f"], "t": r["t"]}


def sum_between(labels: list[str], start: str, end: str) -> float | None:
    """ラベルの start〜end（両端を含む）の金額の合計。日ごとの記録がその期間を覆っていなければ None（推測しない）。"""
    cov = coverage()
    if not labels or cov is None or cov["f"] is None or not (cov["f"] <= start and end <= cov["t"]):
        return None
    q = ",".join("?" * len(labels))
    return float(store.val(f"SELECT COALESCE(SUM(amount),0) FROM newprod_daily WHERE label IN ({q}) AND day BETWEEN ? AND ?",
                           (*labels, start, end), 0) or 0)
