#!/usr/bin/env python3
"""
NEW PRODUCT（FUN-CREATE 新商品開発管理）。
内部名 newproduct / 公開 newproduct.fun-create.co.jp。

**このファイルにはロジックを置かない。**振り分けだけ。
業務の数字の作り方は app/ 配下に閉じる（全体設計書 §9）。
ここが太ると、どこで数字が変わったのか追えなくなる。

**第2段（案件・タスク・ゲート）と、第1段のうちアイデア台帳・採点v2まで実装済み。**
第1段の残り（機会カレンダー・年間プランの枠）・原価/調達（第3段）・
発売後評価（第4段）は未実装で、画面に「未実装（第N段）」と出す。
**空欄を黙って0にしない。**

外部からは Caddy 経由の 127.0.0.1:8794。
サービス間API（/api/svc/*）は `svc_ok()` の3条件
（loopback ＋ 転送ヘッダ不在 ＋ Bearer）で守る。
loopback チェックだけでは、Caddy が 127.0.0.1 へ転送するので外から素通りになる。
**Caddy 側でも /api/* を落とす。アプリと Caddy の二重で塞ぐ。**

標準ライブラリのみ（http.server + sqlite3）。
フレームワーク・ORM・テンプレートエンジンを足さない。
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import time
import datetime as _dt
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import auth  # noqa: E402  （/opt/keiei/app/auth.py の複製。_upstream.json 参照）

from app import ai_score as ai_m  # noqa: E402
from app import gate as gate_m   # noqa: E402
from app import idea as idea_m   # noqa: E402
from app import project as project_m  # noqa: E402
from app import seed as seed_m   # noqa: E402
from app import store            # noqa: E402
from app import task as task_m   # noqa: E402

UI_DIR = BASE / "ui"
ASSETS_DIR = UI_DIR / "assets"
CONFIG_DIR = BASE / "config"
DATA_DIR = BASE / "data"

HOST = os.environ.get("NEWPRODUCT_BIND", "127.0.0.1")
PORT = int(os.environ.get("NEWPRODUCT_PORT", "8794"))
PUBLIC_URL = os.environ.get("NEWPRODUCT_PUBLIC_URL",
                            "https://newproduct.fun-create.co.jp")

# 数字の形の約束。**違う版どうしでは数字を出さずに止まる**（全体設計書 §5-5）
CONTRACT_VERSION = "0.1.0"
STARTED_AT = time.strftime("%Y-%m-%d %H:%M:%S")

SVC_TOKEN_PATH = CONFIG_DIR / "svc_token"

# Caddy を通ると必ず付くヘッダ。1つでもあれば「外から来た」とみなす
FORWARD_HEADERS = ("X-Forwarded-Proto", "X-Real-IP", "X-Forwarded-For",
                   "X-Forwarded-Host", "Forwarded")

# ══════════════════════════════════════════════════════════════
# 社内アプリ共通の素材。**拡張子の許可リスト方式**（知らないものは配らない）
#
# **`.css` を application/octet-stream で返すと、`nosniff` のせいで
# ブラウザがスタイルシートの適用を拒む。しかも HTTP は 200 のままなので
# curl では気づけない。**2026-09-20 に Auto GROWTH と LPSCOPE の両方で
# 実際に起き、ヘッダーが素の文字になった（全体設計書 §5-8）。
# → selfcheck.py が**実際に HTTP で取得して Content-Type を検査する**。
#
# 許可リストにしておくと、ディレクトリ跨ぎ（`../`・`%2e%2e%2f`・`..%2f`）も
# 自然に落ちる。`urlparse` は百分率デコードをしないので、`..%2f` のような形も
# 「そんな拡張子は知らない」で 404 になる。
# ══════════════════════════════════════════════════════════════
ASSET_TYPES = {".svg": "image/svg+xml", ".png": "image/png",
               ".ico": "image/x-icon", ".css": "text/css; charset=utf-8",
               ".webmanifest": "application/manifest+json",
               ".js": "text/javascript; charset=utf-8"}


def _asset_version() -> str:
    """`?v=` に入れる版。素材が変わったときだけ変わればよい。

    中身から作る（mtime だと rsync のたびに変わる）。
    ここが変わらないと、ブラウザが古い CSS を1年使い続ける。
    """
    h = hashlib.sha256()
    for p in sorted(list(ASSETS_DIR.glob("*")) + [UI_DIR / "app.js"]):
        if p.is_file():
            h.update(p.name.encode("utf-8"))
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()[:8]


ASSET_V = _asset_version()


def _token_file(path: Path) -> str:
    """無ければ作る（0600）。"""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(18))
    return path.read_text().strip()


def svc_token() -> str:
    return _token_file(SVC_TOKEN_PATH)


def _page(name: str, **subs) -> bytes:
    """ui/ の HTML を読んで差し込む。**テンプレートエンジンは足さない。**

    差し込む値は必ず escape してから渡すこと（呼び出し側の責任）。
    """
    s = (UI_DIR / name).read_text(encoding="utf-8")
    s = s.replace("{V}", ASSET_V)
    for k, v in subs.items():
        s = s.replace("{" + k + "}", v)
    return s.encode("utf-8")


class H(BaseHTTPRequestHandler):
    server_version = "newproduct"
    sys_version = ""

    # ── 下ごしらえ ──────────────────────────────────────
    def log_message(self, fmt, *args):
        """journald へ。**接続元を必ず先頭に置く。**

        ヘッダの値は外から来る。空白を含む値を書かれると1行に偽のログ行を
        仕込めてしまうので、空白を潰して1語にする（keiei server.py と同じ）。
        """
        ip = "-".join((self.client_ip() or "?").split())[:45]
        sys.stderr.write("%s %s %s\n"
                         % (ip, self.path.split("?")[0], fmt % args))

    @property
    def secure(self) -> bool:
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def client_ip(self) -> str:
        return (self.headers.get("X-Real-IP")
                or (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                or self.client_address[0])

    def user(self):
        sid = auth.parse_cookie(self.headers.get("Cookie"))
        _, u = auth.session_of(sid)
        return u

    def svc_ok(self) -> bool:
        """サーバ間API の3点判定（全体設計書 §6）。

        **2番目が要。**loopback チェックだけでは、Caddy が 127.0.0.1 へ
        転送するため外部到達を許す。
        """
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return False
        if any(self.headers.get(h) for h in FORWARD_HEADERS):
            return False
        tok = (self.headers.get("Authorization") or "")
        tok = tok[7:] if tok.lower().startswith("bearer ") else ""
        return bool(tok) and hmac.compare_digest(tok, svc_token())

    def csrf_ok(self) -> bool:
        """画面からの POST。**Origin ＋ Content-Type の2つ**（全体設計書 §6）。

        fetch は同一オリジンからしか任意の Content-Type を付けられず、
        フォーム送信型の CSRF は Origin で落ちる。
        """
        ct = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ct not in ("application/x-www-form-urlencoded",):
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True          # フォーム送信で Origin が無い古い環境は通す
        want = {PUBLIC_URL, f"http://{self.headers.get('Host', '')}",
                f"https://{self.headers.get('Host', '')}"}
        return origin.rstrip("/") in {w.rstrip("/") for w in want}

    # ── 返す ────────────────────────────────────────────
    def send(self, code: int, body: bytes, ctype="text/html; charset=utf-8",
             extra: list[tuple[str, str]] | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # **これが本体。**型を間違えたまま nosniff を付けると、
        # ブラウザは黙って読み込みを拒む。型のほうを検査で担保する（§5-8）
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def sendj(self, code: int, obj):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                  "application/json; charset=utf-8")

    def not_found(self):
        return self.send(404, b"", "text/plain; charset=utf-8")

    def redirect(self, to: str, extra=None):
        self.send(303, b"", "text/plain; charset=utf-8",
                  [("Location", to)] + (extra or []))

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 64 * 1024:
            return {}
        raw = self.rfile.read(n).decode("utf-8", "replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    # ── 入口 ────────────────────────────────────────────
    def do_GET(self):
        self._run("GET")

    def do_HEAD(self):
        self._run("GET")

    def do_POST(self):
        self._run("POST")

    def _run(self, method):
        try:
            self.route(method)
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            try:
                self.send(500, b"internal error", "text/plain; charset=utf-8")
            except Exception:
                pass

    # ── 静的ファイル（許可リスト方式）─────────────────────
    def serve_static(self, f: Path, query: str):
        """**拡張子が許可リストに無ければ 404。**存在しても配らない。

        `..` の正規化には頼らない（`urlparse` は百分率デコードをしないので、
        `%2e%2e%2f` は「拡張子なし」として、ここで落ちる）。
        """
        ct = ASSET_TYPES.get(f.suffix.lower())
        if not ct:
            return self.not_found()
        try:
            real = f.resolve(strict=True)
        except OSError:
            return self.not_found()
        # 二重で塞ぐ。許可リストで落ちるはずだが、置き場の外は必ず断る
        if not str(real).startswith(str(UI_DIR) + os.sep) or not real.is_file():
            return self.not_found()
        cc = ("public, max-age=31536000, immutable"
              if "v=" in (query or "") else "public, max-age=3600")
        return self.send(200, real.read_bytes(), ct, [("Cache-Control", cc)])

    # ── 振り分け ────────────────────────────────────────
    def route(self, method: str):
        u = urllib.parse.urlparse(self.path)
        path = u.path.rstrip("/") or "/"

        # --- 監視。認証なしで返すのはここだけ。中身を漏らさない ---
        if path == "/healthz":
            return self.send(200, b"ok", "text/plain; charset=utf-8")

        # --- 共通意匠の素材。**認証の前に置く。**
        # ログイン画面でもファビコンと帯のロゴと共通CSSが要る。
        # 中身は公開してよい素材だけ。ディレクトリを跨がせない ---
        if path.startswith("/assets/"):
            name = path[len("/assets/"):]
            if not name or "/" in name or "\\" in name or ".." in name:
                return self.not_found()
            return self.serve_static(ASSETS_DIR / name, u.query)
        if path == "/app.js":
            return self.serve_static(UI_DIR / "app.js", u.query)

        # --- API ---
        if path == "/api/health":
            # 監視用。**外からは Caddy が /api/* を落とす。**
            return self.sendj(200, health())
        if path.startswith("/api/svc/"):
            # 既定拒否。3点判定を通ったものだけ
            if not self.svc_ok():
                return self.sendj(403, {"error": "forbidden"})
            return self.svc(method, path)
        if path.startswith("/api/"):
            # **既定拒否。**画面のAPIも利用者が要る
            user = self.user()
            if not user:
                return self.sendj(401, {"error": "ログインしてください"})
            try:
                return self.app_api(method, path, u, user)
            except PermissionError as e:
                return self.sendj(403, {"error": str(e)})
            except LookupError as e:
                return self.sendj(404, {"error": str(e)})
            except ValueError as e:
                return self.sendj(400, {"error": str(e)})

        # --- 画面 ---
        if path == "/login":
            return self.login(method)
        if path == "/logout":
            sid = auth.parse_cookie(self.headers.get("Cookie"))
            auth.logout(sid)
            return self.redirect("/", [("Set-Cookie",
                                        auth.clear_cookie_header(self.secure))])

        # **既定拒否。**ここから先は利用者が要る。
        # `/opt/accounts/roles/newproduct.json` が未登録のあいだは
        # `config/users.json` が空なので、**全員がここで止まる。それで正しい。**
        user = self.user()
        if not user:
            return self.redirect("/login")

        if path == "/":
            # SPA の外枠。画面の切り替えは app.js の hash routing（§5-1）
            return self.send(200, _page(
                "index.html", USER=html.escape(str(user.get("name") or
                                                   user.get("user_id") or ""))))
        return self.not_found()

    # ── ログイン ────────────────────────────────────────
    def login(self, method: str):
        """**初回登録（bootstrap）の口は開けない。**

        `config/users.json` はまだ置いていない（`/opt/accounts/roles/
        newproduct.json` の登録が未了）。ここに「最初の1人が管理者になる」
        画面を置くと、URL を知っている人が誰でも管理者になれる。
        利用者の登録は Calendar の画面から十文字さんが行う。
        **それまでは全員が断られるのが正しい状態。**
        """
        if method == "GET":
            if self.user():
                return self.redirect("/")
            return self.send(200, _page("login.html", ERROR=""))
        if not self.csrf_ok():
            return self.send(400, b"bad request", "text/plain; charset=utf-8")
        d = self.body()
        s, err = auth.login(d.get("user_id", ""), d.get("password", ""),
                            self.client_ip())
        if err:
            return self.send(200, _page("login.html",
                                        ERROR=html.escape(err)))
        return self.redirect("/", [("Set-Cookie",
                                    auth.cookie_header(s["sid"], self.secure))])

    # ── 画面のAPI（第2段）───────────────────────────────
    #
    # **server.py にロジックを置かない。**振り分けと、入力の受け取りだけ。
    # 業務の数字の作り方は app/ 配下に閉じる（§9）。
    def app_api(self, method: str, path: str, u, user):
        uid = str(user.get("user_id") or user.get("name") or "")
        ip = self.client_ip()
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        parts = [p for p in path.split("/") if p][1:]   # "api" を落とす

        if method == "POST" and not self.csrf_ok():
            return self.sendj(400, {"error": "bad request"})

        # /api/me
        if parts == ["me"]:
            return self.sendj(200, {
                "user_id": uid, "name": user.get("name"),
                "app_role": user.get("role"),
                # **アプリ権限と業務ロールは別軸**（§10-2 ②）
                "business_roles": gate_m.roles_of(uid),
            })

        # /api/meta — 画面が使うマスタ
        if parts == ["meta"]:
            return self.sendj(200, {
                "flow_types": project_m.flow_types(),
                "roles": store.rows(store.q("SELECT * FROM role ORDER BY sort")),
                "stages": project_m.STAGES,
                "gates": [{"gate": g["gate"], "name": g["name"],
                           "approver_role": g["approver_role"],
                           "applies_to_flow_types": g["applies_to_flow_types"],
                           "required_items": g["required_items"]}
                          for g in gate_m.defs()],
                "reasons": gate_m.reasons(),
                "sections": project_m.SECTIONS,
                "task_status": task_m.STATUSES,
                # 第1段（アイデア台帳）。**語彙はサーバの1か所に置く**（§4-9 label）
                "idea": {
                    "origins": [{"code": c, "label": lab}
                                for c, lab in idea_m.ORIGINS],
                    "stages": idea_m.STAGES,
                    "demand_cycles": idea_m.DEMAND_CYCLES,
                    "ranks": idea_m.RANKS,
                    "themes": store.rows(store.q(
                        "SELECT id,label,note FROM theme WHERE kind='評価テーマ' "
                        "ORDER BY sort")),
                    "v2_version": idea_m.V2_VERSION,
                    "margin_bands_note": idea_m.MARGIN_BANDS_NOTE,
                },
                "ai_scoring": ai_m.status(),
            })

        if parts == ["dashboard"]:
            return self.sendj(200, task_m.dashboard(uid))

        # /api/projects
        if parts == ["projects"] and method == "GET":
            return self.sendj(200, project_m.listing(qs))
        if parts == ["projects"] and method == "POST":
            d = self.body()
            r = project_m.create(uid, **d)
            store.audit(uid, "project.create", r["id"], d, ip)
            return self.sendj(200, r)

        if len(parts) == 2 and parts[0] == "projects" and method == "GET":
            d = project_m.detail(parts[1], uid)
            if d is None:
                return self.sendj(404, {"error": "案件がありません"})
            return self.sendj(200, d)

        if len(parts) == 3 and parts[0] == "projects" and method == "POST":
            pid, what = parts[1], parts[2]
            d = self.body()
            if what == "section":
                project_m.save_section(pid, d.get("key", ""), d.get("body", ""), uid)
                store.audit(uid, "project.section", pid, {"key": d.get("key")}, ip)
                return self.sendj(200, {"ok": True})
            if what == "check":
                project_m.save_check(pid, d.get("item_key", ""),
                                     d.get("done") in ("1", "true", "on"),
                                     d.get("note", ""), uid)
                store.audit(uid, "project.check", pid, d, ip)
                return self.sendj(200, {"ok": True})
            return self.sendj(404, {"error": "not found"})

        # /api/tasks
        if parts == ["tasks"] and method == "GET":
            return self.sendj(200, task_m.listing(
                qs.get("when", task_m.DEFAULT_WHEN), qs.get("tab", "project"),
                qs.get("role", ""), qs.get("assignee", "")))
        if len(parts) == 4 and parts[0] == "tasks" and parts[3] == "status" \
                and method == "POST":
            d = self.body()
            task_m.set_status(parts[1], int(parts[2]), d.get("status", ""), uid,
                              d.get("ai_used"))
            store.audit(uid, "task.status", f"{parts[1]}:{parts[2]}", d, ip)
            return self.sendj(200, {"ok": True})

        # ── /api/ideas（第1段・F-1）─────────────────────
        # **起票は4項目＋起票経路だけ**（F-1-3）。ここを重くしない
        if parts == ["ideas"] and method == "GET":
            return self.sendj(200, idea_m.listing(qs))
        if parts == ["ideas"] and method == "POST":
            d = self.body()
            r = idea_m.create(uid, **d)
            store.audit(uid, "idea.create", r["id"],
                        {"title": d.get("title"), "origin": d.get("origin")}, ip)
            return self.sendj(200, r)
        # 起票の前に似た案を出す（F-1-12）。**外部APIを使わない**
        if parts == ["ideas", "similar"] and method == "POST":
            d = self.body()
            return self.sendj(200, {"rows": idea_m.similar_to_title(
                d.get("title", ""), exclude_id=d.get("exclude") or None)})
        if parts == ["rubrics"] and method == "GET":
            return self.sendj(200, {"rows": idea_m.rubrics()})
        if parts == ["settings"] and method == "GET":
            return self.sendj(200, {"rows": idea_m.settings(),
                                    "concept_stock": idea_m.concept_stock(),
                                    "ai_scoring": ai_m.status()})
        if len(parts) == 2 and parts[0] == "ideas" and method == "GET":
            d = idea_m.detail(parts[1])
            if d is None:
                return self.sendj(404, {"error": "アイデアがありません"})
            return self.sendj(200, d)
        if len(parts) == 3 and parts[0] == "ideas" and method == "POST":
            iid, what = parts[1], parts[2]
            d = self.body()
            if what == "fields":
                r = idea_m.update_fields(iid, uid, **d)
                store.audit(uid, "idea.fields", iid, d, ip)
                return self.sendj(200, r)
            if what == "score":
                # **v2 の採点。v1 は触らない**（F-1-10。既存データは再採点しない）
                r = idea_m.score_v2(iid, d, uid)
                store.audit(uid, "idea.score", iid,
                            {"rubric_version": idea_m.V2_VERSION}, ip)
                return self.sendj(200, r)
            if what == "ai-score":
                # F-1-11。**既定 off。**予算枠が未取得のあいだは呼ばない
                r = ai_m.run_batch([iid], uid)
                store.audit(uid, "idea.ai_score", iid,
                            {"enabled": r.get("enabled")}, ip)
                return self.sendj(200, r)
            return self.sendj(404, {"error": "not found"})

        # /api/gates
        if parts == ["gates"] and method == "GET":
            return self.sendj(200, gates_board(uid))
        if len(parts) == 3 and parts[0] == "gates" and method == "POST":
            d = self.body()
            r = gate_m.review(parts[1], parts[2], d.get("result", ""), uid,
                              d.get("comment", ""), d.get("reason_code", ""))
            store.audit(uid, "gate.review", f"{parts[1]}:{parts[2]}", d, ip)
            return self.sendj(200, r)

        return self.sendj(404, {"error": "not found"})

    # ── サーバ間API（全体設計書 §6）─────────────────────
    def svc(self, method: str, path: str):
        """いまは枠だけ。中身は各段の実装で埋める。

        **枠のうちに 501 を返しておく。**404 にすると、呼ぶ側が
        「経路が無い」と「まだ無い」を区別できない。
        """
        if path in ("/api/svc/plan", "/api/svc/themes"):
            return self.sendj(501, {"error": "not implemented",
                                    "contract_version": CONTRACT_VERSION})
        if path == "/api/svc/ingest" and method == "POST":
            return self.sendj(501, {"error": "not implemented",
                                    "contract_version": CONTRACT_VERSION})
        return self.sendj(404, {"error": "not found"})


def gates_board(user_id: str) -> dict:
    """ゲート盤（画面設計 3-9）。**自分が判断者のものを先に出す。**"""
    gds = gate_m.defs()
    mine = set(gate_m.roles_of(user_id))
    role_label = {r["code"]: r["label"] for r in
                  store.q("SELECT code,label FROM role")}
    flow_label = {f["code"]: f["label"] for f in project_m.flow_types()}
    rows = []
    for r in store.q("SELECT * FROM project ORDER BY (launch_date IS NULL), "
                     "launch_date, id"):
        p = dict(r)
        b = gate_m.board(p)
        nx = next((g for g in b if g["state"] in ("判定待ち", "差戻し", "保留")), None)
        who = ("／".join(role_label.get(x, x) for x in nx["approver_role"])
               if nx else "—")
        rows.append({
            "id": p["id"], "product": project_m.product_label(p),
            "flow_label": flow_label.get(p["flow_type"] or "", "—"),
            "next_gate": (f"{nx['gate']} {nx['name']}" if nx else "—"),
            "who": who,
            "mine": bool(nx and (mine & set(nx["approver_role"]))),
            "cells": [{"gate": g["gate"], "state": g["state"],
                       "glyph": gate_m.STATES[g["state"]]["glyph"],
                       "word": gate_m.STATES[g["state"]]["word"],
                       "missing_n": len(g["missing"])} for g in b],
        })
    # **自分が判断者のものが最初に来る**
    rows.sort(key=lambda x: (not x["mine"],))
    since = (_dt.date.today() - _dt.timedelta(days=30)).isoformat()
    return {
        "gates": [{"gate": g["gate"], "name": g["name"]} for g in gds],
        "rows": rows,
        "my_roles": sorted(mine),
        "my_role_labels": [role_label.get(x, x) for x in sorted(mine)],
        # **何も動いていないのか、全部通ったのかを区別する**（画面設計 3-9）
        "passed_30d": store.val(
            "SELECT COUNT(*) FROM gate_review WHERE result='通過' "
            "AND approved_at >= ?", (since,), 0),
    }


def departments_sha():
    """`config/departments.json` の sha256。**まだ無ければ null。**

    「未計測」と「0」を区別する（§5-9 の7）。無いことを null で言う。
    """
    p = CONFIG_DIR / "departments.json"
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def health():
    return {
        "app": "newproduct",
        "contract_version": CONTRACT_VERSION,
        "started_at": STARTED_AT,
        "port": PORT,
        "departments_sha": departments_sha(),
        # 各取込の鮮度。**いまは空。**取込を足すたびにここへ1行ずつ増やす。
        # 空の辞書は「取込がまだ1本も無い」という意味で、鮮度切れではない
        "ingest": {},
        # 第2段の実体。**0 と未実装を区別できるように件数で出す**
        "db": db_health(),
        "shared_accounts": auth.shared_on(),
        "degraded": auth.degraded(),
        "users_registered": auth.count(),
    }


def db_path_note() -> str:
    return str(store.db_path())


def db_health() -> dict:
    """DB の実体。**「入っていない」を件数で言う。**"""
    try:
        n = {t: store.val(f"SELECT COUNT(*) FROM {t}", (), 0) for t in
             ("flow_type", "role", "role_member", "task_template", "gate_def",
              "hold_reason", "abort_reason", "project", "task", "work_item",
              "gate_review", "audit")}
    except Exception as e:                      # まだ migrate していない等
        return {"error": str(e)}
    n["flow_type_without_effort_point"] = store.val(
        "SELECT COUNT(*) FROM flow_type WHERE effort_point IS NULL", (), 0)
    n["flow_type_without_template"] = store.val(
        "SELECT COUNT(*) FROM flow_type WHERE has_template=0", (), 0)
    try:
        n["idea"] = idea_m.counts()
    except Exception as e:                  # 第1段の移行前など
        n["idea"] = {"error": str(e)}
    n["stages_implemented"] = "第2段（案件・タスク・ゲート）＋ 第1段のアイデア台帳・採点v2"
    return n


def _startup_notes():
    """**未登録なら未登録と言う。**黙って全員を断らない。

    `config/users.json` が無い状態は、いまは正常（登録がまだ）。
    だが journal に何も出ないと、「ログインできない」の原因を追う人が
    アプリ側を疑って時間を使う。**どちらの状態かを起動時に1行で出す。**
    """
    roles = Path(os.environ.get("ACCOUNTS_DIR", "/opt/accounts")) / "roles" / "newproduct.json"
    n = auth.count()
    if n == 0:
        sys.stderr.write(
            "[auth] このアプリの利用者台帳（config/users.json）は空です。"
            "**全員がログインを断られます。これは正しい状態です。**\n")
        sys.stderr.write(
            f"[auth] 共通ログインの割り当て {roles} "
            f"{'があります' if roles.exists() else 'がまだありません'}。"
            "利用者の登録は Calendar の画面から行ってください\n")
    else:
        sys.stderr.write(f"[auth] 利用者 {n} 名が登録されています\n")
    why = auth.binding_warning()
    if why:
        sys.stderr.write(f"[auth] 共通ログインの設定を確認してください: {why}\n")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # **起動のたびに流す。**何度流しても同じ結果になるように書いてある
    counts = seed_m.run()
    sys.stderr.write(f"[db] {db_path_note()} {counts}\n")
    (DATA_DIR / "export").mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    svc_token()
    _startup_notes()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    sys.stderr.write(f"newproduct listening on {HOST}:{PORT} "
                     f"(contract {CONTRACT_VERSION} / assets v={ASSET_V})\n")
    sys.stderr.flush()
    srv.serve_forever()


if __name__ == "__main__":
    main()
