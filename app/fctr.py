#!/usr/bin/env python3
"""
FCTR（Auto GROWTH の週次トレンド）の受け取り（F-9-1〜3・FR-135〜137）。

- 上流が出すのは**市場性（30点）だけ**。自社側70点（商品相性・製造運用・発売速度・利益性）は
  本アプリの担当（FR-138・未着手）。**上流が出さない「月商予測」は作らない**
- **時限スコア。恒久スコアと合算しない。**最後に観測された週から **8週で 0 まで直線で減らす**（FR-137）
- TOP3 は上流に無い。**客層ごとに減衰後の点の上位3つ**をここで決める
- 1操作でアイデアへ起票（起票経路＝FCTR・FR-136）。**同じテーマを二重に起票しない**
- 取り込みは**画面を開いたときに新しい週があれば**行う（上流は毎週月曜 02:10）
"""
from __future__ import annotations

import datetime as _dt
import json
import os

from app import store

PATH = "/opt/autogrowth/data/export/fctr_weekly.json"
CONTRACT = 1
DECAY_WEEKS = 8
TOP_N = 3
THEME_KIND = "FCTRテーマ"
SOURCE = "AutoGrowth fctr_weekly"


class Unavailable(RuntimeError):
    pass


def path() -> str:
    return os.environ.get("NEWPRODUCT_FCTR") or PATH


def _week_index(week_id: str) -> int:
    """'2026-W40' → 通し週番号（年またぎの差を正しく取るため）。"""
    y, w = week_id.split("-W")
    return _dt.date.fromisocalendar(int(y), int(w), 1).toordinal() // 7


def current_week() -> str:
    y, w, _d = store.today().isocalendar()
    return f"{y}-W{w:02d}"


def ingest() -> dict:
    """新しい週があれば取り込む。**同じ週を二度入れない。**"""
    p = path()
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise Unavailable(f"FCTR の週次結果（{p}）を読めません: {e}") from None
    if d.get("contract_version") != CONTRACT:
        raise Unavailable(f"FCTR の版が {d.get('contract_version')!r} です（このアプリは {CONTRACT} 前提。読まずに止めました）")
    week = d.get("week_id") or ""
    if store.one("SELECT 1 FROM theme_signal WHERE week_id=? AND source LIKE ?", (week, SOURCE + "%")):
        return {"week_id": week, "added": 0, "already": True, "meta": _meta(d)}
    n = 0
    with store.tx() as c:
        for seg in d.get("segments") or []:
            for t in seg.get("themes") or []:
                key = (t.get("theme_key") or t.get("theme") or "").strip()
                if not key:
                    continue
                tid = "fctr:" + key
                c.execute("INSERT OR IGNORE INTO theme (id,label,kind,sort,note) VALUES (?,?,?,?,?)",
                          (tid, t.get("theme") or key, THEME_KIND, 0, "Auto GROWTH の FCTR から"))
                detail = {k: t.get(k) for k in ("components", "evidence", "market_score_max_possible",
                                                 "weeks_present", "first_seen_week", "novelty_penalty",
                                                 "novelty_penalty_basis", "sources_used")}
                n += c.execute(
                    "INSERT OR IGNORE INTO theme_signal (theme_id,week_id,market_score,weeks_present,novelty,"
                    "cycle,source,fetched_at,segment,segment_name,score_max,detail_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, week, t.get("market_score"), t.get("consecutive_weeks"), t.get("novelty"),
                     str(t.get("cycle")) if t.get("cycle") is not None else None,
                     f"{SOURCE}/{seg.get('segment_id')}", d.get("generated_at") or store.now_s(),
                     seg.get("segment_id"), f"{seg.get('site_name')} {seg.get('segment_name')}",
                     t.get("market_score_max"), json.dumps(detail, ensure_ascii=False))).rowcount
    return {"week_id": week, "added": n, "already": False, "meta": _meta(d)}


def _meta(d: dict) -> dict:
    return {"generated_at": d.get("generated_at"), "sources_ok": d.get("sources_ok") or [],
            "sources_disabled": [s.get("source") for s in d.get("sources_disabled") or []],
            "sources_unavailable": d.get("sources_unavailable") or [],
            "notes": d.get("notes") or [], "not_produced": (d.get("scope") or {}).get("not_produced") or []}


def decay(score, obs_week: str, now_week: str):
    if score is None:
        return None
    age = _week_index(now_week) - _week_index(obs_week)
    f = max(0.0, 1.0 - max(0, age) / DECAY_WEEKS)
    return round(score * f, 2)


