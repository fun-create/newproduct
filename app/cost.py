#!/usr/bin/env python3
"""
第3段 原価・調達（ADR-047・2026-10-01 十文字さん決定）。

- **マスタは持たない。**外注先・仕入先・原材料の一覧は seisan が正。
  ここは**案件ごとの検討中のもの**（相見積の候補・試算原価の版）だけ
- 試算原価は**ゼロから積む**（「まずは」。似ている商品から始める形は seisan に口ができてから）
- **為替は持たない。円・税抜で入れる**（外貨で見積もったら、換算レートを備考へ）
- **分からない値は未確定（NULL）。0 で埋めない**（FR-91）。合計にも「未確定を含む」と出す
- 粗利＝販売価格 − 直接費（材料費＋外注費）。**工数費は別列で、粗利から引かない**
  （seisan の管理会計方針・F-7-3・FR-94）
- **前の版は書き換えない。**直すときは新しい版を作る（FR-92）
"""
from __future__ import annotations

import datetime as _dt

from app import store

KINDS = ("資材", "外注")
PARTS_MATERIAL = ("本体", "付属品", "専用資材", "その他")
PARTS = PARTS_MATERIAL + ("外注", "工数")
CONFIDENCE = ("高", "中", "低")

TAX_DEFAULT = 10.0   # 現行の消費税率（F-7-2）。設定 `cost_tax_rate` で変えられる


# ── 小道具 ───────────────────────────────────────────────
def _num(v, label: str, *, positive=False, integer=False):
    """空欄は None（未確定）。数字でなければ断る。**0 を勝手に置かない。**"""
    if v in (None, ""):
        return None
    try:
        x = float(str(v).replace(",", "").strip())
    except ValueError:
        raise ValueError(f"{label}は数字で入れてください") from None
    if x < 0 or (positive and x == 0):
        raise ValueError(f"{label}は{'0より大きい' if positive else '0以上の'}数で入れてください")
    return int(x) if integer else x


def _txt(v, n=500):
    s = str(v or "").strip()
    return s[:n] if s else None


def _project(pid: str) -> dict:
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    return dict(p)


def _editable(pid: str) -> dict:
    p = _project(pid)
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません（R-2）")
    return p


def tax_rate() -> float:
    from app import idea
    v = idea.setting("cost_tax_rate")
    try:
        return float(v) if v not in (None, "") else TAX_DEFAULT
    except ValueError:
        return TAX_DEFAULT


# ── 相見積の候補（FR-98・FR-96）────────────────────────────
CAND_FIELDS = ("kind", "part", "supplier", "shape_size", "url", "unit_price", "min_lot",
               "lead_days", "min_designs", "sample_ok", "stability", "features", "note")


