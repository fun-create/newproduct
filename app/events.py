#!/usr/bin/env python3
"""
年間イベント・ライフイベントの登録と修正（設定ページ・2026-10-06 十文字さんの選択・ADR-060）。

- 正本はこのアプリ（機会カレンダーの暦）。他のアプリから写さない
- **ID は変えない。**名前を直しても ID はそのまま。年間プランの枠は名前の文字列で機会を持っているので、
  名前を直したら**策定中の版の枠だけ**名前を付け替える（承認済み・失効の版は、その時点の記録として残す）
- **消さない。**「使わない」にすると機会カレンダーから外れる。既存の枠の記録は残る
- 同じ種類で同じ名前（表記ゆれを除いて）は保存しない。似た名前があれば、確かめてから保存する
- 日付は**原文のまま**（「11月15日前後の土日」など）。日付の形に直さない（ADR-032）
- ライフイベントの総合点は**サーバーで計算する**（購買意欲×2 ＋ 写真親和性 ＋ 発生頻度×2・満点40）。
  各軸の上限は 購買意欲10・写真親和性10・発生頻度5（満点40と、取り込んだ19件の実測から）。
  3つそろわなければ総合点は未設定（0 にしない）。優先順位は人が入れる（勝手に付けない）
- 画面で直した行には印（edited_at）を付け、取り込みツールはその行を上書きしない
"""
from __future__ import annotations

import hashlib

from app import store

KINDS = ("年間イベント", "ライフイベント")
EDITORS = ("devdept", "admin", "president")
AXES = (("gift_intent", "記念品購買意欲", 10), ("photo_fit", "写真親和性", 10), ("frequency", "発生頻度", 5))


def can_edit(user_id: str) -> bool:
    from app import gate
    return bool(set(gate.roles_of(user_id)) & set(EDITORS))


def _new_id(kind: str, name: str) -> str:
    """取り込みツールと同じ作り方（名前から決める）。"""
    h = hashlib.sha256(f"{kind}\t{name}".encode()).hexdigest()[:8]
    return ("ev" if kind == "年間イベント" else "lf") + h


def listing() -> dict:
    rows = store.rows(store.q(
        "SELECT t.*, s.gift_intent, s.photo_fit, s.frequency, s.total, s.priority FROM theme t "
        "LEFT JOIN theme_score s ON s.theme_id=t.id WHERE t.kind IN ('年間イベント','ライフイベント') "
        "ORDER BY t.kind, t.active DESC, (t.kind='年間イベント') DESC, t.month, (s.total IS NULL), s.total DESC, t.sort"))
    slots = {}
    for r in store.q("SELECT occasion, COUNT(*) n FROM plan_slot WHERE occasion IS NOT NULL GROUP BY occasion"):
        slots[r["occasion"]] = r["n"]
    for r in rows:
        r["slots"] = slots.get(r["label"], 0)
        r["origin"] = ("画面で " + (r["edited_by"] or "") + "・" + (r["edited_at"] or "")) if r["edited_at"] \
            else (f"取り込み 行{r['source_row']}" if r["source_row"] else "—")
    count = {k: sum(1 for r in rows if r["kind"] == k and r["active"]) for k in KINDS}
    last = store.one("SELECT edited_by, edited_at FROM theme WHERE edited_at IS NOT NULL ORDER BY edited_at DESC LIMIT 1")
    return {"rows": rows, "count": count, "last": dict(last) if last else None,
            "axes": [{"key": k, "label": l, "max": m} for k, l, m in AXES]}


def _int(v, label, lo, hi):
    s = str(v if v is not None else "").strip()
    if s == "":
        return None
    if not s.isdigit() or not lo <= int(s) <= hi:
        raise ValueError(f"{label}は {lo}〜{hi} の整数で入れてください")
    return int(s)


def similar(kind: str, name: str, exclude: str = "") -> list[dict]:
    from app import idea
    n = idea.normalize(name)
    out = []
    for r in store.q("SELECT id, label FROM theme WHERE kind=?", (kind,)):
        if r["id"] == exclude:
            continue
        m = idea.normalize(r["label"])
        if m == n:
            out.append({"id": r["id"], "label": r["label"], "same": True})
        else:
            a, b = idea.bigrams(n), idea.bigrams(m)
            if a and b and 2 * len(a & b) / (len(a) + len(b)) >= 0.6:
                out.append({"id": r["id"], "label": r["label"], "same": False})
    return out


