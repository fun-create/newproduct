#!/usr/bin/env python3
"""
発売後の仕事を、新商品の案件の外に作る（2026-11-09 修正依頼書 H05・H07・ADR-128）。

新商品の案件のタスクは**発売まで**。発売後の仕事は G5（発売可）を通したときに、案件外の仕事として1件ずつ作る。
元の案件へ戻れるように `work_item.project_id` と `origin` を持ち、**同じ案件・同じ origin は1件だけ**
（G5 の通し直し・移行の補正・発売日の再計算で二重に作らない。一意の索引でも止める）。

| origin | 仕事 | 担当区分 | 期限 | 作る開発タイプ |
|---|---|---|---|---|
| plc  | 生産問題のヒアリングと対応（H07） | 生産部 | 既定は発売の暦で2か月後以降の最初の営業日。案件ごとに不要・日数・日付へ変えられる | 既定で作るのは、ひな形に「生産の問題点を確認（2か月後）」があった5タイプ |
| mall | モール出品（楽天・Amazon・giftmall・Yahoo!） | Webマーケ | 発売より後の4営業日目 | ひな形にモール出品があった6タイプ |
| fba  | FBA化を検討 | 商品開発部管理者 | 発売の暦で6か月後以降の最初の営業日 | 既成デザイン |

決めた人と日: 担当区分・モール出品と FBA を案件外へ移すことは 2026-11-09 十文字さん（AskUserQuestion）。

**発売日が空なら作らない。**今日で代用しない（要確認として理由を返し、カルテに出す）。
営業日はカレンダーアプリの会社休業日で決める。登録の外の日しか無いときは、暦の日のまま作り、そう書く。
"""
from __future__ import annotations

import datetime as dt
import re

from app import assign, calfc, store

PLC_DEFAULT_FLOWS = ("freecut", "material", "meire", "readymade", "webdeco")
MALL_FLOWS = ("freecut", "material", "meire", "newmodel", "readymade", "webdeco")
FBA_FLOWS = ("readymade",)

KINDS = {
    "plc": {"title": "{name}の生産問題のヒアリングと対応", "role": "prod", "category": "発売後の生産確認", "rule": "L>2m"},
    "mall": {"title": "{name}のモール出品（楽天・Amazon・giftmall・Yahoo!）", "role": "webmkt", "category": "モール出品", "rule": "L+4"},
    "fba": {"title": "{name}のFBA化を検討", "role": "admin", "category": "FBA化の検討", "rule": "L>6m"},
}
_PLC_RULE = re.compile(r"^L>(\d{1,3})([md])$")

# 案件の中にあった発売後の行（ひな形・シートから来たもの）。**開発タイプが空の移行案件**は、これがあったかで決める。
# 名前の「発売後」だけでは決めない（修正依頼書 H05）。新商品売上レポートへの計上設定は発売日の行なので含めない
POST_ROWS = {
    "plc": ("生産の問題点を確認", "生産問題点確認（発売+2か月）"),
    "mall": ("楽天に出品", "Amazonに出品", "giftmallに出品", "Yahoo!に出品"),
    "fba": ("FBA化を検討",),
    "weekly": ("売上集計（2週間後）", "売上集計（発売+2週）"),
}


def row_kind(title: str) -> str | None:
    """タスク名 → 発売後の行の種類（plc・mall・fba・weekly）。当てはまらなければ None"""
    t = (title or "").strip()
    for k, keys in POST_ROWS.items():
        if any(t.endswith(x) if k == "mall" else t.startswith(x) for x in keys):   # 出品は「18 DUO 楽天に出品」のように頭に何か付く
            return k
    return None


def _had(pid: str, kind: str) -> bool:
    return any(row_kind(r["title"]) == kind for r in store.q("SELECT title FROM task WHERE project_id=?", (pid,)))


def _name(p: dict) -> str:
    return p.get("internal_name") or p.get("code") or p["id"]


