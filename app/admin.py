#!/usr/bin/env python3
"""
設定ページの「アプリ全体に効くもの」（2026-10-06 十文字さんの指示と選択・ADR-059）。

- **アプリ全体の設定値**を画面から変える。誰が変えられるかは十文字さんの選択
  「中身は業務ロール、接続先はアプリ権限 admin」。発売目標本数は社長だけ（FR-131「確定は人（社長）」）
- **根拠が必須。**変えた記録に**前の値・新しい値・根拠・誰・いつ**を残す（監査ログ。設定ページで全員に見せる）
- **空欄は「未設定に戻す」**。0 にしない（N-10）
- 正本が他にあるもの・意味の無いものは変えさせない（長期連休の月＝Calendar、年度の開始月＝使っていない、
  AI採点＝まだ呼ぶ実装が無い、AI予算の確認先＝書き換えると別のものにつながる）
- **業務ロールの付け外し**は、アプリ権限 admin（十文字さんの選択）。社長の業務ロールを持つ人を0人にはしない
  （年間プランの承認・G2/G3 を誰も通せなくなるため）
"""
from __future__ import annotations

import json
import re

from app import store

BIZ_CORE = ("admin", "president")
BIZ_DEV = ("devdept", "admin", "president")
APP_ADMIN = "app_admin"

# key → (変えられる人, 検査, 区分)。検査は文字列を受けて正規化した文字列を返す（不正なら ValueError）
def _num(lo=None, hi=None, integer=False, positive=False):
    def f(v, label):
        try:
            x = float(v.replace(",", ""))
        except ValueError:
            raise ValueError(f"{label}は数字で入れてください") from None
        if integer and x != int(x):
            raise ValueError(f"{label}は整数で入れてください")
        if positive and x <= 0:
            raise ValueError(f"{label}は0より大きい数で入れてください")
        if lo is not None and x < lo or hi is not None and x > hi:
            raise ValueError(f"{label}は {lo}〜{hi} で入れてください")
        return str(int(x)) if integer or x == int(x) else str(x)
    return f


def _ratio(v, label):
    if not re.fullmatch(r"\s*\d+\s*[:：]\s*\d+\s*", v):
        raise ValueError(f"{label}は「3:1」の形で入れてください")
    a, b = re.split(r"[:：]", v)
    return f"{int(a)}:{int(b)}"


def _room(v, label):
    if not re.fullmatch(r"\d{3,15}", v.strip()):
        raise ValueError(f"{label}は数字だけで入れてください")
    return v.strip()


def _url(v, label):
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?(/[^\s]*)?", v.strip()):
        raise ValueError(f"{label}は https:// で始まる URL で入れてください")
    return v.strip().rstrip("/")


EDITABLE = {
    "cost_tax_rate":                (BIZ_CORE, _num(0, 100), "原価・価格"),
    "competitor_review_rate":       (BIZ_DEV, _num(0, 100, positive=True), "競合の推計"),
    "monthly_launch_target":        (("president",), _num(1, 100, integer=True), "在庫と目標"),
    "concept_stock_floor":          (BIZ_CORE, _num(0, 60, positive=True), "在庫と目標"),
    "plan.effort_min":              (BIZ_CORE, _num(0, 1000), "年間プランの挿入ルール"),
    "plan.effort_max":              (BIZ_CORE, _num(0, 1000), "年間プランの挿入ルール"),
    "plan.monthly_launch_slots":    (BIZ_CORE, _num(1, 50, integer=True), "年間プランの挿入ルール"),
    "plan.ratio_original_to_uchiwa": (BIZ_CORE, _ratio, "年間プランの挿入ルール"),
    "plan.ratio_tolerance_slots":   (BIZ_CORE, _num(0, 50), "年間プランの挿入ルール"),
    "plan.task_setup_lead_months":  (BIZ_DEV, _num(0, 12, integer=True), "年間プランの挿入ルール"),
    "plan.holiday_month_max_slots": (("president",), _num(0, 50, integer=True), "年間プランの挿入ルール"),
    "automation.chatwork_room_id":  (APP_ADMIN, _room, "接続先"),
    "automation.app_base_url":      (APP_ADMIN, _url, "接続先"),
}
# 変えさせないもの（理由は画面に出す）
LOCKED = {
    "plan.holiday_months": "正本は Calendar の会社休業日です（まだ連携していません）。ここで入れると写しになります",
    "plan.fiscal_year_start_month": "このアプリでは使っていません",
    "ai_scoring_enabled": "AI採点を呼ぶ実装がまだありません。押しても動きません",
    "ai.usage_endpoint": "書き換えると別のものにつながるため、画面からは変えません",
}
GROUP_ORDER = ("原価・価格", "競合の推計", "在庫と目標", "年間プランの挿入ルール", "接続先", "変えないもの")
ROLE_LABEL = {"admin": "管理者", "president": "社長", "devdept": "商品開発部", "prod": "生産部"}


def _biz(user_id: str) -> set:
    from app import gate
    return set(gate.roles_of(user_id))


def can_edit(key: str, user_id: str, app_role: str) -> bool:
    rule = EDITABLE.get(key)
    if rule is None:
        return False
    who = rule[0]
    if who == APP_ADMIN:
        return app_role == "admin"
    return bool(_biz(user_id) & set(who))


