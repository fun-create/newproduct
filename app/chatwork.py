#!/usr/bin/env python3
"""
ChatWork への投稿（F-15-6 ／ FR-171〜）。2026-09-24 十文字さんの選択C。

## 守っていること

**トークンをこのアプリに写さない。**他のアプリ（Auto GROWTH・secretary・lpscope）が
既に持っているが、いずれも 0600 で別ユーザー。**読めないのが正しい。**
`config/chatwork.env` に**このアプリ専用のトークン**を置いてもらう（0600・git 管理外）。
**無ければ送らない。**「設定がないので送りません」と画面に出す（黙って落ちない・N-10）。

**コードにトークンを書かない**（lpscope の作法・共通ルール §1）。
**ログにも出さない。**例外メッセージにも混ぜない。

**送る前に、送る文面をそのまま見せる。**ChatWork は取り消せない。
lpscope は「対外送信＝人の明示操作が必須。自動送信しない」を規約にしている。
**同じ部屋へ投げる以上、こちらだけ黙って自動送信しない。**

**Markdown は効かない。**ChatWork 記法（`[info]` `[title]`）で組む。
`**強調**` をそのまま送ると、アスタリスクがそのまま出る。

**名乗らない。**自分のアカウントから出るので「十文字です」は要らない。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
API = "https://api.chatwork.com/v2/rooms/{room}/messages"
TOKEN_FILE = BASE / "config" / "chatwork.env"
TOKEN_KEY = "CHATWORK_API_TOKEN"
TIMEOUT = 20


class NotConfigured(RuntimeError):
    """設定が足りない。**送らない理由を言葉で持つ。**"""


def token_path() -> Path:
    return Path(os.environ.get("NEWPRODUCT_CHATWORK_ENV") or TOKEN_FILE)


def _read_token() -> str:
    p = token_path()
    if not p.is_file():
        raise NotConfigured(
            f"{p} がありません。ChatWork のAPIトークンを、このアプリ専用に発行して"
            f"置いてください（`{TOKEN_KEY}=…` の1行・権限 600・所有者 newproduct）。"
            "**他のアプリのトークンを写さないでください**（どのアプリが投げたのか"
            "分からなくなり、片方を止めると両方止まります）")
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        if k.strip() == TOKEN_KEY:
            v = v.strip().strip('"').strip("'")
            if v:
                return v
    raise NotConfigured(f"{p} に {TOKEN_KEY} の行がありません")


def status(room_id: str | None) -> dict:
    """送れる状態か。**値は返さない。**在る／無いだけ。"""
    p = token_path()
    has_token = False
    why = None
    try:
        _read_token()
        has_token = True
    except NotConfigured as e:
        why = str(e)
    ok = bool(room_id) and has_token
    if not room_id:
        why = ("送り先の部屋が未設定です。設定 `automation.chatwork_room_id` に"
               "ChatWork の部屋IDを入れてください" + (f"／{why}" if why else ""))
    return {"ready": ok, "token_file": str(p), "has_token": has_token,
            "room_id": room_id, "why": None if ok else why}


def post(room_id: str, body: str, *, sender=None) -> dict:
    """1本投げる。`sender` は検査で差し替える（**テストは外へ出さない**）。"""
    if not room_id:
        raise NotConfigured("送り先の部屋が未設定です")
    if not (body or "").strip():
        raise ValueError("本文が空です")
    if sender is not None:
        return sender(room_id, body)
    tok = _read_token()
    req = urllib.request.Request(
        API.format(room=urllib.parse.quote(str(room_id))),
        data=urllib.parse.urlencode({"body": body}).encode("utf-8"),
        headers={"X-ChatWorkToken": tok,
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
            return {"status": r.status, "message_id": (json.loads(raw) or {}).get("message_id")}
    except urllib.error.HTTPError as e:
        # **本文にトークンを混ぜない。**状態コードと ChatWork の返事だけ
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"ChatWork が {e.code} を返しました: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"ChatWork へつながりません: {e.reason}") from None
