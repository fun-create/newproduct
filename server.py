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
from app import ai_name as ai_name_m  # noqa: E402
from app import ai_draft as ai_draft_m  # noqa: E402
from app import ai_web as ai_web_m  # noqa: E402
from app import schedule as sched_m  # noqa: E402
from app import automation as auto_m  # noqa: E402
from app import gate as gate_m   # noqa: E402
from app import idea as idea_m   # noqa: E402
from app import plan as plan_m    # noqa: E402
from app import project as project_m  # noqa: E402
from app import seed as seed_m   # noqa: E402
from app import sales as sales_m  # noqa: E402
from app import cost as cost_m    # noqa: E402
from app import compat as compat_m  # noqa: E402
from app import competitor as comp_m  # noqa: E402
from app import prefs as prefs_m  # noqa: E402
from app import admin as admin_m  # noqa: E402
from app import events as events_m  # noqa: E402
from app import templates as tpl_m  # noqa: E402
from app import idea_import as imp_m  # noqa: E402
from app import simulate as sim_m  # noqa: E402
from app import mallreq as mall_m  # noqa: E402
from app import handoff as handoff_m  # noqa: E402
from app import target as target_m  # noqa: E402
from app import abc as abc_m       # noqa: E402
from app import fctr as fctr_m     # noqa: E402
from app import opportunity as opp_m  # noqa: E402
from app import lpreq as lpreq_m  # noqa: E402
from app import report as report_m  # noqa: E402
from app import seisan as seisan_m  # noqa: E402
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


CONNECTION_KEYS = admin_m.CONNECTION_KEYS


