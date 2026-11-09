#!/usr/bin/env python3
"""
ChatWork「FUN 商品開発」へ、承認・確認・依頼・催促を **TO 担当者** で送る（2026-11-09 十文字さん・ADR-135）。

> 社長に承認や確認をしてもらいたいときに、その内容の付近に「ChatWork で承認依頼する」「確認依頼をする」
> ボタンを用意し、押すと TO 担当者でメッセージが送信される

## 決まり

- **人が押したときだけ送る。**押す前に、送る文面と TO の相手をそのまま見せる（ADR-034。ChatWork は取り消せない）
- 送り主は FUN-CREATE BOT（ADR-067 と同じ鍵 `config/chatwork.env`）。**だから本文に「依頼した人」を書く**
  （BOT から出るので、誰が頼んだかが本文に無いと分からない）
- 送り先は設定 `ask.chatwork_room_id`（既定は「FUN 商品開発」407742634）。自動化依頼の部屋とは別
- TO の相手は、ゲートなら承認者（業務ロール）、年間プランなら社長、タスク・仕事・改善要望なら担当者
  （担当者が空なら担当区分の人）。**押した本人は TO に入れない**
- ChatWork の account_id は、その部屋のメンバーの名前とこのアプリの利用者の名前を突き合わせて決める
  （空白の違いは見ない）。合わない人は設定 `ask.chatwork_accounts`（user_id=account_id）で足す。
  **TO が付かない人がいたら送らない**（その人に届かないまま「送った」にしない）
- 本文の中の利用者が書いた語（題・一言）は `[` を全角にして、`[toall]` などのタグとして効かないようにする
- 同じものを2分以内にもう一度押したら止める（二度押し）。それより後は「催促」として送れる。送った記録は消さない
"""
from __future__ import annotations

import json
import re
import time

from app import chatwork as cw
from app import store

ROOM_SETTING = "ask.chatwork_room_id"
ACCOUNTS_SETTING = "ask.chatwork_accounts"
DEFAULT_ROOM = "407742634"                       # FUN 商品開発（2026-11-09 BOT がメンバーであることを確かめた）
KINDS = ("gate", "plan", "task", "work", "improve")
PURPOSES = ("承認", "確認", "依頼", "催促")
TITLE = {"承認": "承認のお願い", "確認": "確認のお願い", "依頼": "お願い", "催促": "催促"}
DOUBLE_PRESS_SEC = 120
DONE = ("完了", "対象外", "見送り")

_members_cache: dict = {}
MEMBERS_TTL = 600


# ── 設定 ─────────────────────────────────────────────────
def _setting(key: str, default=None):
    r = store.one("SELECT value FROM setting WHERE key=?", (key,))
    return default if r is None or r["value"] in (None, "") else r["value"]


def room_id() -> str:
    return str(_setting(ROOM_SETTING, DEFAULT_ROOM))


def parse_accounts(v: str | None) -> dict:
    """「user_id=account_id、…」。壊れていたら ValueError（保存のときに止める）"""
    out = {}
    for part in re.split(r"[、,\n]", v or ""):
        part = part.strip()
        if not part:
            continue
        k, sep, a = part.partition("=")
        if not sep or not k.strip() or not re.fullmatch(r"\d{3,15}", a.strip()):
            raise ValueError(f"「{part}」は user_id=数字 の形で入れてください")
        out[k.strip()] = a.strip()
    return out


def seed_settings() -> int:
    rows = [(ROOM_SETTING, "承認・確認・依頼・催促を送る ChatWork の部屋ID", DEFAULT_ROOM,
             "FUN 商品開発。送り主は FUN-CREATE BOT（config/chatwork.env）なので、BOT がこの部屋のメンバーであること"),
            (ACCOUNTS_SETTING, "ChatWork の account_id（名前で合わない人だけ）", None,
             "未設定です。部屋のメンバーの名前と利用者の名前が合う人は入れなくて構いません。合わない人だけ user_id=account_id を 、 で区切って入れます")]
    for key, label, default, why in rows:
        store.ex("INSERT INTO setting (key,value,label,kind,unit,why) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET label=excluded.label,kind=excluded.kind,why=excluded.why",
                 (key, default, label, "text", None, why))
    return len(rows)


