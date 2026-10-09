#!/usr/bin/env python3
"""
カレンダーアプリ（calfc）の会社休業日を読む（2026-10-09 十文字さん「B」＝カレンダーとつなぐ・ADR-065）。**正はあちら。**

`GET http://127.0.0.1:8790/api/svc/holidays?from=&to=`
`Authorization: Bearer <config/calfc_token>`
→ `{"closed": ["2026-09-02", …], "covered": [{"from","to","name","key"}, …]}`

経営管理（keiei `app/calfc.py`）と同じ約束で読む（あちらの CLAUDE.md「会社休業日は経営管理も読む」）:
- **種別を持ち込まない。**休みの日（closed）だけを受け取る
- **土日を足さない。**covered の中では、返ってきた closed がすべて（休日出勤の土曜を出勤日にした日に食い違わないように）
- **covered の外は「分からない」。**休みなしとは扱わない
- トークンは `config/calfc_token`（newproduct 0600・git 管理外）。**`/api/svc/holidays` だけを通す専用のもの**。
  LPSCOPE 用の全通しトークンや他アプリのトークンは写さない
- `X-Actor` は **ASCII だけ**（日本語を入れると送信前に落ち、相手のログにも残らない・keiei で実際に起きた）

長期連休の月（年間プランの「連休月は多くしない」）は、ここから**連続して休みが LONG_RUN 日以上続く月**として決める。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.environ.get("NEWPRODUCT_CALFC_TOKEN") or os.path.join(BASE, "config", "calfc_token")
API = os.environ.get("NEWPRODUCT_CALFC_API") or "http://127.0.0.1:8790/api/svc/holidays"
TIMEOUT = 5
ACTOR = "newproduct/plan"          # **ASCII のみ**
LONG_RUN = 5                       # 連続5日以上の休み（土日・祝日・会社休業日を含む）を「長期連休」とする
_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_TTL = 300


class NotConnected(RuntimeError):
    """読み口が無い・トークンが無い。**「休日0件」ではない。**"""


def _token() -> str:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            t = f.read().strip()
    except OSError as e:
        raise NotConnected("カレンダーアプリとまだつながっていません（読むための鍵がありません）") from e
    if not t:
        raise NotConnected("カレンダーアプリの読むための鍵が空です")
    return t


def fetch(fr: str, to: str, opener=None) -> dict:
    key = (fr, to)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    url = f"{API}?{urllib.parse.urlencode({'from': fr, 'to': to})}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token()}", "X-Actor": ACTOR})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as r:
            doc = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise NotConnected(f"カレンダーアプリが {e.code} を返しました") from e
    except OSError as e:
        raise NotConnected(f"カレンダーアプリにつながりません: {e}") from e
    except UnicodeError as e:
        raise NotConnected(f"問い合わせを組み立てられません: {e}") from e
    except ValueError as e:
        raise NotConnected(f"カレンダーアプリの返事を読めません: {e}") from e
    _CACHE[key] = (time.time(), doc)
    return doc


def month_stats(fr: dt.date, to: dt.date, doc: dict | None = None) -> dict:
    """[fr, to] の月ごとに {"long": True/False/None, "work": 営業日数 or None}。
    **登録の外にかかる月は営業日数を数えない**（None。土日だけで数えると多く見える）。"""
    doc = doc if doc is not None else fetch(fr.isoformat(), to.isoformat())
    closed = set(doc.get("closed") or [])
    covered = list(doc.get("covered") or [])
    longm = long_holiday_months(fr, to, doc)
    work, full = {}, {}
    d = fr
    while d <= to:
        m = d.strftime("%Y-%m")
        s = d.isoformat()
        inside = any(c.get("from", "") <= s <= c.get("to", "") for c in covered)
        full[m] = full.get(m, True) and inside
        if inside and s not in closed:
            work[m] = work.get(m, 0) + 1
        d += dt.timedelta(days=1)
    return {m: {"long": longm.get(m), "work": (work.get(m, 0) if full[m] else None)} for m in full}


def long_holiday_months(fr: dt.date, to: dt.date, doc: dict | None = None) -> dict:
    """[fr, to] の月ごとに {"YYYY-MM": True/False/None}。None＝登録の外で分からない。

    連続 LONG_RUN 日以上の休み（closed）が1日でもかかる月を True にする。月をまたぐ連休（年末年始）は両方の月。"""
    doc = doc if doc is not None else fetch(fr.isoformat(), to.isoformat())
    closed = set(doc.get("closed") or [])
    covered = list(doc.get("covered") or [])

    def cov(d):
        s = d.isoformat()
        return any(c.get("from", "") <= s <= c.get("to", "") for c in covered)

    out, unknown = {}, set()
    d = fr
    while d <= to:
        m = d.strftime("%Y-%m")
        out.setdefault(m, False)
        if not cov(d):
            unknown.add(m)
        d += dt.timedelta(days=1)
    # 連続する休みの塊を見つける（前後に1日ずつ広げて、範囲の端で切れた連休も拾う）
    d, run = fr - dt.timedelta(days=LONG_RUN), []
    end = to + dt.timedelta(days=LONG_RUN)
    while d <= end + dt.timedelta(days=1):
        if d.isoformat() in closed:
            run.append(d)
        else:
            if len(run) >= LONG_RUN:
                for x in run:
                    m = x.strftime("%Y-%m")
                    if m in out:
                        out[m] = True
            run = []
        d += dt.timedelta(days=1)
    for m in unknown:
        if not out[m]:
            out[m] = None                 # 分からない（登録の外）
    return out
