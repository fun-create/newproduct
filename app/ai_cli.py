#!/usr/bin/env python3
"""
AI（claude）を呼ぶ土台（FR-74・2026-10-09 十文字さんの選択「経営管理と同じ claude ログイン」・ADR-081）。

経営管理（`/opt/keiei/app/ai.py`）と同じ作法にそろえる。向こうで実測して分かっていること:

1. 未ログインでも `claude -p` は **終了コード0** で、JSON の `is_error` にだけ本当のことが書いてある
   → **終了コードを見ない。**`is_error` を見る
2. 資格情報が間違っていると、落ちずに再試行で固まる → **必ず時間で打ち切る**
3. 定額プランのとき `total_cost_usd` は「API で同じことをしたらいくらか」の**目安**で、請求はされない
   → 画面では「目安」と言葉で分ける

ここで足した約束:
- **ツールを渡さない**（`--tools ""`）。AI がサーバのファイルを読んだりコマンドを打ったりしない。渡すのは組み立てた本文だけ
- **Claude Code の既定の指示を使わない**（`--system-prompt` で置き換える）。採点の指示だけを渡す
- **会話を残さない**（`--no-session-persistence`）
- **claude.ai のコネクタ（Google Drive・Notion など）を読み込まない**（`--strict-mcp-config` と
  `ENABLE_CLAUDEAI_MCP_SERVERS=false`）。定額プランでログインすると、そのアカウントのコネクタが自動で付く。
  付いたままだと AI がそれらに触れられるうえ、説明だけで毎回約17万トークンを使う
  （2026-10-09 実測: 「1+1」1回が目安 $0.69 → 切ると $0.0019）
- HOME は `data/aihome`。本番は `ProtectSystem=strict` で、書けるのは data/ logs/ config/ だけ
  （`/opt/newproduct` を HOME にすると read-only で落ちる。手で `sudo -u` すると制限がかからず通るので気づけない）

ログイン（1回だけ・十文字さんがサーバで行う。ブラウザが要る）:

    sudo -u newproduct HOME=/opt/newproduct/data/aihome claude
    → /login
"""
from __future__ import annotations

import json
import os
import re
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_BIN = os.environ.get("NEWPRODUCT_CLAUDE_BIN", "/usr/bin/claude")
AI_HOME = os.environ.get("NEWPRODUCT_AI_HOME", os.path.join(BASE, "data", "aihome"))
LOGIN_FILE = os.path.join(AI_HOME, ".claude", ".credentials.json")
# 日々の定型（採点の案出し）なので Sonnet（CLAUDE.md「日次の定例タスクは Sonnet」）。
# 実際に答えたモデルの正式名は返事から取って残す
DEFAULT_MODEL = "sonnet"
TIMEOUT_SEC = int(os.environ.get("NEWPRODUCT_AI_TIMEOUT", "300"))
LOGIN_HOW = f"sudo -u newproduct HOME={AI_HOME} claude   →  /login"


def credential() -> dict:
    """呼べる状態か。**呼ばずに調べる**（調べるだけで使わない）。"""
    if not os.path.exists(CLAUDE_BIN):
        return {"ok": False, "kind": "no_cli", "label": "Claude Code が入っていません",
                "why": f"{CLAUDE_BIN} がありません"}
    if os.path.exists(LOGIN_FILE):
        return {"ok": True, "kind": "login", "label": "定額プランのログイン", "why": ""}
    return {"ok": False, "kind": "none", "label": "まだログインしていません",
            "why": "AI を呼ぶ資格情報がこのアプリにありません。サーバで1回だけログインすると動きます",
            "how": LOGIN_HOW, "who": "十文字さん（ブラウザが要ります）"}


def writable() -> dict:
    try:
        os.makedirs(AI_HOME, exist_ok=True)
    except OSError as e:
        return {"ok": False, "why": f"{AI_HOME} を作れません（{type(e).__name__}）"}
    if not os.access(AI_HOME, os.W_OK):
        return {"ok": False, "why": f"{AI_HOME} へ書けません"}
    return {"ok": True, "why": ""}


def preflight() -> dict:
    """動くか。**「入っていない」「ログインしていない」「書けない」を分ける。**"""
    cred, wr = credential(), writable()
    ng = []
    if not cred["ok"]:
        ng.append({"key": "cred", "label": cred["label"], "why": cred["why"],
                   "how": cred.get("how"), "who": cred.get("who")})
    if not wr["ok"]:
        ng.append({"key": "write", "label": "書き込み先", "why": wr["why"], "who": "サーバの運用者"})
    return {"ok": not ng, "kind": cred["kind"], "ng": ng}


def cost_is_real() -> bool:
    """請求される額か。**定額プランのログインでは目安**（請求されない）。"""
    return False if credential()["kind"] == "login" else True


def answered_by(usage: dict, asked: str) -> str:
    """実際に答えたモデルの正式名。claude は裏で補助のモデル（Haiku）も使うので、**頼んだ系統のものを選ぶ。**"""
    keys = list(usage.keys())
    hit = [k for k in keys if asked.lower() in (str((usage[k] or {}).get("canonicalModel") or k)).lower()]
    if len(hit) == 1:
        return hit[0]
    if len(keys) == 1:
        return keys[0]
    return ",".join(hit or keys) or asked


URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]]+")
WEB_TOOLS = ("WebSearch", "WebFetch")
WEB_TIMEOUT_SEC = int(os.environ.get("NEWPRODUCT_AI_WEB_TIMEOUT", "600"))


def _trace(lines: list[dict]) -> dict:
    """道具の使い方の記録。**検索した言葉・開いた URL・検索結果に出た URL** を全部拾う（後から見られるように）。"""
    calls, opened, seen = [], [], []
    for d in lines:
        msg = d.get("message") or {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for c in content:
            if d.get("type") == "assistant" and c.get("type") == "tool_use":
                inp = c.get("input") or {}
                if c.get("name") == "WebSearch":
                    calls.append({"tool": "検索", "what": str(inp.get("query") or "")[:300]})
                elif c.get("name") == "WebFetch":
                    u = str(inp.get("url") or "")[:500]
                    calls.append({"tool": "ページを開く", "what": u})
                    opened.append(u)
                else:
                    calls.append({"tool": str(c.get("name")), "what": json.dumps(inp, ensure_ascii=False)[:300]})
            elif d.get("type") == "user" and c.get("type") == "tool_result":
                body = c.get("content")
                body = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
                seen += [u.rstrip(".,;:") for u in URL_RE.findall(body)]
    return {"calls": calls, "opened": list(dict.fromkeys(opened)), "seen": list(dict.fromkeys(seen + opened))[:300]}


def ask(prompt: str, system: str, *, model: str | None = None, timeout: int | None = None,
        runner=None, web: bool = False) -> dict:
    """`claude -p` を1回。返り値は必ず
    {"ok", "text", "error", "error_kind", "cost_usd", "model", "duration_ms"}（web のときは "trace" も）。

    `web=True` のときだけ、検索（WebSearch）とページの読み取り（WebFetch）を渡す（2026-10-09 十文字さんの選択
    「検索＋どのページでも読む」・ADR-088）。**それ以外の道具は渡さない**（ファイル・コマンド・コネクタは使えない）。
    道具の使い方は stream-json で受け取り、検索した言葉・開いた URL を全部返す（呼んだ側が記録して画面に出す）。

    `error_kind` が `env`（資格情報・書き込み・時間切れ・形の崩れ）なら**環境側の失敗**で、
    呼んだ側はその回を打ち切る（入力の不備とは分ける・HUB rules/apps-common.md §2）。
    """
    mdl = model or DEFAULT_MODEL
    lim = timeout or (WEB_TIMEOUT_SEC if web else TIMEOUT_SEC)

    def bad(kind, msg, cost=0.0, ms=0):
        return {"ok": False, "text": "", "error": msg, "error_kind": kind,
                "cost_usd": cost, "model": mdl, "duration_ms": ms}

    if runner is None:
        pre = preflight()
        if not pre["ok"]:
            return bad("env", pre["ng"][0]["why"])
    cmd = [CLAUDE_BIN, "-p", prompt, "--model", mdl, "--system-prompt", system,
           "--no-session-persistence", "--strict-mcp-config"]
    if web:
        cmd += ["--output-format", "stream-json", "--verbose", "--tools", ",".join(WEB_TOOLS),
                "--allowedTools", *WEB_TOOLS]
    else:
        cmd += ["--output-format", "json", "--tools", ""]
    env = {"HOME": AI_HOME, "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "ja_JP.UTF-8",
           "ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
    try:
        r = (runner or subprocess.run)(cmd, capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=lim, env=env, cwd=AI_HOME)
    except subprocess.TimeoutExpired:
        return bad("env", f"{lim}秒たっても返りませんでした。資格情報が正しいか確かめてください", ms=lim * 1000)
    except FileNotFoundError:
        return bad("env", f"{CLAUDE_BIN} が見つかりません")
    raw = (r.stdout or "").strip()
    if not raw:
        return bad("env", (r.stderr or "").strip()[:300] or "何も返りませんでした")
    d, parsed = None, []
    for line in raw.splitlines():                       # 結果の JSON は1行。後ろに別の記録が付くことがある
        try:
            x = json.loads(line)
        except ValueError:
            continue
        if not isinstance(x, dict):
            continue
        parsed.append(x)
        if d is None and (x.get("type") == "result" or ("type" not in x and ("result" in x or "is_error" in x))):
            d = x
    if d is None:
        return bad("env", f"返事が JSON ではありません（末尾200字）: {raw[-200:]}")
    text = str(d.get("result") or "")
    cost = float(d.get("total_cost_usd") or 0.0)
    ms = int(d.get("duration_ms") or 0)
    real_model = answered_by(d.get("modelUsage") or {}, mdl)
    if d.get("is_error"):                              # 終了コードは見ない（未ログインでも 0）
        return {**bad("env", text[:300] or "AI が失敗を返しました", cost, ms), "model": real_model}
    if not text.strip():
        return {**bad("env", "答えが空で返りました", cost, ms), "model": real_model}
    out = {"ok": True, "text": text, "error": "", "error_kind": "", "cost_usd": cost,
           "model": real_model, "duration_ms": ms}
    if web:
        out["trace"] = _trace(parsed)
    return out