# ── 人 ───────────────────────────────────────────────────
def _norm(name: str) -> str:
    return re.sub(r"[\s　]+", "", name or "")


def _users() -> dict:
    try:
        import auth
        return {u["user_id"]: (u.get("name") or u["user_id"]) for u in auth.public_users()}
    except Exception:                               # noqa: BLE001 台帳が読めなくても ID で出す
        return {}


def _fetch_members(room: str) -> list[dict]:
    import urllib.request
    tok = cw._read_token()
    req = urllib.request.Request(f"https://api.chatwork.com/v2/rooms/{room}/members",
                                 headers={"X-ChatWorkToken": tok})
    try:
        with urllib.request.urlopen(req, timeout=cw.TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:                          # noqa: BLE001 トークンを混ぜずに理由だけ
        raise RuntimeError(f"ChatWork の部屋のメンバーを読めません（{type(e).__name__}）") from None


def members(room: str, *, fetch=None) -> list[dict]:
    """部屋のメンバー（account_id・name）。10分だけ覚えておく。`fetch` は検査で差し替える"""
    hit = _members_cache.get(room)
    if fetch is None and hit and time.time() - hit[0] < MEMBERS_TTL:
        return hit[1]
    ms = (fetch or _fetch_members)(room)
    if fetch is None:
        _members_cache[room] = (time.time(), ms)
    return ms


def account_map(room: str, *, fetch=None) -> dict:
    """user_id → account_id。名前の突き合わせ＋設定の上書き"""
    by_name = {_norm(m.get("name", "")): str(m["account_id"]) for m in members(room, fetch=fetch)}
    out = {uid: by_name[_norm(n)] for uid, n in _users().items() if _norm(n) in by_name}
    try:
        out.update(parse_accounts(_setting(ACCOUNTS_SETTING)))
    except ValueError:
        pass                                         # 壊れた設定は保存のときに止まる。ここでは名前の分だけで送る
    return out


def _role_people(roles, approval: bool) -> list[str]:
    if not roles:
        return []
    ph = ",".join("?" * len(roles))
    sql = f"SELECT user_id FROM role_member WHERE role_code IN ({ph})"
    if approval:
        sql += f" UNION SELECT user_id FROM approval_member WHERE role_code IN ({ph})"
    return sorted({r["user_id"] for r in store.q(sql, tuple(roles) * (2 if approval else 1))})


# ── 何について頼むか ──────────────────────────────────────
def _safe(s) -> str:
    """利用者が書いた語を ChatWork のタグとして効かせない（[toall] で全員に飛ばない）"""
    return str(s or "").replace("[", "［").replace("]", "］").strip()


def _base() -> str:
    return (_setting("automation.app_base_url") or "").rstrip("/")


def subject(kind: str, target: str, gate: str = "") -> dict:
    """頼む中身。{title, lines, to, url, due_on, done, default_purpose}"""
    if kind not in KINDS:
        raise ValueError("知らない種類です")
    if kind == "gate":
        from app import gate as gm
        p = store.one("SELECT id, internal_name, launch_date FROM project WHERE id=?", (target,))
        gd = next((g for g in gm.defs() if g["gate"] == gate), None)
        if p is None or gd is None:
            raise LookupError("その案件・ゲートはありません")
        name = _safe(p["internal_name"])
        return {"title": f"{gate} {gd['name']}（{name}）",
                "lines": [f"商品: {name}", f"ゲート: {gate} {gd['name']}",
                          "発売予定日: " + (p["launch_date"] or "未定")],
                "ask": f"NEW PRODUCT で中身を確かめて、{gate} の判定（通過・差戻し・保留・中止）を記録してください",
                "to": _role_people(gd["approver_role"], approval=True),
                "url": f"/#/projects/{p['id']}?sec=G", "due_on": None, "done": False, "default_purpose": "承認"}
    if kind == "plan":
        v = store.one("SELECT id, fiscal_year, label, state FROM plan_version WHERE id=?", (target,))
        if v is None:
            raise LookupError("その版はありません")
        return {"title": f"年間プラン {v['label']}",
                "lines": [f"年間プラン: {v['fiscal_year']}年度 {v['label']}（{v['state']}）"],
                "ask": "NEW PRODUCT で枠を確かめて、よければ「この版を承認する」を押してください",
                "to": _role_people(["president"], approval=True),
                "url": f"/#/plan?v={v['id']}", "due_on": None, "done": v["state"] != "策定中",
                "default_purpose": "承認"}
    if kind in ("task", "work"):
        if kind == "task":
            t = store.one("SELECT t.*, p.internal_name AS project FROM task t JOIN project p ON p.id=t.project_id "
                          "WHERE t.id=?", (target,))
        else:
            t = store.one("SELECT w.*, NULL AS project FROM work_item w WHERE w.id=?", (target,))
        if t is None:
            raise LookupError("そのタスクはありません")
        lines = ([f"商品: {_safe(t['project'])}"] if t["project"] else []) + [f"タスク: {_safe(t['title'])}",
                                                                       f"状態: {t['status']}"]
        return {"title": _safe(t["title"]), "lines": lines,
                "ask": "NEW PRODUCT のタスクで中身を確かめて、終わったら進捗を「完了」にしてください",
                "to": [t["assignee"]] if t["assignee"] else _role_people([t["role"]] if t["role"] else [], approval=False),
                "url": f"/#/tasks?when=all&tab={'project' if kind == 'task' else 'work'}&focus={'task' if kind == 'task' else 'work_item'}-{t['id']}",
                "due_on": t["due_on"], "done": t["status"] in DONE,
                "default_purpose": "催促" if t["due_on"] and t["due_on"] < store.today_s() else "依頼"}
    r = store.one("SELECT i.*, p.internal_name AS project FROM improve_request i "
                  "LEFT JOIN project p ON p.id=i.project_id WHERE i.id=?", (target,))
    if r is None:
        raise LookupError("その改善要望はありません")
    lines = ([f"商品: {_safe(r['project'])}"] if r["project"] else []) + [f"改善要望: {_safe(r['title'])}",
                                                                   f"優先度: {r['priority']}・状態: {r['status']}"]
    return {"title": "改善要望: " + _safe(r["title"]), "lines": lines,
            "ask": "NEW PRODUCT の改善要望で中身を確かめて、進め方（状態・期限）を入れてください",
            "to": [r["assignee"]] if r["assignee"] else _role_people([r["role"]] if r["role"] else [], approval=False),
            "url": "/#/improve", "due_on": r["due_on"], "done": r["status"] in DONE,
            "default_purpose": "催促" if r["due_on"] and r["due_on"] < store.today_s() else "依頼"}


def _last(kind: str, target: str, gate: str) -> list[dict]:
    return store.rows(store.q("SELECT * FROM ask_log WHERE kind=? AND target=? AND COALESCE(gate,'')=? "
                              "ORDER BY id DESC LIMIT 5", (kind, str(target), gate or "")))


# ── 文面 ─────────────────────────────────────────────────
def compose(kind: str, target: str, gate: str, purpose: str, note: str, user_id: str, *, fetch=None) -> dict:
    """送る前に見せるもの。**送るときもこれと同じ文面を送る**"""
    if purpose not in PURPOSES:
        raise ValueError("承認・確認・依頼・催促 から選んでください")
    s = subject(kind, str(target), gate or "")
    names = _users()
    room = room_id()
    blockers = []
    to_ids = [u for u in s["to"] if u and u != user_id]
    if not to_ids:
        blockers.append("TO にする人がいません（担当者・担当区分・承認者を入れてください）"
                        if not s["to"] else "TO にする人があなた自身だけです")
    st = cw.status(room)
    amap = {}
    if st["has_token"]:
        try:
            amap = account_map(room, fetch=fetch)
        except RuntimeError as e:
            blockers.append(str(e))
    else:
        blockers.append(st["why"])
    to = [{"user_id": u, "name": names.get(u, u), "account_id": amap.get(u)} for u in to_ids]
    miss = [x["name"] for x in to if not x["account_id"]]
    if miss and st["has_token"] and not any("メンバーを読めません" in b for b in blockers):   # 1人でも宛先不明なら送らない（全員不明でも）
        blockers.append("ChatWork の宛先が分からない人がいます: " + "・".join(miss)
                        + "（部屋のメンバーにいないか、名前が違います。設定 ask.chatwork_accounts で足せます）")
    if s["done"]:
        blockers.append("もう終わっています（完了・承認済みなど）。送る必要はありません")
    last = _last(kind, str(target), gate or "")
    n_before = len(last)
    head = TITLE[purpose] + (f"（{n_before + 1}回目）" if purpose == "催促" and n_before else "")
    lines = [("".join(f"[To:{x['account_id'] or '?'}]{x['name']}さん" for x in to))]
    lines.append(f"[info][title]NEW PRODUCT より・{head}: {s['title']}[/title]")
    lines += s["lines"]
    if s["due_on"]:
        late = s["due_on"] < store.today_s()
        lines.append(f"期限: {s['due_on']}" + ("（過ぎています）" if late else ""))
    lines.append(f"依頼した人: {names.get(user_id, user_id)}")
    if _safe(note):
        lines += ["[hr]" + _safe(note)[:1000]]
    lines += ["[hr]やってほしいこと: " + s["ask"]]
    if _base():
        lines.append("開く: " + _base() + s["url"])
    lines.append("[/info]")
    return {"ready": not blockers, "blockers": blockers, "room": room, "to": to, "text": "\n".join(lines),
            "purpose": purpose, "default_purpose": s["default_purpose"], "purposes": PURPOSES,
            "last": [{"purpose": r["purpose"], "sent_at": r["sent_at"],
                      "sent_by": names.get(r["sent_by"], r["sent_by"]),
                      "to": [names.get(u, u) for u in json.loads(r["to_users"] or "[]")]} for r in last],
            "note": "ChatWork は取り消せません。上の文面と TO の相手を確かめてから送ってください（送り主は FUN-CREATE BOT）"}


def send(kind: str, target: str, gate: str, purpose: str, note: str, user_id: str, *, sender=None, fetch=None) -> dict:
    """人が押したときだけ呼ぶ"""
    c = compose(kind, target, gate, purpose, note, user_id, fetch=fetch)
    if not c["ready"]:
        raise ValueError("送れません: " + " ／ ".join(x for x in c["blockers"] if x))
    last = store.one("SELECT sent_at FROM ask_log WHERE kind=? AND target=? AND COALESCE(gate,'')=? AND purpose=? "
                     "AND sent_by=? ORDER BY id DESC LIMIT 1", (kind, str(target), gate or "", purpose, user_id))
    if last:
        try:
            import datetime as dt
            ago = (dt.datetime.now() - dt.datetime.fromisoformat(last["sent_at"])).total_seconds()
        except ValueError:
            ago = DOUBLE_PRESS_SEC + 1
        if ago < DOUBLE_PRESS_SEC:
            raise ValueError(f"さっき（{last['sent_at']}）同じものを送っています。二度押しを止めました")
    res = cw.post(c["room"], c["text"], sender=sender)
    with store.tx() as tx:
        aid = tx.execute("INSERT INTO ask_log (kind,target,gate,purpose,to_users,room,message_id,note,sent_by,sent_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (kind, str(target), gate or None, purpose, json.dumps([x["user_id"] for x in c["to"]]),
                          c["room"], str(res.get("message_id") or ""), (note or "")[:1000], user_id,
                          store.now_s())).lastrowid
    store.audit(user_id, "ask.send", f"{kind}:{target}{(':' + gate) if gate else ''}",
                {"purpose": purpose, "to": [x["user_id"] for x in c["to"]], "message_id": res.get("message_id"), "log": aid})
    return {"ok": True, "message_id": res.get("message_id"), "to": [x["name"] for x in c["to"]], "room": c["room"]}