def _cand_values(f: dict) -> dict:
    kind = (f.get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError("種類は 資材／外注 から選んでください")
    sup = _txt(f.get("supplier"), 120)
    if not sup:
        raise ValueError("仕入先・外注先の名前を入れてください")
    part = (f.get("part") or "").strip() or None
    if kind == "資材" and part not in PARTS_MATERIAL:
        raise ValueError("資材の区分は 本体／付属品／専用資材／その他 から選んでください")
    if kind == "外注":
        part = None
    url = _txt(f.get("url"), 500)
    if url and not url.lower().startswith(("http://", "https://")):
        # 画面でリンクにするので、javascript: などを入れさせない
        raise ValueError("URL は http:// か https:// で始まるものを入れてください")
    so = f.get("sample_ok")
    return {
        "kind": kind, "part": part, "supplier": sup,
        "shape_size": _txt(f.get("shape_size"), 120), "url": url,
        "unit_price": _num(f.get("unit_price"), "単価"),
        "min_lot": _num(f.get("min_lot"), "最低ロット", positive=True),
        "lead_days": _num(f.get("lead_days"), "リードタイム（日）"),
        "min_designs": _num(f.get("min_designs"), "最低デザイン数", positive=True, integer=True),
        "sample_ok": None if so in (None, "") else (1 if str(so) in ("1", "true", "on", "可") else 0),
        "stability": _txt(f.get("stability"), 200), "features": _txt(f.get("features")),
        "note": _txt(f.get("note")),
    }


def save_candidate(pid: str, f: dict, user_id: str) -> dict:
    _editable(pid)
    vals = _cand_values(f)
    now = store.now_s()
    cid = f.get("id")
    with store.tx() as c:
        if cid:
            r = c.execute("SELECT id FROM sourcing_candidate WHERE id=? AND project_id=?",
                          (int(cid), pid)).fetchone()
            if r is None:
                raise LookupError("その候補はこの案件にありません")
            c.execute(f"UPDATE sourcing_candidate SET {','.join(k + '=?' for k in vals)},"
                      "updated_by=?,updated_at=? WHERE id=?",
                      (*vals.values(), user_id, now, int(cid)))
        else:
            cur = c.execute(
                f"INSERT INTO sourcing_candidate (project_id,{','.join(vals)},created_by,"
                f"created_at,updated_by,updated_at) VALUES (?,{','.join('?' * len(vals))},?,?,?,?)",
                (pid, *vals.values(), user_id, now, user_id, now))
            cid = cur.lastrowid
    return {"ok": True, "id": int(cid)}


def adopt(pid: str, cid, adopted: bool, reason: str, user_id: str) -> dict:
    """採用／不採用。**不採用には理由が要る**（F-8-4。後で同じ候補を探し直さないため）。"""
    _editable(pid)
    reason = _txt(reason)
    if not adopted and not reason:
        raise ValueError("採用しなかった理由を入れてください（後で同じ候補を探し直さないため）")
    with store.tx() as c:
        n = c.execute("UPDATE sourcing_candidate SET adopted=?,not_adopted_reason=?,updated_by=?,"
                      "updated_at=? WHERE id=? AND project_id=?",
                      (1 if adopted else 0, None if adopted else reason, user_id, store.now_s(),
                       int(cid), pid)).rowcount
    if not n:
        raise LookupError("その候補はこの案件にありません")
    return {"ok": True}


def candidates(pid: str) -> list[dict]:
    return store.rows(store.q("SELECT * FROM sourcing_candidate WHERE project_id=? "
                              "ORDER BY kind, part, adopted DESC, id", (pid,)))


# ── 発注の締切（FR-101）────────────────────────────────────
def deadline(p: dict, cands: list[dict], today: _dt.date | None = None) -> dict:
    """発売日 − 採用した候補の最長リードタイム ＝ 本番発注の締切。

    **リードタイムが分からない採用候補が1つでもあれば「未確定」。**
    余裕日数は持たない（決まっていない値を発明しない）。"""
    today = today or store.today()
    adopted = [c for c in cands if c["adopted"]]
    if not p.get("launch_date"):
        return {"state": "未確定", "why": "発売予定日がありません"}
    if not adopted:
        return {"state": "未確定", "why": "採用した候補がまだありません"}
    unknown = [c["supplier"] for c in adopted if c["lead_days"] is None]
    if unknown:
        return {"state": "未確定", "why": "リードタイムが未確定の採用候補: " + "、".join(unknown)}
    worst = max(adopted, key=lambda c: c["lead_days"])
    launch = _dt.date.fromisoformat(p["launch_date"])
    due = launch - _dt.timedelta(days=int(round(worst["lead_days"])))
    left = (due - today).days
    return {"state": "間に合う" if left >= 0 else "間に合わない",
            "due": due.isoformat(), "days_left": left, "launch_date": p["launch_date"],
            "lead_days": worst["lead_days"], "by": worst["supplier"],
            "why": None if left >= 0 else
            f"発注の締切 {due.isoformat()} を {-left} 日過ぎています（{worst['supplier']} のリードタイム {worst['lead_days']:g}日）"}


# ── 試算原価（FR-88・89・90・91・92）────────────────────────────
def _latest(pid: str):
    return store.one("SELECT * FROM cost_version WHERE project_id=? ORDER BY version DESC LIMIT 1",
                     (pid,))


def new_version(pid: str, f: dict, user_id: str) -> dict:
    """新しい版。**前の版の行をそのまま写す**（前の版は残る・書き換えない）。"""
    _editable(pid)
    conf = (f.get("confidence") or "").strip() or None
    if conf is not None and conf not in CONFIDENCE:
        raise ValueError("確度は 高／中／低 から選んでください")
    prev = _latest(pid)
    ver = (prev["version"] + 1) if prev else 1
    now = store.now_s()
    price = _num(f.get("price_ex_tax"), "販売価格") if "price_ex_tax" in f else (
        prev["price_ex_tax"] if prev else None)
    with store.tx() as c:
        cur = c.execute(
            "INSERT INTO cost_version (project_id,version,confidence,price_ex_tax,tax_rate,note,"
            "created_by,created_at,updated_by,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, ver, conf or (prev["confidence"] if prev else None), price, tax_rate(),
             _txt(f.get("note")), user_id, now, user_id, now))
        vid = cur.lastrowid
        if prev:
            c.execute("INSERT INTO cost_line (version_id,seq,part,name,qty,unit,unit_price,"
                      "candidate_id,note) SELECT ?,seq,part,name,qty,unit,unit_price,"
                      "candidate_id,note FROM cost_line WHERE version_id=?", (vid, prev["id"]))
    return {"ok": True, "version": ver, "id": vid}