# ── 生産確認の要否と時期（H07）──────────────────────────────
def plc_setting(p: dict) -> dict:
    """{need, rule, label, source}。rule は 'L>2m' のような式か 'YYYY-MM-DD'。"""
    raw = (p.get("plc_rule") or "").strip()
    if not raw:
        if p.get("flow_type"):
            need = p["flow_type"] in PLC_DEFAULT_FLOWS
            why = "開発タイプの既定（発売の暦で2か月後以降の最初の営業日）" if need else "開発タイプの既定は「不要」"
        else:
            need = _had(p["id"], "plc")
            why = ("開発タイプが未設定。移行したタスクに生産確認の行があったので「要」（発売の暦で2か月後以降の最初の営業日）" if need
                   else "開発タイプが未設定（既定は不要。要るなら選んでください）")
        rule = KINDS["plc"]["rule"] if need else None
        return {"need": need, "rule": rule, "label": why, "source": "既定"}
    if raw == "none":
        return {"need": False, "rule": None, "label": "不要", "source": "個別"}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return {"need": True, "rule": raw, "label": f"期限日 {raw}", "source": "個別"}
    m = _PLC_RULE.fullmatch(raw)
    if m:
        n, u = m.groups()
        return {"need": True, "rule": raw,
                "label": f"発売の暦で {n}{'か月' if u == 'm' else '日'}後以降の最初の営業日", "source": "個別"}
    return {"need": False, "rule": None, "label": f"読めない設定（{raw}）", "source": "個別"}


def set_plc(pid: str, value: str, user_id: str) -> dict:
    """value: '' ＝既定に戻す／'none'／'months:N'／'days:N'／'date:YYYY-MM-DD'。"""
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    v = (value or "").strip()
    if v in ("", "default"):
        new = None
    elif v == "none":
        new = "none"
    elif v.startswith("months:") or v.startswith("days:"):
        k, n = v.split(":", 1)
        if not n.isdigit() or not (1 <= int(n) <= (24 if k == "months" else 730)):
            raise ValueError("か月は 1〜24、日数は 1〜730 で入れてください")
        new = f"L>{int(n)}{'m' if k == 'months' else 'd'}"
    elif v.startswith("date:"):
        d = v[5:]
        try:
            new = dt.date.fromisoformat(d).isoformat()
        except ValueError:
            raise ValueError("期限日は 2027-01-15 の形で入れてください") from None
    else:
        raise ValueError("知らない設定です")
    old = p["plc_rule"]
    if (old or None) == new:
        return {"ok": True, "changed": False}
    with store.tx() as c:
        c.execute("UPDATE project SET plc_rule=?, updated_at=?, updated_by=? WHERE id=?", (new, store.now_s(), user_id, pid))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                  (pid, store.now_s(), user_id, "発売後の生産確認", f"{old or '既定'} → {new or '既定'}"))
    # もう作ってある仕事は、未完了なら期限を合わせる。不要にしたら対象外にする（**消さない**）
    p = dict(store.one("SELECT * FROM project WHERE id=?", (pid,)))
    w = store.one("SELECT * FROM work_item WHERE project_id=? AND origin='plc'", (pid,))
    if w is not None and w["status"] not in ("完了", "対象外"):
        s = plc_setting(p)
        if not s["need"]:
            with store.tx() as c:
                c.execute("UPDATE work_item SET status='対象外' WHERE id=?", (w["id"],))
            store.audit(user_id, "followup.retire", str(w["id"]), {"project": pid, "origin": "plc", "why": "不要に変えた"})
        elif p.get("launch_date"):
            due, _note = due_for(s["rule"], p["launch_date"])
            with store.tx() as c:
                c.execute("UPDATE work_item SET due_on=? WHERE id=?", (due, w["id"]))
    return {"ok": True, "changed": True, "setting": plc_setting(p)}


# ── 期限 ──────────────────────────────────────────────
def due_for(rule: str, launch: str) -> tuple[str, str]:
    """(期限, 注記)。rule が日付ならそのまま。式なら会社の営業日で計算し、できなければ暦の日。"""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", rule):
        return rule, "期限日の指定"
    from app.reschedule import Calendar, _edate, resolve
    from app import schedule_rules
    L = dt.date.fromisoformat(launch)
    try:
        cal = Calendar(L - dt.timedelta(days=7), L + dt.timedelta(days=400))
        return resolve(rule, L, cal).isoformat(), schedule_rules.label(rule)
    except calfc.NotConnected as e:
        d = L
        for op, n in schedule_rules.parse(rule):
            d = (_edate(d, n) if op == "m" else d + dt.timedelta(days=n) if op == "d"
                 else d + dt.timedelta(days=n if op == "a" else -n))
        return d.isoformat(), f"暦の日のまま（営業日に寄せられませんでした: {e}）"


