#!/usr/bin/env python3
"""
AI予算の確認と記録（F-13-2 ／ FR-146）。2026-09-24 決定・2026-09-25 実装。

**枠の正本は Auto GROWTH。**こちらは持たない。`/opt/autogrowth/config` は
`rwx------` で親から通り抜けられないので、ファイルは読まず**同じホストの口を叩く**
（ADR-036）。

    GET  http://127.0.0.1:8789/api/ai-usage?job=newproduct-image
    POST http://127.0.0.1:8789/api/ai-usage
         {"job":…, "model":…, "usd":…, "request_id":…, "note":…}

## 守っていること

**確かめられないときは使わない。**口につながらなければ `unavailable` で止める。
**黙って使うほうが危ない。**上限の無い呼び出しになる。

**job 名は `newproduct-` で始める。**始まらないと枠に入らず、
**全体50.0だけが効く＝会社全体の枠を引ける。**実測（2026-09-25）:

    job=newproduct-image → scope "newproduct-*" / cap 5.0 / remaining 5.0
    job=newproduct       → scope ""             / cap 0.0 / remaining **null**

**送る前にこちらで弾く。**向こうは `ok:true` を返してしまうので、
こちらが弾かないと気づけない。

**`spent` は枠の合算。**`newproduct-image` も `newproduct-text` も
`scope: "newproduct-*"` で返り、金額はその合計。画面には
**「枠（newproduct-*）の使用額」**と書く。「image の使用額」と書くと、
後で読む人が誤解する（Auto GROWTH の申し送り・2026-09-25）。

**`request_id` を必ず付ける。**送信がタイムアウトしたとき成否を区別できない。
再送しても、同じ `request_id` なら向こうが二重に数えない（`counted:false`）。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import store

DEFAULT_ENDPOINT = "http://127.0.0.1:8789/api/ai-usage"
ENDPOINT_SETTING = "ai.usage_endpoint"
JOB_PREFIX = "newproduct-"
JOBS = ("newproduct-image", "newproduct-text")
TIMEOUT = 10


class BudgetUnavailable(RuntimeError):
    """**確かめられなかった。**使わないための例外（「0円だった」ではない）。"""


class OverCap(RuntimeError):
    """枠を使い切っている。"""


class BadJob(ValueError):
    """枠に入らない job 名。**送る前に弾く。**"""


def endpoint() -> str:
    r = store.one("SELECT value FROM setting WHERE key=?", (ENDPOINT_SETTING,))
    v = (r["value"] if r else None) or ""
    return v.strip() or DEFAULT_ENDPOINT


def seed_settings() -> int:
    store.ex("INSERT INTO setting (key,value,label,kind,unit,why) "
             "VALUES (?,?,?,?,?,?) "
             "ON CONFLICT(key) DO UPDATE SET label=excluded.label,"
             "kind=excluded.kind,unit=excluded.unit,why=excluded.why",
             (ENDPOINT_SETTING, DEFAULT_ENDPOINT, "AI予算の確認先（Auto GROWTH）",
              "text", None,
              "同じホストの口。**枠の正本はあちら。**外からは 403 で落ちる"))
    return 1


def check_job(job: str) -> str:
    """**送る前に弾く。**`newproduct-` で始まらないと枠に入らない。"""
    job = (job or "").strip()
    if not job:
        raise BadJob("job 名がありません")
    if not job.startswith(JOB_PREFIX):
        raise BadJob(
            f"job 名は {JOB_PREFIX!r} で始めてください（受け取った値: {job!r}）。"
            "始まらないと **job 別の上限が効かず、全体の枠を引きます**"
            "（実測: cap 0.0 / remaining null）")
    return job


def _call(method: str, url: str, payload: dict | None, *, caller=None) -> dict:
    if caller is not None:
        # **差し替えた呼び出しも、本物と同じように失敗しうる。**
        # ここで包まないと、検査だけ例外の型が変わって本番と挙動がずれる
        try:
            return caller(method, url, payload)
        except (OSError, ValueError) as e:
            raise BudgetUnavailable(
                f"AI予算の口につながりません（{type(e).__name__}）。"
                "**確かめられないので使いません。**"
                f"口: {url}") from None
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # **相手が本物の予算の口か確かめる。**同じポートに別のものが居ることがある
        # （2026-09-25、手元の Mac の 8789 が別プロセスで 401 を返していた）。
        # 予算の返事の形（`reason` か `cap`）を持つときだけ、返事として扱う。
        body = e.read().decode("utf-8", "replace")[:300]
        try:
            d = json.loads(body)
        except ValueError:
            d = None
        if isinstance(d, dict) and ("reason" in d or "cap" in d):
            return d
        raise BudgetUnavailable(
            f"AI予算の口が {e.code} を返しました（予算の返事の形ではありません）。"
            f"**別のものが同じポートに居る可能性があります。**口: {url} / 応答: {body[:120]}"
        ) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise BudgetUnavailable(
            f"AI予算の口につながりません（{type(e).__name__}）。"
            "**確かめられないので使いません。**"
            f"口: {url}") from None


def check(job: str, *, caller=None) -> dict:
    """使ってよいか。**例外を投げずに状態を返す**（画面に出すため）。"""
    try:
        j = check_job(job)
    except BadJob as e:
        return {"state": "bad_job", "usable": False, "why": str(e), "job": job}
    url = endpoint() + "?" + urllib.parse.urlencode({"job": j})
    try:
        d = _call("GET", url, None, caller=caller)
    except BudgetUnavailable as e:
        # **「使えない」と「使っていない」を混ぜない**（N-10）
        return {"state": "unavailable", "usable": False, "why": str(e), "job": j}
    if not isinstance(d, dict) or not ({"ok", "cap", "reason"} & set(d)):
        return {"state": "unavailable", "usable": False, "job": j,
                "why": "AI予算の口が、予算の返事の形で応えていません。"
                       f"**別のものが同じポートに居る可能性があります。**口: {endpoint()}"}
    if not d.get("ok"):
        return {"state": "over_cap" if d.get("reason") == "over_cap" else "refused",
                "usable": False, "why": d.get("detail") or d.get("reason") or "断られました",
                "job": j, **{k: d.get(k) for k in ("cap", "spent", "remaining")}}
    rem = d.get("remaining")
    if d.get("cap") in (None, 0, 0.0) or rem is None:
        return {"state": "no_cap", "usable": False, "job": j,
                "why": f"job {j!r} に枠がありません（cap={d.get('cap')}）。"
                       "Auto GROWTH の `per_job` に入っているか確かめてください",
                **{k: d.get(k) for k in ("cap", "spent", "remaining", "scope")}}
    return {
        "state": "ok" if rem > 0 else "over_cap", "usable": rem > 0, "job": j,
        "scope": d.get("scope"), "cap": d.get("cap"), "spent": d.get("spent"),
        "remaining": rem, "month_total": d.get("month_total"),
        "month_cap": d.get("month_cap"),
        # **合算であることを言葉で持たせる**（画面がここを出す）
        "spent_label": f"枠（{d.get('scope')}）の今月の使用額",
        "note": "この金額は **枠の合算**です。"
                f"{' と '.join(JOBS)} が同じ 5.0 を分け合います（ADR-037）。",
    }


def record(job: str, model: str, usd: float, note: str = "",
           request_id: str | None = None, *, caller=None) -> dict:
    """使った分を記録する。**`request_id` を必ず付ける**（再送で二重に数えない）。

    **記録できなかったら成功扱いにしない。**例外で上げる。
    """
    j = check_job(job)
    try:
        amount = float(usd)
    except (TypeError, ValueError):
        raise ValueError(f"金額が数ではありません: {usd!r}") from None
    if amount < 0:
        raise ValueError("金額が負です")
    rid = request_id or uuid.uuid4().hex
    d = _call("POST", endpoint(),
              {"job": j, "model": model or "", "usd": amount,
               "request_id": rid, "note": note or ""}, caller=caller)
    if not d.get("ok"):
        if d.get("reason") == "over_cap":
            raise OverCap(d.get("detail")
                          or f"枠を使い切っています（cap {d.get('cap')} / "
                             f"spent {d.get('spent')}）")
        raise BudgetUnavailable(f"記録を断られました: {d.get('reason') or d}")
    return {"request_id": rid, "counted": d.get("counted"),
            "recorded_usd": d.get("recorded_usd"), "spent": d.get("spent"),
            "remaining": d.get("remaining"), "cap": d.get("cap")}