def _guard_latest(pid: str, vid) -> dict:
    _editable(pid)
    v = store.one("SELECT * FROM cost_version WHERE id=? AND project_id=?", (int(vid), pid))
    if v is None:
        raise LookupError("その版はこの案件にありません")
    lt = _latest(pid)
    if lt["id"] != v["id"]:
        raise ValueError(f"v{v['version']} は前の版です。直すときは新しい版を作ってください（前の版は残します）")
    return dict(v)


def update_version(pid: str, vid, f: dict, user_id: str) -> dict:
    _guard_latest(pid, vid)
    conf = (f.get("confidence") or "").strip() or None
    if conf is not None and conf not in CONFIDENCE:
        raise ValueError("確度は 高／中／低 から選んでください")
    price = _num(f.get("price_ex_tax"), "販売価格")
    with store.tx() as c:
        c.execute("UPDATE cost_version SET confidence=?,price_ex_tax=?,note=?,updated_by=?,"
                  "updated_at=? WHERE id=?",
                  (conf, price, _txt(f.get("note")), user_id, store.now_s(), int(vid)))
    return {"ok": True}


def save_line(pid: str, vid, f: dict, user_id: str) -> dict:
    _guard_latest(pid, vid)
    part = (f.get("part") or "").strip()
    if part not in PARTS:
        raise ValueError("区分は " + "／".join(PARTS) + " から選んでください")
    name = _txt(f.get("name"), 200)
    if not name:
        raise ValueError("何の費用かを入れてください")
    cand = f.get("candidate_id") or None
    vals = {"part": part, "name": name, "qty": _num(f.get("qty"), "使用量"),
            "unit": _txt(f.get("unit"), 20), "unit_price": _num(f.get("unit_price"), "単価"),
            "candidate_id": int(cand) if cand else None, "note": _txt(f.get("note"))}
    if cand:
        r = store.one("SELECT unit_price FROM sourcing_candidate WHERE id=? AND project_id=?",
                      (int(cand), pid))
        if r is None:
            raise LookupError("その候補はこの案件にありません")
        if f.get("unit_price") in (None, ""):
            vals["unit_price"] = r["unit_price"]       # **写す**（後で候補を直しても変わらない）
    lid = f.get("id")
    with store.tx() as c:
        if lid:
            n = c.execute(f"UPDATE cost_line SET {','.join(k + '=?' for k in vals)} "
                          "WHERE id=? AND version_id=?", (*vals.values(), int(lid), int(vid))).rowcount
            if not n:
                raise LookupError("その行はこの版にありません")
        else:
            seq = c.execute("SELECT COALESCE(MAX(seq),0)+1 FROM cost_line WHERE version_id=?",
                            (int(vid),)).fetchone()[0]
            lid = c.execute(f"INSERT INTO cost_line (version_id,seq,{','.join(vals)}) "
                            f"VALUES (?,?,{','.join('?' * len(vals))})",
                            (int(vid), seq, *vals.values())).lastrowid
        c.execute("UPDATE cost_version SET updated_by=?,updated_at=? WHERE id=?",
                  (user_id, store.now_s(), int(vid)))
    return {"ok": True, "id": int(lid)}