def board() -> dict:
    """客層ごとに、最後に観測された週の素点を減衰させて並べる。0 になったテーマは出さない。"""
    try:
        info = ingest()
        why = None
    except Unavailable as e:
        info, why = {"meta": {}}, str(e)
    now = current_week()
    rows = store.q("SELECT s.*, t.label FROM theme_signal s JOIN theme t ON t.id=s.theme_id "
                   "WHERE s.source LIKE ? ORDER BY s.week_id", (SOURCE + "%",))
    latest = {}
    for r in rows:
        latest[(r["segment"], r["theme_id"])] = dict(r)        # 週の昇順なので最後が最新
    ideas = {r["theme_id"]: r["id"] for r in store.q(
        "SELECT id, theme_id FROM idea WHERE origin='fctr' AND theme_id LIKE 'fctr:%'")}
    segs = {}
    for (seg, tid), r in latest.items():
        dv = decay(r["market_score"], r["week_id"], now)
        if not dv:
            continue
        det = json.loads(r["detail_json"] or "{}")
        ev = (det.get("evidence") or [{}])[0]
        segs.setdefault(seg, {"segment": seg, "name": r["segment_name"], "themes": []})["themes"].append({
            "theme_id": tid, "label": r["label"], "week_id": r["week_id"],
            "raw": r["market_score"], "score_max": r["score_max"], "decayed": dv,
            "age_weeks": _week_index(now) - _week_index(r["week_id"]),
            "consecutive": r["weeks_present"], "novelty": r["novelty"],
            "evidence": ev.get("signal"), "components": det.get("components") or {},
            "idea_id": ideas.get(tid)})
    selfs = _self_scores()
    for sg in segs.values():
        for t in sg["themes"]:
            ss = selfs.get(t["theme_id"])
            t["self"] = ss
            t["total100"] = round(t["decayed"] + ss["total"], 2) if ss and ss["total"] is not None else None
    out = []
    for s in sorted(segs.values(), key=lambda x: x["segment"] or ""):
        s["themes"].sort(key=lambda x: (-x["decayed"], x["label"]))
        for i, t in enumerate(s["themes"]):
            t["top"] = i < TOP_N
        out.append(s)
    weeks = [r["week_id"] for r in store.q(
        "SELECT DISTINCT week_id FROM theme_signal WHERE source LIKE ? ORDER BY week_id DESC", (SOURCE + "%",))]
    return {"now_week": now, "latest_week": weeks[0] if weeks else None, "weeks": weeks,
            "decay_weeks": DECAY_WEEKS, "top_n": TOP_N, "segments": out, "why": why,
            "self_axes": [{"key": k, "label": l, "max": m} for k, l, m in SELF_AXES],
            "meta": info.get("meta") or {}}


def to_idea(theme_id: str, segment: str, user_id: str) -> dict:
    """1操作でアイデアへ（起票経路＝FCTR）。**同じテーマは二重に起票しない。**"""
    from app import idea
    t = store.one("SELECT * FROM theme WHERE id=? AND kind=?", (theme_id, THEME_KIND))
    if t is None:
        raise LookupError("その FCTR テーマはありません")
    ex = store.one("SELECT id FROM idea WHERE origin='fctr' AND theme_id=?", (theme_id,))
    if ex:
        raise ValueError(f"このテーマは既にアイデアにしています（{ex['id']}）")
    s = store.one("SELECT * FROM theme_signal WHERE theme_id=? AND segment=? ORDER BY week_id DESC LIMIT 1",
                  (theme_id, segment))
    summary = (f"FCTR {s['week_id']}・{s['segment_name']}・市場性 {s['market_score']}/{s['score_max']}"
               f"（{s['weeks_present']}週連続）" if s else "FCTR から")
    return idea.create(user_id, title=t["label"], origin="fctr", theme_id=theme_id, summary=summary)


# ── 自社側70点（FR-138）。**基準は商品開発部が決める**（ここでは欄と上限だけ）──────────
SELF_AXES = (("fit", "商品相性", 25), ("ops", "製造運用", 20), ("speed", "発売速度", 15), ("profit", "利益性", 10))
SELF_EDITORS = ("devdept", "admin", "president")


def can_score(user_id: str) -> bool:
    from app import gate
    return bool(set(gate.roles_of(user_id)) & set(SELF_EDITORS))


def save_self(theme_id: str, f: dict, user_id: str) -> dict:
    if not can_score(user_id):
        raise PermissionError("自社側の点を付けられるのは、商品開発部・管理者・社長の業務ロールの人です")
    if store.one("SELECT 1 FROM theme WHERE id=? AND kind=?", (theme_id, THEME_KIND)) is None:
        raise LookupError("その FCTR テーマはありません")
    vals = {}
    for key, label, mx in SELF_AXES:
        v = str(f.get(key) or "").strip()
        if v == "":
            vals[key] = None                       # 未採点（0 ではない）
            continue
        try:
            x = float(v)
        except ValueError:
            raise ValueError(f"{label}は数字で入れてください") from None
        if not 0 <= x <= mx:
            raise ValueError(f"{label}は 0〜{mx} で入れてください")
        vals[key] = x
    with store.tx() as c:
        c.execute("INSERT INTO fctr_self_score (theme_id,fit,ops,speed,profit,note,updated_by,updated_at) "
                  "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(theme_id) DO UPDATE SET fit=excluded.fit,"
                  "ops=excluded.ops,speed=excluded.speed,profit=excluded.profit,note=excluded.note,"
                  "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                  (theme_id, vals["fit"], vals["ops"], vals["speed"], vals["profit"],
                   (f.get("note") or "").strip()[:300] or None, user_id, store.now_s()))
    return {"ok": True}


def _self_scores() -> dict:
    out = {}
    for r in store.q("SELECT * FROM fctr_self_score"):
        d = dict(r)
        parts = [d[k] for k, _l, _m in SELF_AXES]
        d["total"] = sum(parts) if all(p is not None for p in parts) else None   # そろうまで合計を出さない
        out[d["theme_id"]] = d
    return out