def _root_attrs(user: dict) -> str:
    """`<html>` に付ける表示の属性（ADR-057）。**最初の描画から**その人の見え方にする（後から JS で替えるとちらつく）。

    値は許可リストのものだけ（prefs.get と auth.theme_of が倒す）。`auto` は属性を付けない
    （端末の設定に従う。app-shell.css の約束）。念のため escape もする。"""
    uid = str(user.get("user_id") or "")
    pf = prefs_m.get(uid)
    out = []
    t = auth.theme_of(user)
    if t in ("light", "dark"):
        out.append(f' data-theme="{t}"')
    out.append(f' data-np-font="{html.escape(pf["font"])}"')
    out.append(f' data-np-density="{html.escape(pf["density"])}"')
    out.append(f' data-np-start="{html.escape(pf["start"])}"')
    return "".join(out)


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

    def body(self, limit: int = 64 * 1024) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            # **黙って空にしない。**空にすると「内容がありません」と別の理由で断られて、本当の理由が見えない
            raise ValueError(f"送る内容が大きすぎます（{n // 1024} KB。上限 {limit // 1024} KB）。分けて送ってください")
        if n <= 0:
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
                                                   user.get("user_id") or "")),
                ROOTATTR=_root_attrs(user)))
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
        try:
            d = self.body()
        except ValueError:
            d = {}                       # 大きすぎる送信は、今までどおり空として断る（ログインで 500 を出さない）
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
                # 表示（ADR-057）。配色は共通部品、ほかはこのアプリの設定
                "theme": auth.theme_of(user), "themes": [["auto", "端末に合わせる"], ["light", "ライト"], ["dark", "ダーク"]],
                "prefs": prefs_m.get(uid), "pref_options": prefs_m.options(),
            })
        # 自分の表示を変える。**本人の見た目だけ**なので誰でも変えられる（管理者専用にしない）
        if parts == ["me", "display"] and method == "POST":
            d = self.body()
            out = {"ok": True}
            if "theme" in d:
                if d["theme"] not in auth.THEMES:
                    return self.sendj(400, {"error": "配色の指定が不正です"})
                rec, err = auth.set_theme(uid, d["theme"])
                if err:
                    return self.sendj(409, {"error": err})
                out["theme"] = d["theme"]
            rest = {k: v for k, v in d.items() if k in prefs_m.PREFS}
            if rest:
                out["prefs"] = prefs_m.save(uid, rest)["prefs"]
            elif "theme" not in d:
                return self.sendj(400, {"error": "変える項目がありません"})
            store.audit(uid, "me.display", uid, {k: d.get(k) for k in ["theme", *prefs_m.PREFS] if k in d}, ip)
            return self.sendj(200, out)
        # 操作マニュアル（docs/操作マニュアル.md）。**画面と同じ場所で直す**ため、文書はリポジトリに置く
        if parts == ["manual"] and method == "GET":
            mp = BASE / "docs" / "操作マニュアル.md"
            try:
                return self.sendj(200, {"body_md": mp.read_text(encoding="utf-8")})
            except OSError:
                return self.sendj(404, {"error": "操作マニュアルがまだありません"})

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
                "ai_scoring": ai_m.status(uid),
                # 自動化依頼（F-15）。**質問は固定。サーバの1か所に置く**
                "automation": {"questions": auto_m.questions(),
                               "stages": auto_m.STAGES},
                # 年間プラン（第1段の残り・F-3）
                "plan": {
                    "kinds": plan_m.kinds(),
                    "states": plan_m.STATES,
                    "rules": plan_m.RULES,
                },
            })

        if parts == ["dashboard"]:
            return self.sendj(200, task_m.dashboard(uid))

        # 機会カレンダー（FR-78〜81）
        # 販売計画シミュレーション（F-14・ADR-072）。見るのは全員・確定は社長
        if parts == ["simulate"] and method == "GET":
            def _f(k):
                v = (qs.get(k) or "").replace(",", "").strip()
                if not v:
                    return None
                try:
                    return float(v)
                except ValueError:
                    raise ValueError(f"{k} は数字で入れてください") from None
            fy = int(qs.get("fy") or sim_m.current_fy())
            cands = [int(x) for x in (qs.get("cands") or "").replace("、", ",").split(",") if x.strip().isdigit()]
            depts = [x for x in (qs.get("depts") or "").split(",") if x.strip()]
            r = sim_m.compare(fy, _f("target"), cands or None, _f("ep"), _f("share"), depts or None)
            r["can_confirm"] = sim_m.can_confirm(uid)
            r["versions"] = sim_m.versions(fy)
            r["revision"] = sim_m.revision_check(fy)          # FR-132。経営管理の計画が改訂されたか
            r["handoff"] = handoff_m.latest(fy)                 # FR-124。経営管理へ渡した中身と状態
            return self.sendj(200, r)
        if parts == ["simulate", "resend"] and method == "POST":
            if not sim_m.can_confirm(uid):
                return self.sendj(403, {"error": "経営管理へ送り直せるのは、社長の業務ロールの人です"})
            return self.sendj(200, handoff_m.send(int(self.body().get("id") or 0)))
        if parts == ["simulate", "confirm"] and method == "POST":
            d = self.body()
            tgt = (d.get("target") or "").replace(",", "").strip()
            ep = (d.get("ep") or "").strip()
            return self.sendj(200, sim_m.confirm(int(d.get("fy") or sim_m.current_fy()), int(d.get("n") or 0),
                                                 float(tgt) if tgt else None, float(ep) if ep else None,
                                                 d.get("note", ""), uid, ip, d.get("basis", ""),
                                                 {"version": d.get("plan_version") or None,
                                                  "share_pct": float(d["share"]) if d.get("share") else None,
                                                  "depts": [x for x in (d.get("depts") or "").split(",") if x] or None}))
        if parts == ["opportunities"] and method == "GET":
            return self.sendj(200, opp_m.calendar())
        if parts == ["opportunities", "slot"] and method == "POST":
            d = self.body()
            r = opp_m.to_slot(d.get("theme_id", ""), uid)
            store.audit(uid, "plan.slot.from_opportunity", r["id"], {"theme_id": d.get("theme_id")}, ip)
            return self.sendj(200, r)

        # FCTR の週次トレンド（FR-135〜137）
        if parts == ["fctr"] and method == "GET":
            pf = prefs_m.get(uid)                         # 見せ方は利用者ごと（ADR-057）
            r = fctr_m.board(top_n=int(pf["trend_top"]), order=pf["trend_order"],
                             faded=pf["trend_faded"] == "show")
            r["can_score"] = fctr_m.can_score(uid)
            return self.sendj(200, r)
        if parts == ["fctr", "self"] and method == "POST":
            d = self.body()
            r = fctr_m.save_self(d.get("theme_id", ""), d, uid)
            store.audit(uid, "fctr.self_score", d.get("theme_id"), {k: d.get(k) for k in ("fit", "ops", "speed", "profit")}, ip)
            return self.sendj(200, r)
        if parts == ["fctr", "idea"] and method == "POST":
            d = self.body()
            r = fctr_m.to_idea(d.get("theme_id", ""), d.get("segment", ""), uid)
            store.audit(uid, "fctr.to_idea", r["id"], {"theme_id": d.get("theme_id")}, ip)
            return self.sendj(200, r)

        # 商品ABC分析（2026-10-02・ADR-050）。見るだけ
        if parts == ["abc"] and method == "GET":
            from app import salesfeed as sf
            try:
                return self.sendj(200, abc_m.analyze(
                    qs.get("site") or "goods", qs.get("from") or "", qs.get("to") or "",
                    qs.get("compare") or "yoy", qs.get("cfrom") or "", qs.get("cto") or ""))
            except sf.Unavailable as e:
                return self.sendj(409, {"error": str(e)})

        # 原価・調達の一覧（案件をまたぐ）。見るだけ
        if parts == ["cost"] and method == "GET":
            return self.sendj(200, {"rows": cost_m.board()})

        # 売上実績（2026-10-01 決定）。**見るだけ。**利用者全員（十文字さん「利用者全員」）
        if parts == ["sales", "new-products"] and method == "GET":
            return self.sendj(200, sales_m.new_product_summary())
        if parts == ["sales"] and method == "GET":
            return self.sendj(200, sales_m.overview(qs.get("site") or "all",
                                                    qs.get("month") or None))

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
            d["ai_names"] = ai_name_m.proposals_of(parts[1])          # 商品名の案（ADR-082）
            d["schedule"] = sched_m.summary(parts[1])                  # タスクの目安（ADR-084）
            d["ai_drafts"] = ai_draft_m.proposals_of(parts[1])        # LP依頼書・競合調査の下書き（ADR-087）
            d["ai_web"] = ai_web_m.of("project", parts[1], "competitor")  # ウェブで調べた競合の候補（ADR-088）
            return self.sendj(200, d)

        # LP依頼書・競合調査の下書き（FR-149・ADR-087）。**AI は案を出すだけ。**人が直した文面を欄に足す
        if (len(parts) == 4 and parts[0] == "projects" and parts[2] == "ai-drafts" and method == "POST"):
            r = ai_draft_m.start_run(parts[1], parts[3], uid)
            store.audit(uid, "project.ai_draft", parts[1], {"kind": parts[3], **r}, ip)
            return self.sendj(200, r)
        if (len(parts) == 5 and parts[0] == "projects" and parts[2] == "ai-draft-proposals"
                and parts[4] in ("adopt", "reject") and method == "POST"):
            d = self.body()
            pid = int(parts[3])
            r = (ai_draft_m.adopt(pid, uid, d.get("text", "")) if parts[4] == "adopt"
                 else ai_draft_m.reject(pid, uid, d.get("note", "")))
            store.audit(uid, f"project.ai_draft.{parts[4]}", parts[1], {"proposal": pid}, ip)
            return self.sendj(200, r)

        # 商品名の案出し（FR-149・ADR-082）。**AI は案を出すだけ。**選んだ名前だけを F.name に足す
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "ai-names" and method == "POST":
            r = ai_name_m.start_run(parts[1], uid)
            store.audit(uid, "project.ai_names", parts[1], r, ip)
            return self.sendj(200, r)
        if (len(parts) == 5 and parts[0] == "projects" and parts[2] == "ai-names"
                and parts[4] in ("adopt", "reject") and method == "POST"):
            d = self.body()
            pid = int(parts[3])
            if parts[4] == "adopt":
                picks = d.get("picks")
                if isinstance(picks, str):
                    picks = [int(x) for x in picks.split(",") if x.strip()]
                r = ai_name_m.adopt(pid, uid, picks or [])
            else:
                r = ai_name_m.reject(pid, uid, d.get("note", ""))
            store.audit(uid, f"project.ai_names.{parts[4]}", parts[1], {"proposal": pid, **d}, ip)
            return self.sendj(200, r)

        # 対応確認（FR-102）。ひな形は商品開発部が作る（項目の中身は発明しない）
        if parts == ["compat", "templates"] and method == "POST":
            d = self.body()
            r = compat_m.save_template(d, uid)
            store.audit(uid, "compat.template", r["name"], {"items": len(r["items"])}, ip)
            return self.sendj(200, r)
        if len(parts) == 3 and parts[0] == "projects" and parts[2].startswith("compat"):
            pid, what = parts[1], parts[2]
            if what == "compat" and method == "GET":
                return self.sendj(200, compat_m.overview(pid, uid))
            if method == "POST" and what in ("compat-apply", "compat-result"):
                d = self.body()
                r = (compat_m.apply(pid, d.get("template_id"), uid) if what == "compat-apply"
                     else compat_m.set_result(pid, d.get("id"), d.get("result", ""), d.get("note", ""), uid))
                store.audit(uid, "project." + what, pid,
                            {k: d.get(k) for k in ("template_id", "id", "result")}, ip)
                return self.sendj(200, r)
            return self.sendj(404, {"error": "not found"})

        # 競合調査（FR-141〜143）。**出典と確認日が必須**。推計はレビュー率が決まるまで出さない
        if len(parts) == 3 and parts[0] == "projects" and parts[2].startswith("competitor"):
            pid, what = parts[1], parts[2]
            if what == "competitor" and method == "GET":
                return self.sendj(200, comp_m.overview(pid, uid))
            if method == "POST" and what in ("competitor", "competitor-delete"):
                d = self.body()
                r = comp_m.save(pid, d, uid) if what == "competitor" else comp_m.delete(pid, d.get("id"), uid)
                store.audit(uid, "project." + what, pid, {k: d.get(k) for k in ("id", "shop", "url", "checked_on")}, ip)
                return self.sendj(200, r)
            return self.sendj(404, {"error": "not found"})
        # アプリ全体の設定（ADR-059）。**根拠が必須**・前後の値を監査に残す（admin.set_value の中で）
        if parts == ["settings", "value"] and method == "POST":
            d = self.body()
            return self.sendj(200, admin_m.set_value(d.get("key", ""), d.get("value"), d.get("reason", ""),
                                                     uid, user.get("role"), ip))
        # 年間・ライフイベント（ADR-060）。**消さない**（使わない にする）。ID は変えない
        if parts == ["settings", "events"] and method == "GET":
            r = events_m.listing()
            r["can_edit"] = events_m.can_edit(uid)
            return self.sendj(200, r)
        if parts == ["settings", "events"] and method == "POST":
            return self.sendj(200, events_m.save(self.body(), uid, ip))
        if parts == ["settings", "events", "active"] and method == "POST":
            d = self.body()
            return self.sendj(200, events_m.set_active(d.get("id", ""), d.get("on") in ("1", "true", "on"), uid, ip))
        # 標準タスクのひな形と係数（ADR-061）。版1は種データ専用・画面の版は2から・起こし済みの案件は変えない
        if parts == ["settings", "templates"] and method == "GET":
            r = tpl_m.overview()
            r["can_edit"] = tpl_m.can_edit(uid)
            return self.sendj(200, r)
        if len(parts) == 3 and parts[:2] == ["settings", "templates"] and method == "GET":
            r = tpl_m.detail(parts[2])
            r["can_edit"] = tpl_m.can_edit(uid)
            return self.sendj(200, r)
        if len(parts) == 4 and parts[:2] == ["settings", "templates"] and method == "POST":
            code, act, d = parts[2], parts[3], self.body()
            ops = {
                "draft": lambda: tpl_m.new_draft(code, uid, ip),
                "row": lambda: tpl_m.save_row(code, d, uid),
                "row-delete": lambda: tpl_m.delete_row(code, d.get("id"), uid),
                "row-move": lambda: tpl_m.move_row(code, d.get("id"), d.get("up") == "1", uid),
                "discard": lambda: tpl_m.discard(code, uid, ip),
                "activate": lambda: tpl_m.activate(code, uid, d.get("reason", ""), ip),
                "revert": lambda: tpl_m.revert(code, uid, d.get("reason", ""), ip),
                "effort": lambda: tpl_m.set_effort(code, d.get("value"), d.get("reason", ""), uid, ip),
            }
            if act not in ops:
                return self.sendj(404, {"error": "not found"})
            return self.sendj(200, ops[act]())
        # アイデアの一括登録（ADR-062）。確認は何も保存しない。登録は確認した札と一致するものだけ
        if parts == ["settings", "ideas-import"] and method == "GET":
            return self.sendj(200, {"batches": imp_m.batches(), "can_edit": imp_m.can_edit(uid),
                                    "origins": [{"code": c, "label": l} for c, l in idea_m.ORIGINS],
                                    "max_rows": imp_m.MAX_ROWS})
        if parts == ["settings", "ideas-import", "preview"] and method == "POST":
            d = self.body(limit=1024 * 1024)
            return self.sendj(200, imp_m.preview(d.get("text", ""), d.get("default_origin", "")))
        if parts == ["settings", "ideas-import", "register"] and method == "POST":
            d = self.body(limit=1024 * 1024)
            return self.sendj(200, imp_m.register(d.get("text", ""), d.get("default_origin", ""), d.get("token", ""),
                                                  [x for x in (d.get("exclude") or "").split(",") if x], uid, ip))
        if parts == ["settings", "ideas-import", "undo"] and method == "POST":
            return self.sendj(200, imp_m.undo(self.body().get("token", ""), uid, ip))
        # 業務ロールの付け外し（アプリ権限 admin・十文字さんの選択）
        if parts == ["settings", "role"] and method == "POST":
            d = self.body()
            return self.sendj(200, admin_m.set_role(d.get("user_id", ""), d.get("role", ""),
                                                    d.get("on") in ("1", "true", "on"), uid, user.get("role"), ip))

        # 年間目標（FR-109/110・FR-106）。**3方式と根拠が必須**。未設定は「目標未設定」
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "target":
            pid = parts[1]
            if method == "POST":
                d = self.body()
                r = target_m.save(pid, d, uid)
                store.audit(uid, "project.target", pid, {"method": d.get("method"),
                                                         "annual_yen": d.get("annual_yen")}, ip)
                return self.sendj(200, r)
            ps = sales_m.project_sales(pid)
            actual = (sum(x["total"] for x in ps.get("sites") or [] if x["total"] is not None)
                      if not ps.get("why") else None)
            p = store.one("SELECT launch_date FROM project WHERE id=?", (pid,))
            r = target_m.progress(pid, p["launch_date"] if p else None, actual)
            # FR-177。確定した販売計画の1本あたりを「逆算法」の候補として出す。実績の分布は目安として添える
            r["plan_ref"] = sim_m.plan_ref(p["launch_date"] if p else None)
            # FR-132。逆算法の目標で、元の販売計画が経営管理の改訂で変わっていたら差を出す（未発売の案件だけ）
            pr = r["plan_ref"]
            if pr and not pr.get("none") and r.get("method") == "逆算法":
                rv = sim_m.revision_check(pr["fy"])
                if rv and rv.get("changed"):
                    r["plan_revision"] = {k: rv.get(k) for k in ("was", "now", "new_each", "old_each", "why")}
            dd = sim_m.distribution()
            r["dist"] = {k: dd.get(k) for k in ("n", "median", "top80")}
            return self.sendj(200, r)

        # 月次レポート（FR-116）。毎月2日に timer が作る。画面からも作り直せる
        if parts == ["reports"] and method == "GET":
            return self.sendj(200, {"rows": report_m.listing(), "default": report_m.prev_month()})
        if len(parts) == 2 and parts[0] == "reports" and method == "GET":
            r = report_m.get(parts[1])
            if r is None:
                return self.sendj(404, {"error": f"{parts[1]} の月次レポートはまだありません"})
            return self.sendj(200, r)
        if len(parts) == 3 and parts[0] == "reports" and parts[2] == "build" and method == "POST":
            import re as _re
            if not _re.match(r"^\d{4}-(0[1-9]|1[0-2])$", parts[1]):
                return self.sendj(400, {"error": "月は YYYY-MM で"})
            r = report_m.save(parts[1], uid)
            store.audit(uid, "report.build", parts[1], {"problems": len(r["problems"])}, ip)
            return self.sendj(200, r)

        # 4モールの出品依頼（FR-119・ADR-075）。**作るだけ。送らない**
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "mall-request" and method == "GET":
            return self.sendj(200, mall_m.build(parts[1]))

        # LP依頼書（FR-118）。**作るだけ。送らない**
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "lp-request" and method == "GET":
            return self.sendj(200, lpreq_m.build(parts[1]))

        # 新商品の発売後の売上（FR-183）。見るだけ
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "sales" and method == "GET":
            return self.sendj(200, sales_m.project_sales(parts[1]))

        # ── 原価・調達（第3段・ADR-047）。**マスタは持たない。**案件ごとの候補と試算だけ ──
        if len(parts) == 3 and parts[0] == "projects" and parts[2].startswith("cost"):
            pid, what = parts[1], parts[2]
            if what == "cost" and method == "GET":
                return self.sendj(200, cost_m.overview(pid, uid))
            if what == "cost-seisan-vocab" and method == "GET":
                return self.sendj(200, cost_m.seisan_vocab())
            if method != "POST":
                return self.sendj(404, {"error": "not found"})
            d = self.body()
            if what == "cost-candidate":
                r = cost_m.save_candidate(pid, d, uid)
            elif what == "cost-adopt":
                r = cost_m.adopt(pid, d.get("id"), d.get("adopted") in ("1", "true", "on"),
                                 d.get("reason", ""), uid)
            elif what == "cost-version":
                r = cost_m.new_version(pid, d, uid)
            elif what == "cost-version-update":
                r = cost_m.update_version(pid, d.get("version_id"), d, uid)
            elif what == "cost-line":
                r = cost_m.save_line(pid, d.get("version_id"), d, uid)
            elif what == "cost-seisan":
                try:
                    r = cost_m.register_candidate(pid, d.get("id"), d, uid)
                except seisan_m.NotConfigured as e:
                    return self.sendj(409, {"error": str(e)})
            elif what == "cost-line-delete":
                r = cost_m.delete_line(pid, d.get("version_id"), d.get("id"), uid)
            else:
                return self.sendj(404, {"error": "not found"})
            store.audit(uid, "project." + what, pid,
                        {k: d.get(k) for k in ("id", "version_id", "kind", "part", "adopted",
                                               "code", "outsourcer_id", "target_kind")}, ip)
            return self.sendj(200, r)

        # ── seisan への商品登録（第3段・FR-103／FR-60・ADR-043）──
        # **マスタは持たない。正は seisan。**ここは下書きと共通商品コードだけ
        if len(parts) == 3 and parts[0] == "projects" and parts[2].startswith("seisan"):
            pid, what = parts[1], parts[2]
            try:
                if what == "seisan" and method == "GET":
                    return self.sendj(200, seisan_m.overview(pid, uid))
                if what == "seisan-similar" and method == "GET":
                    return self.sendj(200, {"rows": seisan_m.similar(
                        qs.get("cat1", ""), qs.get("cat2", ""), qs.get("cat3", ""))})
                if method != "POST":
                    return self.sendj(404, {"error": "not found"})
                d = self.body()
                vid = d.get("variant_id") or None
                if what == "seisan-draft":
                    r = seisan_m.save_draft(pid, vid, d, uid)
                elif what == "seisan-register":
                    r = seisan_m.register(pid, vid, uid)
                elif what == "seisan-code":
                    r = seisan_m.record_code(pid, vid, d.get("code", ""), uid)
                elif what == "seisan-reset":
                    r = seisan_m.reset(pid, vid, uid)
                else:
                    return self.sendj(404, {"error": "not found"})
                # 監査には**商品名を残さない**（正は seisan。ここに写しを作らない）
                store.audit(uid, "project." + what, pid,
                            {"variant_id": vid, "code": d.get("code")}, ip)
                return self.sendj(200, r)
            except seisan_m.NotConfigured as e:
                return self.sendj(409, {"error": str(e)})
            except seisan_m.Refused as e:
                return self.sendj(409, {"error": "CIP が断りました: " + str(e)})

        if len(parts) == 3 and parts[0] == "projects" and method == "POST":
            pid, what = parts[1], parts[2]
            d = self.body()
            if what == "ai-web":
                r = ai_web_m.start_run("competitor", pid, uid)
                store.audit(uid, "project.ai_web", pid, r, ip)
                return self.sendj(200, r)
            if what in ("schedule", "schedule-adopt"):
                # タスクの目安（FR-41・ADR-084）。**目安は期限ではない。**期限にするのは人が押したときだけ
                pr = store.one("SELECT source_of_truth FROM project WHERE id=?", (pid,))
                if pr is None:
                    raise LookupError("案件がありません")
                if pr["source_of_truth"] != "app":
                    raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
                r = sched_m.refresh(pid, uid) if what == "schedule" else sched_m.adopt(pid, uid)
                store.audit(uid, "project." + what, pid, r, ip)
                return self.sendj(200, r)
            if what == "section":
                project_m.save_section(pid, d.get("key", ""), d.get("body", ""), uid)
                store.audit(uid, "project.section", pid, {"key": d.get("key")}, ip)
                return self.sendj(200, {"ok": True})
            if what == "stage":
                r = project_m.move_stage(pid, d.get("action", ""), uid, d.get("reason_code", ""), d.get("note", ""))
                store.audit(uid, "project.stage", pid, {k: d.get(k) for k in ("action", "reason_code")}, ip)
                return self.sendj(200, r)
            if what == "launch-date":
                r = project_m.set_launch_date(pid, d.get("launch_date", ""), d.get("reason", ""), uid)
                store.audit(uid, "project.launch_date", pid, {k: d.get(k) for k in ("launch_date", "reason")}, ip)
                return self.sendj(200, r)
            if what == "revenue":
                r = project_m.set_revenue(pid, d.get("counted") in ("1", "true", "on"), d.get("basis", ""), uid)
                store.audit(uid, "project.revenue", pid, {"counted": d.get("counted"), "basis": d.get("basis")}, ip)
                return self.sendj(200, r)
            if what == "variant":
                r = project_m.save_variant(pid, d, uid)
                store.audit(uid, "project.variant", pid, {k: d.get(k) for k in ("id", "label", "state")}, ip)
                return self.sendj(200, r)
            if what == "variant-delete":
                r = project_m.delete_variant(pid, d.get("id"), uid)
                store.audit(uid, "project.variant_delete", pid, {"id": d.get("id")}, ip)
                return self.sendj(200, r)
            if what == "check":
                project_m.save_check(pid, d.get("item_key", ""),
                                     d.get("done") in ("1", "true", "on"),
                                     d.get("note", ""), uid)
                store.audit(uid, "project.check", pid, d, ip)
                return self.sendj(200, {"ok": True})
            return self.sendj(404, {"error": "not found"})

        # 案件外の仕事・他部署への依頼（FR-47・FR-48）
        if parts == ["work-items"] and method == "POST":
            d = self.body()
            r = task_m.create_work_item(d, uid)
            store.audit(uid, "work_item.create", str(r["id"]), {k: d.get(k) for k in ("kind", "title", "dept")}, ip)
            return self.sendj(200, r)
        if len(parts) == 3 and parts[0] == "work-items" and parts[2] == "receive" and method == "POST":
            r = task_m.receive_work_item(int(parts[1]), uid)
            store.audit(uid, "work_item.receive", parts[1], None, ip)
            return self.sendj(200, r)
        if len(parts) == 3 and parts[0] == "work-items" and parts[2] == "accept" and method == "POST":
            r = task_m.accept_work_item(int(parts[1]), uid)
            store.audit(uid, "work_item.accept", parts[1], None, ip)
            return self.sendj(200, r)

        # /api/tasks
        if parts == ["tasks"] and method == "GET":
            return self.sendj(200, task_m.listing(
                qs.get("when", task_m.DEFAULT_WHEN), qs.get("tab", "project"),
                qs.get("role", ""), qs.get("assignee", ""),
                mine=uid if qs.get("mine") == "1" else ""))
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
            # 接続先（ChatWork の部屋・AI予算の口・本文の URL）は**アプリ権限 admin にだけ**見せる。
            # 業務の数え方ではなく、他のシステムへのつなぎ方なので（2026-10-06 app-ui の提案・ADR-058）
            rows = admin_m.settings_view(uid, user.get("role"), CONNECTION_KEYS)
            return self.sendj(200, {"rows": rows, "roles": admin_m.roles_view(user.get("role")),
                                    "log": admin_m.change_log(app_role=user.get("role") or ""),
                                    "concept_stock": idea_m.concept_stock(),
                                    "ai_scoring": ai_m.status(uid)})
        if len(parts) == 2 and parts[0] == "ideas" and method == "GET":
            d = idea_m.detail(parts[1])
            if d is None:
                return self.sendj(404, {"error": "アイデアがありません"})
            d["ai_proposals"] = ai_m.proposals_of(parts[1])        # AI採点の案（ADR-081）
            d["ai_web"] = ai_web_m.of("idea", parts[1], "demand")    # ウェブで調べた需要・市場（ADR-088）
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
            if what == "ai-web":
                r = ai_web_m.start_run("demand", iid, uid)
                store.audit(uid, "idea.ai_web", iid, r, ip)
                return self.sendj(200, r)
            if what == "ai-score":
                # F-1-11。**AI は案を出すだけ**（点にはしない・ADR-081）。この1件の案を出す回を起こす
                r = ai_m.start_run([iid], uid)
                store.audit(uid, "idea.ai_score", iid, r, ip)
                return self.sendj(200, r)
            return self.sendj(404, {"error": "not found"})
        if (len(parts) == 5 and parts[0] == "ideas" and parts[2] == "ai-proposals"
                and parts[4] in ("adopt", "reject") and method == "POST"):
            d = self.body()
            pid = int(parts[3])
            if parts[4] == "adopt":
                axes = d.get("axes") if isinstance(d.get("axes"), dict) else {a: d.get(a) for a in ai_m.AXES}
                r = ai_m.adopt(pid, uid, axes, d.get("note", ""))
            else:
                r = ai_m.reject(pid, uid, d.get("note", ""))
            store.audit(uid, f"idea.ai_proposal.{parts[4]}", parts[1], {"proposal": pid, **d}, ip)
            return self.sendj(200, r)

        # ── /api/ai-web/<id>/adopt|reject（ウェブ調査の採用・ADR-088）──
        if len(parts) == 3 and parts[0] == "ai-web" and parts[2] in ("adopt", "reject") and method == "POST":
            d = self.body()
            wid = int(parts[1])
            if parts[2] == "adopt":
                picks = d.get("picks")
                if isinstance(picks, str):
                    picks = [int(x) for x in picks.split(",") if x.strip()]
                r = ai_web_m.adopt(wid, uid, picks or [])
            else:
                r = ai_web_m.reject(wid, uid, d.get("note", ""))
            store.audit(uid, f"ai_web.{parts[2]}", str(wid), {"picks": d.get("picks")}, ip)
            return self.sendj(200, r)

        # ── /api/ai/score（AI採点の案・ADR-081）──
        if parts == ["ai", "score"] and method == "GET":
            return self.sendj(200, {**ai_m.status(uid), "runs": ai_m.runs(), "pending_rows": ai_m.pending()})
        if parts == ["ai", "score-runs"] and method == "POST":
            d = self.body()
            ids = d.get("idea_ids") or None
            if isinstance(ids, str):
                ids = [x.strip() for x in ids.split(",") if x.strip()] or None
            r = ai_m.start_run(ids, uid)
            store.audit(uid, "ai.score_run", str(r.get("run_id") or ""), {**r, "idea_ids": d.get("idea_ids")}, ip)
            return self.sendj(200, r)
        if len(parts) == 3 and parts[:2] == ["ai", "score-runs"] and method == "GET":
            return self.sendj(200, ai_m.run_view(int(parts[2])))

        # ── /api/plan（第1段の残り・F-3 ／ FR-82〜FR-86）──
        #
        # **警告は保存を止めない**（FR-84）。検査は GET 側で返すだけで、
        # POST 側は一度も検査結果を見ない。ここで弾くと現場は表計算に戻る。
        if parts == ["plan"] and method == "GET":
            r = plan_m.overview(qs.get("fy") or None)
            r["can_approve"] = plan_m.can_approve(uid)            # 承認のボタンは社長にだけ出す（ADR-058）
            r["can_delete"] = plan_m.can_delete(uid)              # 策定中の版を消す（ADR-064）
            return self.sendj(200, r)
        if parts == ["plan", "versions"] and method == "POST":
            d = self.body()
            r = plan_m.create_version(uid, d.get("fiscal_year"),
                                      d.get("label", ""), d.get("note", ""))
            store.audit(uid, "plan.version.create", r["id"], d, ip)
            return self.sendj(200, r)
        if len(parts) == 3 and parts[:2] == ["plan", "versions"] and method == "GET":
            d = plan_m.detail(parts[2])
            if d is None:
                return self.sendj(404, {"error": "その版がありません"})
            return self.sendj(200, d)
        if len(parts) == 4 and parts[:2] == ["plan", "versions"] and method == "POST":
            vid, what = parts[2], parts[3]
            d = self.body()
            if what == "approve":
                r = plan_m.approve(vid, uid)
                store.audit(uid, "plan.version.approve", vid, r, ip)
                return self.sendj(200, r)
            if what == "delete":
                r = plan_m.delete_version(vid, uid)
                store.audit(uid, "plan.version.delete", vid,
                            {"label": f"年間プランの版を消した（{r['label']}・枠 {r['slots']} 本）"}, ip)
                return self.sendj(200, r)
            if what == "revise":
                r = plan_m.revise(vid, uid, d.get("label", ""))
                store.audit(uid, "plan.version.revise", r["id"],
                            {"based_on": vid, "slots": r.get("slots_copied")}, ip)
                return self.sendj(200, r)
            if what == "ack":
                # 例外の承知（FR-84）。**理由は必須。**警告自体は消えない
                r = plan_m.ack(vid, d.get("rule", ""), d.get("scope", ""),
                               d.get("reason", ""), uid)
                store.audit(uid, "plan.ack", vid, d, ip)
                return self.sendj(200, r)
            return self.sendj(404, {"error": "not found"})
        if parts == ["plan", "slots"] and method == "POST":
            d = self.body()
            r = plan_m.create_slot(uid, **d)
            store.audit(uid, "plan.slot.create", r["id"], d, ip)
            return self.sendj(200, r)
        if len(parts) == 4 and parts[:2] == ["plan", "slots"] and method == "POST":
            sid, what = parts[2], parts[3]
            d = self.body()
            if what == "update":
                r = plan_m.update_slot(sid, uid, **d)
                store.audit(uid, "plan.slot.update", sid, d, ip)
                return self.sendj(200, r)
            if what == "delete":
                r = plan_m.delete_slot(sid)
                store.audit(uid, "plan.slot.delete", sid, {}, ip)
                return self.sendj(200, r)
            if what == "convert":
                # **1操作で案件へ**（FR-86）。タスク設定期限を割り戻して返す
                r = plan_m.convert(sid, uid, **d)
                store.audit(uid, "plan.slot.convert", sid,
                            {"project_id": r["project_id"],
                             "task_setup_due": r["task_setup_due"].get("due")}, ip)
                return self.sendj(200, r)
            return self.sendj(404, {"error": "not found"})

        # ── /api/automation（F-15 ／ FR-159〜）──────────
        #
        # **このアプリは実装しない。**答えが揃ったら要件として書き出し、
        # 作る人へ渡すところまで。`set_stage` が答えの不足で断るのがその担保。
        if parts == ["automation"] and method == "GET":
            return self.sendj(200, auto_m.listing(qs))
        if parts == ["automation"] and method == "POST":
            d = self.body()
            r = auto_m.create(uid, **d)
            store.audit(uid, "automation.create", r["id"], d, ip)
            return self.sendj(200, r)
        if len(parts) == 2 and parts[0] == "automation" and method == "GET":
            d = auto_m.detail(parts[1])
            if d is None:
                return self.sendj(404, {"error": "その依頼がありません"})
            # FR-168 の昇格先（標準タスクに当たらない依頼だけ画面に出す）
            d["promote"] = {"can": tpl_m.can_edit(uid),
                            "flows": [{"code": f["code"], "label": f["label"]} for f in project_m.flow_types()],
                            "roles": store.rows(store.q("SELECT code, label FROM role ORDER BY sort"))}
            return self.sendj(200, d)
        if len(parts) == 3 and parts[0] == "automation" and parts[2] == "chatwork" \
                and method == "GET":
            # **押す前に、送る文面をそのまま返す**
            try:
                return self.sendj(200, auto_m.chatwork_status(parts[1]))
            except ValueError as e:
                return self.sendj(404, {"error": str(e)})
        if len(parts) == 3 and parts[0] == "automation" and method == "GET" \
                and parts[2] == "requirement":
            try:
                body = auto_m.requirement_text(parts[1]).encode("utf-8")
            except ValueError as e:
                return self.sendj(404, {"error": str(e)})
            return self.send(200, body, "text/markdown; charset=utf-8")
        if len(parts) == 3 and parts[0] == "automation" and method == "POST":
            rid, what = parts[1], parts[2]
            d = self.body()
            if what == "answer":
                r = auto_m.answer(rid, d.get("q_key", ""), d.get("answer", ""), uid)
                store.audit(uid, "automation.answer", rid,
                            {"q_key": d.get("q_key")}, ip)
                return self.sendj(200, r)
            if what == "effort":
                r = auto_m.set_effort(rid, d.get("minutes_each"),
                                      d.get("times_per_month"), uid)
                store.audit(uid, "automation.effort", rid, d, ip)
                return self.sendj(200, r)
            if what == "stage":
                r = auto_m.set_stage(rid, d.get("stage", ""), uid,
                                     d.get("handoff_to", ""))
                store.audit(uid, "automation.stage", rid, d, ip)
                return self.sendj(200, r)
            if what == "after":
                # FR-169。実装後の手間を記録して、前と並べる
                r = auto_m.set_after(rid, d.get("minutes_each"), d.get("times_per_month"), d.get("note", ""), uid)
                store.audit(uid, "automation.after", rid, {k: d.get(k) for k in ("minutes_each", "times_per_month")}, ip)
                return self.sendj(200, r)
            if what == "promote":
                # FR-168。標準タスクに無い作業を、ひな形の下書きか案件外の仕事へ（どちらかは商品開発部の判断）
                if d.get("to") == "work":
                    return self.sendj(200, tpl_m.promote_to_work(rid, d.get("role", ""), uid, ip))
                return self.sendj(200, tpl_m.promote_to_template(rid, d.get("flow", ""), d.get("role", ""),
                                                                 d.get("hours"), uid, ip))
            if what == "chatwork":
                # **人が押したときだけ送る。**自動では送らない
                r = auto_m.chatwork_send(
                    rid, uid,
                    allow_resend=str(d.get("resend") or "") in ("1", "true", "on"))
                store.audit(uid, "automation.chatwork", rid,
                            {"room": r["room"], "message_id": r["message_id"]}, ip)
                return self.sendj(200, r)
            if what == "note":
                r = auto_m.add_note(rid, d.get("body", ""), uid, d.get("q_key", ""))
                store.audit(uid, "automation.note", rid, {}, ip)
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
        # 発売済でゲートの記録が1件も無い案件（移行分）は、起票の判定待ちに見せない（2026-10-09 点検 1-4）
        no_gate = p["stage"] in ("発売済", "追跡中") and not store.val(
            "SELECT COUNT(*) FROM gate_review WHERE project_id=?", (p["id"],), 0)
        if no_gate:
            nx = None
        who = ("／".join(role_label.get(x, x) for x in nx["approver_role"])
               if nx else "—")
        rows.append({
            "id": p["id"], "product": project_m.display_name(p),
            "flow_label": flow_label.get(p["flow_type"] or "", "—"),
            "next_gate": ("発売済・ゲートの記録なし" if no_gate else f"{nx['gate']} {nx['name']}" if nx else "—"),
            "who": who,
            "mine": bool(nx and (mine & set(nx["approver_role"]))),
            "cells": [{"gate": g["gate"], "state": g["state"],
                       "glyph": gate_m.STATES[g["state"]]["glyph"],
                       "word": gate_m.STATES[g["state"]]["word"],
                       "missing_n": len(g["missing"])} for g in b],
        })
    # **自分が判断者のものが最初に来る**
    rows.sort(key=lambda x: (not x["mine"],))
    since = (store.today() - _dt.timedelta(days=30)).isoformat()
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
    # **予備時間を実作業の数に混ぜない**（2026-09-23 の決定）。
    # `task_template` は実作業の数（178）のまま。予備は別の鍵で出す
    # **種データ（版1）の数**で見る。画面で作った改訂版（2以降）は別の鍵で出す（ADR-061）
    n["task_template_reserve"] = store.val(
        "SELECT COUNT(*) FROM task_template WHERE kind='予備' AND template_version=1", (), 0)
    n["task_template"] = store.val(
        "SELECT COUNT(*) FROM task_template WHERE template_version=1", (), 0) - n["task_template_reserve"]
    n["task_template_revised"] = store.val(
        "SELECT COUNT(*) FROM task_template WHERE template_version>1", (), 0)
    n["task_reserve"] = store.val(
        "SELECT COUNT(*) FROM task WHERE kind='予備'", (), 0)
    n["flow_type_without_effort_point"] = store.val(
        "SELECT COUNT(*) FROM flow_type WHERE effort_point IS NULL", (), 0)
    n["flow_type_without_template"] = store.val(
        "SELECT COUNT(*) FROM flow_type WHERE has_template=0", (), 0)
    # 画面で人が決めた開発タイプ（係数・ひな形）。決めた人と日時が付いている（ADR-061）
    n["flow_type_decided_on_screen"] = store.val(
        "SELECT COUNT(*) FROM flow_type WHERE decided_at IS NOT NULL AND decided_by IS NOT NULL", (), 0)
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
    n = ai_m.abandon_running()                  # 走ったまま残った AI の回を「中断」に倒す（ADR-081）
    if n:
        sys.stderr.write(f"[ai] 走ったまま残っていた回 {n} 件を中断にしました\n")
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