def editor_text(key: str) -> str:
    rule = EDITABLE.get(key)
    if rule is None:
        return LOCKED.get(key, "画面からは変えません")
    if rule[0] == APP_ADMIN:
        return "変えられるのは、アプリ権限が管理者（admin）の人です"
    return "変えられるのは、" + "・".join(ROLE_LABEL.get(r, r) for r in rule[0]) + "の業務ロールの人です"


def settings_view(user_id: str, app_role: str, connection_keys=()) -> list[dict]:
    """設定ページの表。区分ごとに並べ、区分の中では**未設定を先に**出す。"""
    from app import idea
    out = []
    for r in idea.settings():
        k = r["key"]
        if k in connection_keys and app_role != "admin":
            continue
        group = EDITABLE[k][2] if k in EDITABLE else "変えないもの"
        out.append({**r, "group": group, "editable": can_edit(k, user_id, app_role),
                    "who": editor_text(k), "locked_why": LOCKED.get(k)})
    out.sort(key=lambda x: (GROUP_ORDER.index(x["group"]) if x["group"] in GROUP_ORDER else 99,
                            x["value"] not in (None, ""), x["label"]))
    return out


def set_value(key: str, value, reason: str, user_id: str, app_role: str, ip: str = "") -> dict:
    """全体に効く設定を変える。**根拠が必須。前の値と新しい値を残す。**空欄は未設定に戻す。"""
    if key not in EDITABLE:
        raise PermissionError(LOCKED.get(key, "この設定は画面からは変えません"))
    if not can_edit(key, user_id, app_role):
        raise PermissionError(editor_text(key))
    row = store.one("SELECT * FROM setting WHERE key=?", (key,))
    if row is None:
        raise LookupError("その設定はありません")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("根拠を書いてください（あとで、なぜこの値なのかを確かめられるように）")
    v = str(value if value is not None else "").strip()
    new = None if v == "" else EDITABLE[key][1](v, row["label"])
    if new == row["value"]:
        return {"ok": True, "changed": False, "value": new}
    with store.tx() as c:
        c.execute("UPDATE setting SET value=?, updated_by=?, updated_at=? WHERE key=?",
                  (new, user_id, store.now_s(), key))
    store.audit(user_id, "setting." + key, key,
                {"before": row["value"], "after": new, "reason": reason[:500], "label": row["label"]}, ip)
    return {"ok": True, "changed": True, "value": new}


# ── 業務ロールの付け外し（アプリ権限 admin・十文字さんの選択）─────────────────
def roles_view(app_role: str) -> dict:
    import auth
    people = [{"user_id": u["user_id"], "name": u.get("name") or u["user_id"]}
              for u in auth.public_users() if u.get("active") and not u.get("test")]
    roles = store.rows(store.q("SELECT code, label, external FROM role ORDER BY sort"))
    have = {}
    for r in store.q("SELECT user_id, role_code FROM role_member"):
        have.setdefault(r["user_id"], []).append(r["role_code"])
    return {"people": sorted(people, key=lambda x: x["user_id"]), "roles": roles, "members": have,
            "can_edit": app_role == "admin"}


def set_role(target: str, role: str, on: bool, user_id: str, app_role: str, ip: str = "") -> dict:
    if app_role != "admin":
        raise PermissionError("業務ロールを付け外しできるのは、アプリ権限が管理者（admin）の人です")
    if store.one("SELECT 1 FROM role WHERE code=?", (role,)) is None:
        raise LookupError("その業務ロールはありません")
    import auth
    if not any(u["user_id"] == target for u in auth.public_users()):
        raise LookupError("その利用者はいません")
    has = store.one("SELECT 1 FROM role_member WHERE role_code=? AND user_id=?", (role, target)) is not None
    if on == has:
        return {"ok": True, "changed": False}
    if not on and role == "president" and store.val(
            "SELECT COUNT(*) FROM role_member WHERE role_code='president'", (), 0) <= 1:
        raise ValueError("社長の業務ロールを持つ人が居なくなります（年間プランの承認と G2・G3 を誰も通せなくなるため外せません）")
    with store.tx() as c:
        if on:
            c.execute("INSERT INTO role_member (role_code,user_id,granted_by,granted_at) VALUES (?,?,?,?)",
                      (role, target, user_id, store.now_s()))
        else:
            c.execute("DELETE FROM role_member WHERE role_code=? AND user_id=?", (role, target))
    store.audit(user_id, "role.grant" if on else "role.revoke", target, {"role": role}, ip)
    return {"ok": True, "changed": True}


# ── 全体に効く変更の記録（全員に見せる）────────────────────────────────
LOG_ACTIONS = ("setting.%", "role.%", "event.%", "template.%", "ideas.import%", "flow.%", "plan.version.approve")


def change_log(limit: int = 50) -> list[dict]:
    cond = " OR ".join("action LIKE ?" for _ in LOG_ACTIONS)
    out = []
    for r in store.q(f"SELECT at, user_id, action, target, detail FROM audit WHERE {cond} "
                     f"ORDER BY id DESC LIMIT ?", (*LOG_ACTIONS, limit)):
        try:
            d = json.loads(r["detail"] or "{}")
        except ValueError:
            d = {}
        out.append({"at": r["at"], "user_id": r["user_id"], "action": r["action"], "target": r["target"],
                    "label": d.get("label"), "before": d.get("before"), "after": d.get("after"),
                    "reason": d.get("reason"), "detail": d if not isinstance(d, dict) or "before" not in d else None})
    return out
