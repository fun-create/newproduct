#!/usr/bin/env python3
"""
ゲート（§4-6・F-6）。

この部品が答えるのは2つだけ。

1. **いま何が欠けているか**（F-6-2）。`gate_def.required_items` との差分を
   機械的に出す。人が思い出す必要をなくす。
2. **誰が通せるか**（§10-2 ②）。`gate_def.approver_role` × `role_member` で決める。
   **アプリ権限（admin/user）では決めない。**

**`対象外` を状態として持つ**（§5-6）。④ニューモデル追加は G2 → G5 の簡易フロー
なので、G1/G3/G4 が空欄だと**永久に「未達」に見える**。
"""
from __future__ import annotations

import json

from . import store

# 状態。**記号だけにしない。**語を必ず添える（N-11・アクセシビリティ）
STATES = {
    "通過":   {"glyph": "●", "word": "通過"},
    "判定待ち": {"glyph": "◐", "word": "待ち"},
    "差戻し": {"glyph": "◼", "word": "差戻"},
    "保留":   {"glyph": "◼", "word": "保留"},
    "中止":   {"glyph": "◼", "word": "中止"},
    "未":     {"glyph": "○", "word": "未"},
    "対象外": {"glyph": "/", "word": "対象外"},
}

RESULTS = ("通過", "差戻し", "保留", "中止")


def defs() -> list[dict]:
    out = []
    for r in store.q("SELECT * FROM gate_def ORDER BY seq"):
        d = dict(r)
        d["approver_role"] = json.loads(d["approver_role"])
        d["required_items"] = json.loads(d["required_items"])
        d["applies_to_flow_types"] = json.loads(d["applies_to_flow_types"])
        out.append(d)
    return out


def applies(gd: dict, flow_type: str | None) -> bool:
    """**flow が一覧に無ければ `対象外`。**未達ではない。"""
    if not flow_type:
        return True          # 開発タイプ未設定の段階では、まだ外せない
    return flow_type in gd["applies_to_flow_types"]


# ── 欠けているもの（F-6-2）────────────────────────────────
def _sections(project_id: str) -> dict[str, str]:
    return {r["section_key"]: (r["body"] or "")
            for r in store.q("SELECT section_key, body FROM project_section "
                             "WHERE project_id=?", (project_id,))}


def _checks(project_id: str) -> dict[str, dict]:
    return {r["item_key"]: dict(r)
            for r in store.q("SELECT * FROM project_check WHERE project_id=?",
                             (project_id,))}


def _satisfied(check: str, proj: dict, secs: dict, chks: dict,
               project_id: str) -> tuple[bool, str]:
    """満たしているか、と**なぜそう判定したか**を返す。

    判定根拠を返さないと、画面で「なぜ欠けていると言われるのか」が分からない。
    """
    kind, _, rest = check.partition(":")
    if kind == "project":
        v = proj.get(rest)
        ok = v is not None and str(v).strip() != ""
        return ok, f"project.{rest}"
    if kind == "section":
        ok = (secs.get(rest) or "").strip() != ""
        return ok, f"節 {rest}"
    if kind == "lines":
        key, _, n = rest.partition(":")
        lines = [ln for ln in (secs.get(key) or "").splitlines() if ln.strip()]
        need = int(n or 1)
        return len(lines) >= need, f"節 {key} に {len(lines)} 行（{need} 行以上が必要）"
    if kind == "variants":
        n = store.val("SELECT COUNT(*) FROM project_variant WHERE project_id=?",
                      (project_id,), 0)
        return n >= int(rest or 1), f"バリエーション {n} 件"
    return False, f"不明な判定 {check!r}"


def missing(project_id: str, gate: str, gd: dict | None = None,
            proj: dict | None = None) -> list[dict]:
    """そのゲートを通すのに**いま欠けているもの**。"""
    gd = gd or next((g for g in defs() if g["gate"] == gate), None)
    if gd is None:
        return []
    proj = proj or dict(store.one("SELECT * FROM project WHERE id=?", (project_id,)) or {})
    secs = _sections(project_id)
    chks = _checks(project_id)
    out = []
    for it in gd["required_items"]:
        if it["check"] == "manual":
            c = chks.get(it["key"])
            ok = bool(c and c["done"])
            why = (f"{c['updated_by']} が {c['updated_at']} に確認"
                   if ok else "確認の記録がありません")
        else:
            ok, why = _satisfied(it["check"], proj, secs, chks, project_id)
        if not ok:
            out.append({**it, "why": why})
    return out


def state_of(project_id: str, gate: str) -> tuple[str, dict | None]:
    """最後の判定がいまの状態。判定が無ければ `未`。"""
    r = store.one("SELECT * FROM gate_review WHERE project_id=? AND gate=? "
                  "ORDER BY id DESC LIMIT 1", (project_id, gate))
    if r is None:
        return "未", None
    return r["result"], dict(r)


