#!/usr/bin/env python3
"""
競合調査（F-9-6〜8・FR-141〜143）。案件ごとに、比べた競合商品を表で持つ。

- 比べる軸は **価格・仕様・デザイン傾向・レビュー**（F-9-6）。カルテの C.competitor（自由記述）はメモとして残す
- **出典（URL）と確認日は必須**（F-9-8）。値は確認日の時点のもの。調べ直したら確認日も変える
- 分からない値は空欄（未確認）。**0 で埋めない**（N-10）
- **売上の推計**（F-9-7）＝ レビュー件数 ÷ レビュー率 × 価格。保存せず、表示のたびに出す。
  **レビュー率は設定 `competitor_review_rate` で、決めるのは商品開発部。**未設定のあいだは推計を出さない。
  変えるのは設定ページ（admin.set_value・根拠が必須・前後の値を残す。ADR-059）
  （現行のスプレッドシートは `#DIV/0!` のまま止まっていた。仮の率で埋めない）
- レビュー件数は掲載からの**累計**なので、推計も累計（月あたりではない）
"""
from __future__ import annotations

import datetime as _dt

from app import store
from app.cost import _editable, _num, _txt

CHANNELS = ("楽天", "Amazon", "Yahoo!", "自社サイト", "その他")
RATE_KEY = "competitor_review_rate"
RATE_EDITORS = ("devdept", "admin", "president")
FIELDS = ("shop", "item", "channel", "url", "checked_on", "price_yen", "spec", "design",
          "review_count", "review_avg", "review_note", "note")


def review_rate() -> float | None:
    """レビュー率（%）。**未設定は None**（推計を出さない）。"""
    from app import idea
    v = idea.setting(RATE_KEY)
    try:
        x = float(v) if v not in (None, "") else None
    except ValueError:
        return None
    return x if x and 0 < x <= 100 else None


def can_set_rate(user_id: str) -> bool:
    from app import gate
    return bool(set(gate.roles_of(user_id)) & set(RATE_EDITORS))


def _values(f: dict) -> dict:
    shop, item = _txt(f.get("shop"), 120), _txt(f.get("item"), 200)
    if not shop or not item:
        raise ValueError("店・メーカーと商品を入れてください")
    url = _txt(f.get("url"), 500)
    if not url:
        raise ValueError("出典の URL を入れてください（後から確かめられるようにするため）")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("URL は http:// か https:// で始まるものを入れてください")
    d = (f.get("checked_on") or "").strip()
    try:
        day = _dt.date.fromisoformat(d)
    except ValueError:
        raise ValueError("確認日を YYYY-MM-DD で入れてください（値がいつの時点のものかを残すため）") from None
    if day > store.today():
        raise ValueError("確認日に先の日付は入れられません")
    ch = (f.get("channel") or "").strip() or None
    if ch and ch not in CHANNELS:
        raise ValueError("販路は " + "／".join(CHANNELS) + " から選んでください")
    avg = _num(f.get("review_avg"), "平均評価")
    if avg is not None and avg > 5:
        raise ValueError("平均評価は 0〜5 で入れてください")
    return {"shop": shop, "item": item, "channel": ch, "url": url, "checked_on": day.isoformat(),
            "price_yen": _num(f.get("price_yen"), "販売価格"), "spec": _txt(f.get("spec")),
            "design": _txt(f.get("design")),
            "review_count": _num(f.get("review_count"), "レビュー件数", integer=True),
            "review_avg": avg, "review_note": _txt(f.get("review_note")), "note": _txt(f.get("note"))}


def save(pid: str, f: dict, user_id: str) -> dict:
    _editable(pid)
    vals = _values(f)
    now = store.now_s()
    cid = f.get("id")
    with store.tx() as c:
        if cid:
            if c.execute("SELECT 1 FROM competitor_item WHERE id=? AND project_id=?",
                         (int(cid), pid)).fetchone() is None:
                raise LookupError("その競合はこの案件にありません")
            c.execute(f"UPDATE competitor_item SET {','.join(k + '=?' for k in vals)},updated_by=?,updated_at=? "
                      "WHERE id=?", (*vals.values(), user_id, now, int(cid)))
        else:
            cid = c.execute(
                f"INSERT INTO competitor_item (project_id,{','.join(vals)},created_by,created_at,updated_by,updated_at) "
                f"VALUES (?,{','.join('?' * len(vals))},?,?,?,?)",
                (pid, *vals.values(), user_id, now, user_id, now)).lastrowid
    return {"ok": True, "id": int(cid)}


def delete(pid: str, cid, user_id: str) -> dict:
    _editable(pid)
    with store.tx() as c:
        n = c.execute("DELETE FROM competitor_item WHERE id=? AND project_id=?", (int(cid), pid)).rowcount
    if not n:
        raise LookupError("その競合はこの案件にありません")
    return {"ok": True}


def estimate(row: dict, rate: float | None) -> dict:
    """推計（累計）。**材料が欠けたら出さず、何が欠けたかを言う。**"""
    miss = []
    if rate is None:
        miss.append("レビュー率（未設定）")
    if row.get("review_count") is None:
        miss.append("レビュー件数")
    if row.get("price_yen") is None:
        miss.append("販売価格")
    if miss:
        return {"qty": None, "revenue": None, "missing": miss}
    qty = row["review_count"] / (rate / 100)
    return {"qty": round(qty), "revenue": round(qty * row["price_yen"]), "missing": []}


def overview(pid: str, user_id: str) -> dict:
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    rate = review_rate()
    rows = store.rows(store.q("SELECT * FROM competitor_item WHERE project_id=? "
                              "ORDER BY checked_on DESC, id DESC", (pid,)))
    today = store.today()
    for r in rows:
        r["estimate"] = estimate(r, rate)
        r["age_days"] = (today - _dt.date.fromisoformat(r["checked_on"])).days
    prices = sorted(r["price_yen"] for r in rows if r["price_yen"] is not None)
    return {"rows": rows, "channels": list(CHANNELS), "editable": p["source_of_truth"] == "app",
            "review_rate": rate, "can_set_rate": can_set_rate(user_id),
            "price_range": [prices[0], prices[-1]] if prices else None,
            "price_known": len(prices)}