def delete_line(pid: str, vid, lid, user_id: str) -> dict:
    _guard_latest(pid, vid)
    with store.tx() as c:
        n = c.execute("DELETE FROM cost_line WHERE id=? AND version_id=?",
                      (int(lid), int(vid))).rowcount
    if not n:
        raise LookupError("その行はこの版にありません")
    return {"ok": True}


def totals(v: dict, lines: list[dict]) -> dict:
    """材料費・外注費・工数費と粗利。**未確定の行は合計に入れず、入っていないと言う。**"""
    def amt(l):
        return None if l["qty"] is None or l["unit_price"] is None else l["qty"] * l["unit_price"]
    out = {}
    for key, parts in (("material", PARTS_MATERIAL), ("outsource", ("外注",)), ("labor", ("工数",))):
        ls = [l for l in lines if l["part"] in parts]
        known = [amt(l) for l in ls if amt(l) is not None]
        out[key] = {"yen": round(sum(known), 2) if known else (0.0 if not ls else None),
                    "lines": len(ls), "unknown": len(ls) - len(known)}
    direct_unknown = out["material"]["unknown"] + out["outsource"]["unknown"]
    known_direct = [out[k]["yen"] for k in ("material", "outsource") if out[k]["yen"] is not None]
    direct = round(sum(known_direct), 2) if known_direct else None
    price = v.get("price_ex_tax")
    rate = v.get("tax_rate") if v.get("tax_rate") is not None else TAX_DEFAULT
    gross = (round(price - direct, 2) if price is not None and direct is not None else None)
    no_lines = not [l for l in lines if l["part"] != "工数"]
    return {**out, "direct": {"yen": direct, "unknown": direct_unknown, "empty": no_lines},
            "price_ex_tax": price, "price_in_tax": round(price * (1 + rate / 100)) if price is not None else None,
            "tax_rate": rate,
            "gross": {"yen": gross, "rate": round(gross / price * 100, 1) if gross is not None and price else None,
                      # 直接費に未確定の行があると、粗利は実際より大きく出る
                      "overstated": direct_unknown > 0},
            }


def overview(pid: str) -> dict:
    p = _project(pid)
    cands = candidates(pid)
    vs = store.rows(store.q("SELECT * FROM cost_version WHERE project_id=? ORDER BY version DESC",
                            (pid,)))
    versions = []
    for v in vs:
        lines = store.rows(store.q("SELECT * FROM cost_line WHERE version_id=? ORDER BY seq, id",
                                   (v["id"],)))
        versions.append({**v, "lines": lines, "totals": totals(v, lines),
                         "latest": v is vs[0]})
    return {"kinds": KINDS, "parts_material": PARTS_MATERIAL, "parts": PARTS,
            "confidence": CONFIDENCE, "tax_rate": tax_rate(),
            "editable": p["source_of_truth"] == "app",
            "candidates": cands, "deadline": deadline(p, cands), "versions": versions}


# ── ゲート（G3「試算原価 v1」）──────────────────────────────
def gate_state(pid: str) -> tuple[bool, str]:
    v = _latest(pid)
    if v is None:
        return False, "試算原価の版がありません"
    lines = store.rows(store.q("SELECT * FROM cost_line WHERE version_id=?", (v["id"],)))
    t = totals(dict(v), lines)
    if t["direct"]["empty"]:
        return False, f"v{v['version']} に材料・外注の行がありません"
    why = f"v{v['version']}（直接費 {t['direct']['yen'] if t['direct']['yen'] is not None else '未確定'}円"
    if t["direct"]["unknown"]:
        why += f"・未確定 {t['direct']['unknown']} 行を含む"
    return True, why + "）"