def board(proj: dict) -> list[dict]:
    """1案件のゲート列。**`対象外` と `判定待ち` をここで決める。**

    `判定待ち` は「手前の適用ゲートが全部 通過 していて、自分がまだ 通過 していない
    最初のゲート」。ここを出さないと「誰の番か」が分からない（§3-9）。
    """
    out = []
    waiting_taken = False
    prior_all_passed = True
    for gd in defs():
        if not applies(gd, proj.get("flow_type")):
            out.append({"gate": gd["gate"], "name": gd["name"], "state": "対象外",
                        "approver_role": gd["approver_role"], "review": None,
                        "missing": [], "required_n": len(gd["required_items"])})
            continue
        st, rev = state_of(proj["id"], gd["gate"])
        if st != "通過" and prior_all_passed and not waiting_taken:
            if st == "未":
                st = "判定待ち"
            waiting_taken = True
        if st != "通過":
            prior_all_passed = False
        miss = missing(proj["id"], gd["gate"], gd, proj) if st != "通過" else []
        out.append({"gate": gd["gate"], "name": gd["name"], "state": st,
                    "approver_role": gd["approver_role"], "review": rev,
                    "missing": miss, "required_n": len(gd["required_items"])})
    return out


def next_gate(proj: dict) -> dict | None:
    for g in board(proj):
        if g["state"] in ("判定待ち", "差戻し", "保留"):
            return g
    return None


# ── 誰が通せるか（§10-2 ②）────────────────────────────────
def roles_of(user_id: str) -> list[str]:
    return [r["role_code"] for r in
            store.q("SELECT role_code FROM role_member WHERE user_id=?", (user_id,))]


def can_approve(user_id: str, gate: str) -> bool:
    """**アプリ権限を見ない。**業務ロールだけで決める。

    admin の管理者が G3（社長）を通せてはいけない。
    admin でない社長が G2/G3 を通せなければいけない。
    """
    gd = next((g for g in defs() if g["gate"] == gate), None)
    if gd is None:
        return False
    return bool(set(roles_of(user_id)) & set(gd["approver_role"]))


def approving_role(user_id: str, gate: str) -> str | None:
    gd = next((g for g in defs() if g["gate"] == gate), None)
    if gd is None:
        return None
    for rc in gd["approver_role"]:
        if rc in roles_of(user_id):
            return rc
    return None


def review(project_id: str, gate: str, result: str, user_id: str,
           comment: str = "", reason_code: str = "") -> dict:
    """判定を記録する。**差し戻しも保留も中止も残す**（F-6-3・B-14）。

    **保留・中止は理由コードを必須にする。**自由記述だけにすると、
    あとから「なぜ止まったか」を数えられない。
    """
    if result not in RESULTS:
        raise ValueError(f"知らない判定 {result!r}")
    if not can_approve(user_id, gate):
        gd = next((g for g in defs() if g["gate"] == gate), None)
        want = "／".join(gd["approver_role"]) if gd else "?"
        raise PermissionError(
            f"{gate} の承認資格がありません（必要な業務ロール: {want}）。"
            "アプリ権限（admin）では通せません")
    proj = store.one("SELECT * FROM project WHERE id=?", (project_id,))
    if proj is None:
        raise LookupError("案件がありません")
    proj = dict(proj)
    if proj.get("source_of_truth") != "app":
        raise PermissionError(
            "この案件は Drive 側が正本です（source_of_truth=drive）。"
            "アプリ側では判定できません")
    gd = next(g for g in defs() if g["gate"] == gate)
    if not applies(gd, proj.get("flow_type")):
        raise ValueError(f"{gate} はこの開発タイプでは対象外です")
    if result in ("保留", "中止"):
        table = "hold_reason" if result == "保留" else "abort_reason"
        if not reason_code or store.one(
                f"SELECT 1 FROM {table} WHERE code=?", (reason_code,)) is None:
            raise ValueError(f"{result} には理由の選択が要ります（選択式・F-6-5）")
        reason_code = reason_code
    else:
        reason_code = ""
    miss = missing(project_id, gate, gd, proj)
    # **揃わないと通さない**（F-6 の「通過条件」）。
    # 通せてしまうと、`required_items` は飾りになり、B-3（何が揃えば完成かの
    # 定義が無い）を新アプリの中で再生産する。
    # 差戻し・保留・中止は、欠けていても記録できる（むしろ欠けているから止める）。
    if result == "通過" and miss:
        raise ValueError(
            f"{gate} は通せません。欠けているものが {len(miss)} 件あります: "
            + "／".join(m["label"] for m in miss)
            + "。埋めてから通すか、差戻し・保留を記録してください")
    with store.tx() as c:
        c.execute(
            "INSERT INTO gate_review (project_id,gate,result,approved_by,"
            "approved_role,approved_at,comment,reason_code,missing_items) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, gate, result, user_id, approving_role(user_id, gate),
             store.now_s(), comment or None, reason_code or None,
             json.dumps([m["key"] for m in miss], ensure_ascii=False)))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,"
                  "what,detail) VALUES (?,?,?,?,?)",
                  (project_id, store.now_s(), user_id, f"{gate} {result}",
                   comment or None))
    return {"gate": gate, "result": result,
            "missing_at_review": [m["key"] for m in miss]}


def reasons() -> dict:
    return {
        "hold": store.rows(store.q("SELECT * FROM hold_reason ORDER BY sort")),
        "abort": store.rows(store.q("SELECT * FROM abort_reason ORDER BY sort")),
    }