def save(f: dict, user_id: str, ip: str = "") -> dict:
    if not can_edit(user_id):
        raise PermissionError("イベントを登録・修正できるのは、商品開発部・管理者・社長の業務ロールの人です")
    eid = (f.get("id") or "").strip()
    cur = store.one("SELECT * FROM theme WHERE id=? AND kind IN ('年間イベント','ライフイベント')", (eid,)) if eid else None
    if eid and cur is None:
        raise LookupError("そのイベントはありません")
    kind = cur["kind"] if cur else (f.get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError("種類は 年間イベント／ライフイベント から選んでください")
    name = (f.get("label") or "").strip()[:60]
    if not name:
        raise ValueError("名前を入れてください")
    sim = similar(kind, name, exclude=eid)
    if any(x["same"] for x in sim):
        raise ValueError(f"同じ名前の{kind}が既にあります（{next(x['label'] for x in sim if x['same'])}）")
    if sim and str(f.get("confirm") or "") != "1":
        return {"ok": False, "similar": sim}            # 人が確かめてから、もう一度送る
    vals = {"label": name, "note": (f.get("note") or "").strip()[:300] or None}
    if kind == "年間イベント":
        vals["month"] = _int(f.get("month"), "月", 1, 12)
        if vals["month"] is None:
            raise ValueError("年間イベントは月を入れてください")
        vals["day"] = (f.get("day") or "").strip()[:40] or None
        vals["sellable"] = 1 if str(f.get("sellable") or "") in ("1", "true", "on") else 0
    else:
        vals["product_gap"] = 1 if str(f.get("product_gap") or "") in ("1", "true", "on") else 0
        vals["product_ideas"] = (f.get("product_ideas") or "").strip()[:300] or None
        vals["sellable"] = 1 if str(f.get("sellable") or "") in ("1", "true", "on") else 0
    sc = {k: _int(f.get(k), l, 0, m) for k, l, m in AXES} if kind == "ライフイベント" else None
    prio = _int(f.get("priority"), "優先順位", 1, 999) if kind == "ライフイベント" else None
    now = store.now_s()
    with store.tx() as c:
        if cur is None:
            eid = _new_id(kind, name)
            if c.execute("SELECT 1 FROM theme WHERE id=?", (eid,)).fetchone():
                raise ValueError("同じ名前のイベントが、使わないものの中にあります。そちらを使うに戻してください")
            sort = (c.execute("SELECT COALESCE(MAX(sort),0)+1 FROM theme WHERE kind=?", (kind,)).fetchone()[0])
            c.execute(f"INSERT INTO theme (id,kind,sort,active,edited_by,edited_at,{','.join(vals)}) "
                      f"VALUES (?,?,?,1,?,?,{','.join('?' * len(vals))})", (eid, kind, sort, user_id, now, *vals.values()))
        else:
            c.execute(f"UPDATE theme SET {','.join(k + '=?' for k in vals)},edited_by=?,edited_at=? WHERE id=?",
                      (*vals.values(), user_id, now, eid))
            if cur["label"] != name:
                # 年間プランの枠は名前の文字列で機会を持つ。**策定中の版だけ**付け替える
                c.execute("UPDATE plan_slot SET occasion=? WHERE occasion=? AND version_id IN "
                          "(SELECT id FROM plan_version WHERE state='策定中')", (name, cur["label"]))
        if sc is not None:
            total = (sc["gift_intent"] * 2 + sc["photo_fit"] + sc["frequency"] * 2) \
                if None not in sc.values() else None
            c.execute("INSERT INTO theme_score (theme_id,gift_intent,photo_fit,frequency,total,priority,source,captured_at) "
                      "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(theme_id) DO UPDATE SET gift_intent=excluded.gift_intent,"
                      "photo_fit=excluded.photo_fit,frequency=excluded.frequency,total=excluded.total,"
                      "priority=excluded.priority,source=excluded.source,captured_at=excluded.captured_at",
                      (eid, sc["gift_intent"], sc["photo_fit"], sc["frequency"], total, prio,
                       f"設定ページ（{user_id}）", now))
    store.audit(user_id, "event.save", eid, {"label": name, "kind": kind, "before": cur["label"] if cur else None,
                                             "after": name}, ip)
    return {"ok": True, "id": eid}


def set_active(eid: str, on: bool, user_id: str, ip: str = "") -> dict:
    if not can_edit(user_id):
        raise PermissionError("イベントを登録・修正できるのは、商品開発部・管理者・社長の業務ロールの人です")
    cur = store.one("SELECT * FROM theme WHERE id=? AND kind IN ('年間イベント','ライフイベント')", (eid,))
    if cur is None:
        raise LookupError("そのイベントはありません")
    with store.tx() as c:
        c.execute("UPDATE theme SET active=?, edited_by=?, edited_at=? WHERE id=?",
                  (1 if on else 0, user_id, store.now_s(), eid))
    store.audit(user_id, "event.active" if on else "event.inactive", eid, {"label": cur["label"]}, ip)
    return {"ok": True}