# ── 作る ──────────────────────────────────────────────
def wanted(p: dict) -> list[tuple[str, str]]:
    """この案件で作る仕事 [(origin, rule)]。"""
    flow = p.get("flow_type") or ""
    out = []
    s = plc_setting(p)
    if s["need"]:
        out.append(("plc", s["rule"]))
    # 開発タイプが空の移行案件は、案件のタスクに出品・FBA の行があったかで決める
    if flow in MALL_FLOWS or (not flow and _had(p["id"], "mall")):
        out.append(("mall", KINDS["mall"]["rule"]))
    if flow in FBA_FLOWS or (not flow and _had(p["id"], "fba")):
        out.append(("fba", KINDS["fba"]["rule"]))
    return out


def ensure(pid: str, user_id: str) -> dict:
    """G5 を通したときに呼ぶ。作った・作らなかったを返す。**何度呼んでも増えない。**"""
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    p = dict(p)
    want = wanted(p)
    if not want:
        return {"created": [], "skipped": [], "why": "この案件で作る発売後の仕事はありません"}
    if not p.get("launch_date"):
        return {"created": [], "skipped": [o for o, _r in want],
                "why": "発売日が空なので作っていません（要確認。今日では代用しません）"}
    created, skipped = [], []
    for origin, rule in want:
        if store.one("SELECT 1 FROM work_item WHERE project_id=? AND origin=?", (pid, origin)):
            skipped.append(origin)                  # もうある（完了・対象外も含めて作り直さない）
            continue
        k = KINDS[origin]
        due, note = due_for(rule, p["launch_date"])
        with store.tx() as c:
            wid = c.execute(
                "INSERT INTO work_item (kind,title,category,role,assignee,due_on,status,created_at,project_id,origin) "
                "VALUES ('案件外',?,?,?,?,?,'未着手',?,?,?)",
                (k["title"].format(name=_name(p)), k["category"], k["role"], assign.for_role(k["role"]),
                 due, store.now_s(), pid, origin)).lastrowid
            c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                      (pid, store.now_s(), user_id, "発売後の仕事を作った",
                       f"{k['category']}（案件外の仕事 {wid}・期限 {due}・{note}）"))
        store.audit(user_id, "followup.create", str(wid), {"project": pid, "origin": origin, "due": due})
        created.append({"origin": origin, "id": wid, "due_on": due, "note": note})
    return {"created": created, "skipped": skipped, "why": ""}


def on_launch_date_change(pid: str, user_id: str) -> int:
    """発売日が変わったら、**未完了の**発売後の仕事の期限を合わせる。完了・対象外は触らない。"""
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None or not p["launch_date"]:
        return 0
    p = dict(p)
    rules = dict(wanted(p))
    n = 0
    for w in store.q("SELECT * FROM work_item WHERE project_id=? AND origin IS NOT NULL "
                     "AND status NOT IN ('完了','対象外')", (pid,)):
        rule = rules.get(w["origin"])
        if not rule:
            continue
        due, _note = due_for(rule, p["launch_date"])
        if due != w["due_on"]:
            with store.tx() as c:
                c.execute("UPDATE work_item SET due_on=? WHERE id=?", (due, w["id"]))
            store.audit(user_id, "followup.redue", str(w["id"]), {"project": pid, "from": w["due_on"], "to": due})
            n += 1
    return n


def of_project(pid: str) -> list[dict]:
    """カルテに出す、この案件の発売後の仕事。"""
    return store.rows(store.q(
        "SELECT w.id, w.origin, w.title, w.role, r.label AS role_label, w.due_on, w.status "
        "FROM work_item w LEFT JOIN role r ON r.code=w.role WHERE w.project_id=? AND w.origin IS NOT NULL "
        "ORDER BY w.due_on", (pid,)))
