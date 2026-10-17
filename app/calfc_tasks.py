#!/usr/bin/env python3
"""
カレンダーアプリ（calfc）へタスクを送る（FR-121・ADR-100・2026-10-13 十文字さんの決定・2026-10-17 実装）。

十文字さんの決定（10-13）: **予定ではなくタスクとして送る**。範囲は**期限と担当の入ったタスク全部**（案件のタスクと案件外の仕事）。

## 送り先（calfc のコードを読んで確かめた・2026-10-13）

- `POST /api/svc/task/upsert` … `ext_ref` で冪等。`title / detail / due_on / status(todo|done) / url`、`members`＝カレンダーの member_id の配列。
  作るときは `X-Actor`（共通ログインID）から担当を引く（引けなければ 422）。こちらは担当を `members` で明示して送る
- `POST /api/svc/task/delete` … `ext_ref` で消す（無くても成功）
- `GET /api/svc/members` … member_id と user_id（共通ログインID）の対応。**両方別の名前空間**なので user_id → member_id に引き直す
- 認証は休業日と同じ鍵（`config/calfc_token`）。**いまの鍵は `/api/svc/holidays` だけを通す**ので、カレンダー側で
  `members`・`task/upsert`・`task/delete` を足してもらうまでは「カレンダーの口がまだ無い」と出る（依頼書は fun-create-ec-agents `docs/product-dev/contracts/`）

## 守っていること

- **正は NEW PRODUCT の一方向。**カレンダーで直した題や期限はこちらに戻らない（次の同期で戻る）。完了・未完了だけはカレンダーから
  `POST /api/svc/task/done`（server.py・専用の鍵 `config/calfc_callback_token`）で受け、こちらの状態を変える
- 送った中身の指紋を `calfc_task_sent` に残し、**変わったものだけ送る**。対象から外れたもの（対象外・中止・期限や担当が消えた）は消す
- 担当がカレンダーのメンバーに無い・期限が無い・担当が無いタスクは**送らず、件数と理由を数える**（黙って落とさない）
- 検査は `opener` を差し替える（外へ出さない）
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app import calfc, store

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_BASE = os.environ.get("NEWPRODUCT_CALFC_BASE") or calfc.API.rsplit("/api/", 1)[0]
PUBLIC_URL = os.environ.get("NEWPRODUCT_PUBLIC_URL", "https://newproduct.fun-create.co.jp")
TIMEOUT = 8
ACTOR = "newproduct/tasks"        # **ASCII のみ**（X-Actor。作るときは担当の user_id を使う）
_MEMBERS: tuple[float, dict] | None = None
_TTL = 300
_LOCK = threading.Lock()
_LAST: dict = {"at": None, "result": None}


class Unavailable(RuntimeError):
    """カレンダーの口が使えない（鍵が無い・道が無い・つながらない）。**「送るものが無い」ではない。**"""


def _req(method: str, path: str, body: dict | None = None, actor: str = ACTOR, opener=None) -> dict:
    url = API_BASE + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    try:
        tok = calfc._token()
    except calfc.NotConnected as e:
        raise Unavailable(str(e)) from None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {tok}", "X-Actor": actor, "Content-Type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            j = {}
        msg = (j.get("error") if isinstance(j, dict) else None) or f"カレンダーアプリが {e.code} を返しました"
        if e.code == 403:
            raise Unavailable(f"カレンダーの口（{path}）がこの鍵に許可されていません。カレンダー側で道を足してもらうまで送れません") from None
        raise Unavailable(msg) from None
    except calfc.NotConnected as e:
        raise Unavailable(str(e)) from None
    except (OSError, ValueError) as e:
        raise Unavailable(f"カレンダーアプリにつながりません: {e}") from None


def members(opener=None) -> dict:
    """user_id（共通ログインID）→ member_id。"""
    global _MEMBERS
    if _MEMBERS and time.time() - _MEMBERS[0] < _TTL and opener is None:
        return _MEMBERS[1]
    j = _req("GET", "/api/svc/members", opener=opener)
    m = {x["user_id"]: x["member_id"] for x in (j.get("members") or []) if x.get("user_id") and x.get("member_id")}
    if opener is None:
        _MEMBERS = (time.time(), m)
    return m


# ── 送るもの ──────────────────────────────────────────
def desired() -> dict:
    """送る対象（期限と担当の入ったタスク）と、送らない理由の件数。"""
    rows, skipped = {}, {"期限なし": 0, "担当なし": 0}
    for t in store.q("SELECT t.*, p.internal_name, p.stage, r.label AS role_label FROM task t "
                     "JOIN project p ON p.id=t.project_id LEFT JOIN role r ON r.code=t.role "
                     "WHERE t.status != '対象外' AND p.stage != '中止'"):
        if not t["due_on"]:
            skipped["期限なし"] += 1
            continue
        if not (t["assignee"] or "").strip():
            skipped["担当なし"] += 1
            continue
        ref = f"newproduct:task:{t['id']}"
        rows[ref] = {"ext_ref": ref, "kind": "task", "local_id": t["id"], "assignee": t["assignee"].strip(),
                     "title": f"{t['title']}（{t['internal_name'] or t['project_id']}）",
                     "detail": "NEW PRODUCT の案件のタスク" + (f"・ロール {t['role_label']}" if t["role_label"] else "")
                               + (f"・標準 {t['hours']}h" if t["hours"] is not None else "")
                               + (f"・いま {t['status']}" if t["status"] not in ("未着手", "完了") else ""),
                     "due_on": t["due_on"], "status": "done" if t["status"] == "完了" else "todo",
                     "url": f"{PUBLIC_URL}/#/projects/{t['project_id']}"}
    for w in store.q("SELECT w.*, r.label AS role_label FROM work_item w LEFT JOIN role r ON r.code=w.role "
                     "WHERE w.status != '対象外'"):
        if not w["due_on"]:
            skipped["期限なし"] += 1
            continue
        if not (w["assignee"] or "").strip():
            skipped["担当なし"] += 1
            continue
        ref = f"newproduct:work:{w['id']}"
        rows[ref] = {"ext_ref": ref, "kind": "work", "local_id": w["id"], "assignee": w["assignee"].strip(),
                     "title": f"{w['title']}（{w['kind']}" + (f"・{w['dept']}" if w["dept"] else "") + "）",
                     "detail": "NEW PRODUCT の案件外の仕事" + (f"・{w['category']}" if w["category"] else "")
                               + (f"・標準 {w['hours']}h" if w["hours"] is not None else ""),
                     "due_on": w["due_on"], "status": "done" if w["status"] == "完了" else "todo",
                     "url": f"{PUBLIC_URL}/#/tasks?tab=" + ("request" if w["kind"] == "他部署依頼" else "work")}
    return {"rows": rows, "skipped": skipped}


def _digest(r: dict, member_id: str) -> str:
    key = json.dumps([r["title"], r["detail"], r["due_on"], r["status"], r["url"], member_id], ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def plan(opener=None) -> dict:
    """送る・消す・送れない を分ける（送らない）。カレンダーの口が無ければ Unavailable。"""
    d = desired()
    mem = members(opener)
    sent = {r["ext_ref"]: dict(r) for r in store.q("SELECT * FROM calfc_task_sent")}
    upsert, unsendable = [], {}
    for ref, r in d["rows"].items():
        mid = mem.get(r["assignee"])
        if not mid:
            unsendable[ref] = f"担当 {r['assignee']} がカレンダーのメンバーにいません"
            continue
        dg = _digest(r, mid)
        s = sent.get(ref)
        if s is None or s["state"] != "送った" or s["digest"] != dg:
            upsert.append({**r, "member_id": mid, "digest": dg})
    delete = [ref for ref, s in sent.items() if s["state"] == "送った" and (ref not in d["rows"] or ref in unsendable)]
    return {"upsert": upsert, "delete": delete, "unsendable": unsendable, "skipped": d["skipped"],
            "total": len(d["rows"]), "already": len(d["rows"]) - len(upsert) - len(unsendable)}


def sync(opener=None) -> dict:
    """差分だけ送る。途中で止まったら、そこまでを覚えて理由を返す（落ちない失敗にしない）。"""
    with _LOCK:
        try:
            p = plan(opener)
        except Unavailable as e:
            _LAST.update(at=store.now_s(), result={"error": str(e), "sent": 0, "deleted": 0})
            return {"error": str(e), "sent": 0, "deleted": 0, "unsendable": {}, "skipped": {}}
        sent = deleted = 0
        err = None
        now = store.now_s()
        for r in p["upsert"]:
            try:
                ans = _req("POST", "/api/svc/task/upsert",
                           {"ext_ref": r["ext_ref"], "title": r["title"], "detail": r["detail"], "due_on": r["due_on"],
                            "status": r["status"], "url": r["url"], "members": [r["member_id"]]},
                           actor=r["assignee"], opener=opener)
            except Unavailable as e:
                err = str(e)
                break
            with store.tx() as c:
                c.execute("INSERT INTO calfc_task_sent (ext_ref,kind,local_id,digest,calfc_task_id,sent_at,state) "
                          "VALUES (?,?,?,?,?,?,'送った') ON CONFLICT(ext_ref) DO UPDATE SET digest=excluded.digest,"
                          "calfc_task_id=excluded.calfc_task_id,sent_at=excluded.sent_at,state='送った'",
                          (r["ext_ref"], r["kind"], r["local_id"], r["digest"], str(ans.get("task_id") or ""), now))
            sent += 1
        if err is None:
            for ref in p["delete"]:
                try:
                    _req("POST", "/api/svc/task/delete", {"ext_ref": ref}, opener=opener)
                except Unavailable as e:
                    err = str(e)
                    break
                with store.tx() as c:
                    c.execute("UPDATE calfc_task_sent SET state='消した', sent_at=? WHERE ext_ref=?", (now, ref))
                deleted += 1
        out = {"error": err, "sent": sent, "deleted": deleted, "unsendable": p["unsendable"], "skipped": p["skipped"],
               "total": p["total"]}
        _LAST.update(at=now, result=out)
        return out


def sync_later() -> None:
    """画面の書き込みの後に裏で同期する。失敗は `status()` に残るだけ（画面の操作は止めない）。"""
    if os.environ.get("NEWPRODUCT_CALFC_TASKS_OFF"):
        return
    threading.Thread(target=sync, daemon=True, name="calfc-tasks-sync").start()


def status() -> dict:
    """設定ページに出す状態。"""
    ok, why = True, None
    try:
        calfc._token()
    except calfc.NotConnected as e:
        ok, why = False, str(e)
    d = desired()
    n_sent = store.val("SELECT COUNT(*) FROM calfc_task_sent WHERE state='送った'", (), 0)
    last = store.one("SELECT MAX(sent_at) FROM calfc_task_sent")
    return {"configured": ok, "why": why, "target": len(d["rows"]), "skipped": d["skipped"], "sent": n_sent,
            "last_sent_at": last[0] if last else None, "last_sync": _LAST["result"], "last_sync_at": _LAST["at"]}


# ── カレンダーからの完了・未完了（server.py `/api/svc/task/done`）──────────────
def set_done(ext_ref: str, done: bool, actor: str) -> dict:
    from app import task as task_m
    ref = (ext_ref or "").strip()
    parts = ref.split(":")
    if len(parts) != 3 or parts[0] != "newproduct" or parts[1] not in ("task", "work") or not parts[2].isdigit():
        raise ValueError("ext_ref の形が違います（newproduct:task:<id> ／ newproduct:work:<id>）")
    kind, tid = parts[1], int(parts[2])
    table = "task" if kind == "task" else "work_item"
    cur = store.one(f"SELECT status FROM {table} WHERE id=?", (tid,))
    if cur is None:
        raise LookupError("そのタスクは NEW PRODUCT にありません")
    if cur["status"] == "対象外":
        raise ValueError("対象外のタスクは完了・未完了を変えられません")
    new = "完了" if done else "未着手"
    if cur["status"] == new or (not done and cur["status"] in ("着手", "保留")):
        return {"ok": True, "status": cur["status"], "changed": False}
    task_m.set_status(kind if kind == "task" else "work", tid, new, actor or "calfc")
    return {"ok": True, "status": new, "changed": True}
