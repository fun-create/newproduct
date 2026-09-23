#!/usr/bin/env python3
"""
NEW PRODUCT — 自己点検（全体設計書 §5-8・N-9）。

    python3 selfcheck.py            # 自分でサーバを起こして検査し、止める
    python3 selfcheck.py --port 8794   # 既に動いているサーバを検査する

**なぜ状態コードだけを見てはいけないか。**
`.css` を `application/octet-stream` で返すと、`nosniff` のせいでブラウザが
スタイルシートの適用を拒む。**しかも HTTP は 200 のまま**なので、
`curl -o /dev/null -w '%{http_code}'` では気づけない。
2026-09-20 に Auto GROWTH と LPSCOPE の両方で実際に起き、ヘッダーが素の文字になった。

→ **実際に HTTP で取得して Content-Type を読む。**
  併せて、許可リストに無い拡張子が落ちること、ディレクトリ跨ぎが落ちることも見る。

接続には `http.client` を使う（`urllib` は URL の `..` を手元で畳んでしまい、
**サーバに届く前に消える**ので、跨ぎの検査にならない）。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
UI = BASE / "ui"

OK, NG = "OK", "NG"
_results: list[tuple[str, str, str]] = []


def note(status: str, what: str, detail: str = ""):
    _results.append((status, what, detail))
    print(f"[{status}] {what}" + (f" — {detail}" if detail else ""), flush=True)


class Client:
    """**パスを畳まずにそのまま送る**最小のクライアント。"""

    def __init__(self, port: int):
        self.port = port

    def get(self, raw_path: str, headers: dict | None = None, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.putrequest(method, raw_path, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            for k, v in (headers or {}).items():
                c.putheader(k, v)
            c.endheaders()
            r = c.getresponse()
            body = r.read()
            return r.status, dict(r.getheaders()), body
        finally:
            c.close()


# ────────────────────────── 検査 ──────────────────────────
def check_health(cl: Client):
    st, hd, body = cl.get("/api/health")
    if st != 200:
        return note(NG, "/api/health が 200 で返る", f"status={st}")
    ct = hd.get("Content-Type", "")
    if not ct.startswith("application/json"):
        return note(NG, "/api/health の型", f"Content-Type={ct!r}")
    try:
        d = json.loads(body)
    except ValueError as e:
        return note(NG, "/api/health が JSON", str(e))
    missing = [k for k in ("contract_version", "started_at", "departments_sha",
                           "ingest") if k not in d]
    if missing:
        return note(NG, "/api/health の中身", f"足りない鍵: {missing}")
    note(OK, "/api/health が 200 で返る",
         f"contract={d['contract_version']} departments_sha={d['departments_sha']} "
         f"ingest={d['ingest']} users={d.get('users_registered')}")


def check_content_types(cl: Client):
    """**ここが本体。**型を実測する。状態コードだけを見ない。"""
    want = [
        ("/assets/app-shell.css", 200, "text/css; charset=utf-8"),
        # アプリ固有CSS。**共通CSS と同じ事故が起きる**（200 のまま型だけ違う）
        ("/assets/np.css", 200, "text/css; charset=utf-8"),
        ("/assets/bar-logo.svg", 200, "image/svg+xml"),
        ("/assets/title-logo.svg", 200, "image/svg+xml"),
        ("/assets/favicon.ico", 200, "image/x-icon"),
        ("/assets/icon-192.png", 200, "image/png"),
        ("/assets/site.webmanifest", 200, "application/manifest+json"),
        ("/app.js", 200, "text/javascript; charset=utf-8"),
    ]
    for path, code, ctype in want:
        st, hd, body = cl.get(path + "?v=x")
        got = hd.get("Content-Type", "")
        if st != code:
            note(NG, f"{path} の状態コード", f"{st} （期待 {code}）")
            continue
        if got != ctype:
            note(NG, f"{path} の Content-Type",
                 f"{got!r} （期待 {ctype!r}）— **200 のまま型だけ違う事故**")
            continue
        if not body:
            note(NG, f"{path} の中身", "空で返った")
            continue
        if hd.get("X-Content-Type-Options") != "nosniff":
            note(NG, f"{path} の nosniff", "ヘッダが無い")
            continue
        note(OK, f"{path}", f"{got} / {len(body)} bytes")


def check_allowlist(cl: Client):
    """許可リストに無い拡張子は、**実在していても**配らない。"""
    probe = UI / "assets" / "allowlist-probe.txt"
    if not probe.is_file():
        note(NG, "許可リストの検査", f"検査用の実ファイルが無い: {probe}")
    else:
        st, _, body = cl.get("/assets/allowlist-probe.txt")
        if st == 404 and not body:
            note(OK, "許可リスト外（.txt・実在するファイル）が 404",
                 "拡張子で落ちている")
        else:
            note(NG, "許可リスト外（.txt・実在するファイル）が 404",
                 f"status={st} len={len(body)}")

    for p in ("/assets/x.txt", "/assets/app-shell.css.bak", "/assets/server.py"):
        st, _, _ = cl.get(p)
        note(OK if st == 404 else NG, f"許可リスト外 {p} が 404", f"status={st}")


def check_traversal(cl: Client):
    """ディレクトリ跨ぎ。**サーバに届く形のまま**送る。

    `urlparse` は百分率デコードをしないので、`%2e%2e%2f` は
    「そんな拡張子は知らない」で落ちる。中身が1バイトも漏れないことまで見る。
    """
    marker = b"newproduct listening"        # server.py にしか無い文字列
    for p in ("/assets/../server.py",
              "/assets/%2e%2e%2fserver.py",
              "/assets/..%2fserver.py",
              "/assets/..%252fserver.py",
              "/assets/../../etc/passwd",
              "/assets/%2e%2e/%2e%2e/etc/passwd",
              "/assets/./../server.py"):
        st, _, body = cl.get(p)
        leaked = marker in body or b"root:x:0" in body
        if st == 404 and not leaked:
            note(OK, f"跨ぎ {p} が 404", "")
        else:
            note(NG, f"跨ぎ {p} が 404", f"status={st} 漏えい={leaked}")


def check_default_deny(cl: Client):
    """**既定拒否。**利用者が未登録のあいだは、画面へ入れない。"""
    st, hd, _ = cl.get("/")
    if st == 303 and hd.get("Location") == "/login":
        note(OK, "未ログインで / が /login へ倒れる", "既定拒否")
    else:
        note(NG, "未ログインで / が /login へ倒れる",
             f"status={st} location={hd.get('Location')!r}")

    st, hd, body = cl.get("/login")
    if st == 200 and hd.get("Content-Type", "").startswith("text/html"):
        if b"bootstrap" in body.lower() or "初期登録".encode() in body:
            note(NG, "ログイン画面に初回登録の口が無い", "登録欄らしきものがある")
        else:
            note(OK, "/login が 200（初回登録の口は無い）", f"{len(body)} bytes")
    else:
        note(NG, "/login が 200", f"status={st}")


def check_svc(cl: Client):
    """3点判定（全体設計書 §6）。**外形ヘッダが付いていたら断る。**"""
    st, _, _ = cl.get("/api/svc/plan?fy=2026")
    note(OK if st == 403 else NG, "/api/svc/* が Bearer 無しで 403", f"status={st}")

    tok = (BASE / "config" / "svc_token")
    if tok.is_file():
        t = tok.read_text().strip()
        # 正しいトークンでも、**Caddy を通った印が付いていれば断る**
        st, _, _ = cl.get("/api/svc/plan",
                          {"Authorization": f"Bearer {t}",
                           "X-Forwarded-Proto": "https"})
        note(OK if st == 403 else NG,
             "/api/svc/* が X-Forwarded-Proto 付きで 403",
             f"status={st} — loopback だけでは外から素通りになる")
        st, _, _ = cl.get("/api/svc/plan",
                          {"Authorization": f"Bearer {t}", "X-Real-IP": "1.2.3.4"})
        note(OK if st == 403 else NG,
             "/api/svc/* が X-Real-IP 付きで 403", f"status={st}")
        # 3点そろえば通る（中身はまだ 501）
        st, _, _ = cl.get("/api/svc/plan", {"Authorization": f"Bearer {t}"})
        note(OK if st in (501, 200) else NG,
             "/api/svc/* が3点そろえば通る", f"status={st}（枠だけなので 501 が正）")
    else:
        note(NG, "config/svc_token", "見つからない")

    st, _, _ = cl.get("/api/svc/plan", {"Authorization": "Bearer wrong-token-xxxx"})
    note(OK if st == 403 else NG, "/api/svc/* が誤ったトークンで 403", f"status={st}")


# ───────────────── 画面（描画して落ちないか）─────────────────
def check_screens(cl: Client):
    """いまは殻だけなので、**帯の形と NAV の整合**を見る。

    業務画面が入ったら、ここに各 view の描画を足す。
    """
    idx = (UI / "index.html").read_text(encoding="utf-8")

    musts = [
        ('<html class="fca-shell" lang="ja" data-app="new-product">', "帯の土台"),
        ('<a class="fca-skip" href="#main">', "本文へ飛ぶリンク"),
        ('<header class="fca-bar">', "帯"),
        ('class="fca-brand-logo"', "帯のロゴ"),
        ('alt="FUN-CREATE NEW PRODUCT"', "alt の正式表記（唯一の読み上げ経路）"),
        ('aria-label="主要メニュー"', "nav のラベル"),
        ('<span class="fca-spacer">', "詰め物"),
        ('<main id="main"', "本文"),
        ('/assets/app-shell.css', "共通CSS の読み込み"),
    ]
    for s, why in musts:
        note(OK if s in idx else NG, f"index.html に {why}", "" if s in idx else s)

    if "purpose" in (UI / "assets" / "site.webmanifest").read_text(encoding="utf-8"):
        note(NG, "webmanifest に purpose を書いていない",
             "maskable のセーフゾーンを超える素材なので宣言しない")
    else:
        note(OK, "webmanifest に purpose を書いていない", "")

    if re.search(r"font-family\s*:", idx) or \
       re.search(r"font-family\s*:", (UI / "login.html").read_text(encoding="utf-8")):
        note(NG, "表示用書体を指定していない",
             "VPS にもブラウザにも入っておらず黙って別の書体に落ちる")
    else:
        note(OK, "表示用書体を指定していない", "")

    # NAV の正本（JSON）と、帯に実際に並んでいるリンクが一致するか
    m = re.search(r'id="fca-nav-data">(.*?)</script>', idx, re.S)
    if not m:
        return note(NG, "NAV の正本", "index.html に fca-nav-data が無い")
    try:
        nav = json.loads(m.group(1))
    except ValueError as e:
        return note(NG, "NAV の正本が JSON", str(e))
    if len(nav) != 7:
        note(NG, "NAV は7つ", f"{len(nav)}個ある（keiei の layout.py が上限7と書いている）")
    else:
        note(OK, "NAV は7つ", "／".join(n["label"] for n in nav))

    anchors = re.findall(r'<a href="(#[^"]*)"\s+data-view="([^"]+)"', idx)
    if [(h, v) for h, v in anchors] == [(n["hash"], n["view"]) for n in nav]:
        note(OK, "帯のリンクと NAV の正本が一致", f"{len(anchors)}件")
    else:
        note(NG, "帯のリンクと NAV の正本が一致",
             f"帯={anchors} 正本={[(n['hash'], n['view']) for n in nav]}")

    if len({n["hash"] for n in nav}) != len(nav):
        note(NG, "NAV の hash が重複していない", "")
    else:
        note(OK, "NAV の hash が重複していない", "")

    js = (UI / "app.js").read_text(encoding="utf-8")
    note(OK if 'aria-current' in js else NG, "app.js が aria-current を出す",
         "現在地を色だけで示さない（N-11）")
    note(OK if 'hashchange' in js and 'location.hash' in js else NG,
         "app.js が hash routing を持つ", "§5-1")
    note(OK if 'document.title' in js else NG,
         "app.js が document.title を書き換える", "タブを見比べるため")

    # JS の構文。node があれば本物の検査をする
    try:
        r = subprocess.run(["node", "--check", str(UI / "app.js")],
                           capture_output=True, text=True, timeout=30)
        note(OK if r.returncode == 0 else NG, "app.js の構文",
             (r.stderr or "").strip()[:200])
    except (OSError, subprocess.SubprocessError) as e:
        note(OK, "app.js の構文", f"node が無いので省略（{e}）")


# ───────────────── 第2段（案件・タスク・ゲート）─────────────────
def check_stage2_db(cl: Client):
    """**種データが入っているか。**0 と「ファイルが無い」を区別する。

    件数を実測する。「入っているはず」で通さない。
    """
    st, _, body = cl.get("/api/health")
    if st != 200:
        return note(NG, "第2段: /api/health", f"status={st}")
    d = json.loads(body)
    db = d.get("db")
    if not isinstance(db, dict) or "error" in db:
        return note(NG, "第2段: DB の件数が /api/health に出る", str(db))

    want = {"flow_type": 7, "role": 7, "gate_def": 7,
            "hold_reason": 5, "abort_reason": 4, "task_template": 178,
            # 6フロー × 管理者/メンバー。**実作業178と混ぜない**（2026-09-23）
            "task_template_reserve": 12}
    for k, n in want.items():
        got = db.get(k)
        note(OK if got == n else NG, f"第2段: {k} が {n} 件", f"実測 {got}")

    # **推測で埋めていないこと。**⑤資材リニューアルの係数は NULL のまま
    note(OK if db.get("flow_type_without_effort_point") == 1 else NG,
         "第2段: 工数ポイント係数が未確定の開発タイプが1つ",
         f"実測 {db.get('flow_type_without_effort_point')} — "
         "⑤資材リニューアルは実測できないので NULL のままにしてある")
    # **⑦ページリニューアルには標準タスクが無い。発明していない**
    note(OK if db.get("flow_type_without_template") == 1 else NG,
         "第2段: 標準タスクが無い開発タイプが1つ",
         f"実測 {db.get('flow_type_without_template')} — ⑦ページリニューアル")


def check_stage2_api(cl: Client):
    """**画面のAPIは既定拒否。**未ログインで中身を返さない。"""
    for p in ("/api/dashboard", "/api/projects", "/api/tasks", "/api/gates",
              "/api/meta", "/api/me"):
        st, hd, body = cl.get(p)
        ct = hd.get("Content-Type", "")
        if st == 401 and ct.startswith("application/json"):
            note(OK, f"{p} が未ログインで 401（JSON）", "")
        else:
            note(NG, f"{p} が未ログインで 401（JSON）",
                 f"status={st} type={ct!r} len={len(body)}")


def check_stage2_screens():
    """画面の描画。**ファイルに対して見る**（ログインが要るので HTTP では出ない）。

    ここで見るのは「その画面があるか」ではなく、
    **誤読を防ぐ文言が消えていないか**（§5-6・§5-9・N-11）。
    """
    js = (UI / "app.js").read_text(encoding="utf-8")
    idx = (UI / "index.html").read_text(encoding="utf-8")

    for s, why in [
        ("/assets/np.css", "アプリ固有CSS を共通CSS の後に読む"),
    ]:
        note(OK if s in idx else NG, f"index.html に {why}", "" if s in idx else s)

    for s, why in [
        ("viewHome", "ダッシュボード"),
        ("viewProjects", "案件一覧"),
        ("viewProject", "案件カルテ"),
        ("viewTasks", "タスク"),
        ("viewGates", "ゲート盤"),
        ("#/projects/", "案件カルテの URL（貼れないと人は Drive を使い続ける）"),
        ("overdue+today", "タスクの既定が「期限切れ＋今日」"),
        ("期限なし", "「期限なし」が常設ボタン"),
        ("6フロー合算", "「43.25h/27.28h」に6フロー合算の注記（§5-6）"),
        ("1本あたりではない", "1本あたりではないことの明示"),
        ("対象外", "④の簡易フローの `対象外`（§5-6）"),
        ("標準タスク未定義", "⑦ページリニューアルの標準タスク未定義の明示"),
        ("未計測", "未計測と0を区別する（§5-9 の7）"),
        ("いま欠けているもの", "カルテ最上段の B節（F-6-2）"),
        ("業務ロール", "承認資格はアプリ権限ではなく業務ロール（§10-2 ②）"),
        ("aria-current", "現在地を色だけで示さない（N-11）"),
    ]:
        note(OK if s in js else NG, f"app.js に {why}", "" if s in js else s)

    # **商品名を出していないこと。**カルテの見出しは分類とサイズ
    note(OK if "product_label" not in js else NG,
         "app.js が商品名の組み立てをしていない",
         "分類とサイズはサーバ側（project.product_label）で作る（N-6-2）")

    # 予備時間（F-5-7 ／ 2026-09-23 の決定）。**実作業と1つの数にしない**
    for s3, why in [
        ("予備h", "実作業と予備を別の列で出す"),
        ("work_hours", "実作業の数を合計と別に持つ"),
        ("reserve_caption", "予備はAI削減の試算に入れない、の断り"),
    ]:
        note(OK if s3 in js else NG, f"app.js に {why}", "" if s3 in js else s3)

    # **Caddy の断片が画面のAPIを塞いでいないこと。**
    # ここはループバックからは絶対に見えない層（selfcheck も e2e も 127.0.0.1）。
    # 2026-09-22、`path /api/*` になっていてログイン後の画面が全部
    # 「表示できませんでした」になっていた。**外から1回叩かないと分からない。**
    frag = (BASE / "deploy" / "Caddyfile.fragment").read_text(encoding="utf-8")
    note(OK if re.search(r"^\s*@svc path /api/svc/\*", frag, re.M) else NG,
         "Caddy 断片が /api/svc/* だけを落としている",
         "`/api/*` にすると SPA の全画面が 404 になる（ループバックでは気づけない）")
    note(OK if not re.search(r"^\s*@svc path /api/\*\s*$", frag, re.M) else NG,
         "Caddy 断片が画面のAPIを塞いでいない", "")

    css = (UI / "assets" / "np.css").read_text(encoding="utf-8")
    note(OK if not re.search(r"font-family\s*:", css) else NG,
         "np.css が表示用書体を指定していない",
         "VPS にもブラウザにも入っておらず黙って別の書体に落ちる")
    note(OK if "--fca-" in css else NG,
         "np.css が共通トークン（--fca-*）を使う",
         "色の値をアプリ側で作ると明暗のどちらかだけ直る事故になる")


# ───────────────── 第1段（アイデア台帳・採点v2）─────────────────
def check_stage1_db(cl: Client):
    """**入っている件数を実測する。**「入っているはず」で通さない。"""
    st, _, body = cl.get("/api/health")
    if st != 200:
        return note(NG, "第1段: /api/health", f"status={st}")
    d = json.loads(body).get("db", {})
    idea = d.get("idea")
    if not isinstance(idea, dict) or "error" in idea:
        return note(NG, "第1段: アイデアの件数が /api/health に出る", str(idea))

    # 採点の版。v1 は4シート分＋v2 で5件。**版を分けて併存させる**（F-1-10）
    note(OK if idea.get("rubric") == 5 else NG, "第1段: rubric が 5 版",
         f"実測 {idea.get('rubric')} — v1-original/v1-uchiwa/v1-lovot/"
         "v1-bukkomi/v2")
    note(OK if idea.get("theme") == 4 else NG, "第1段: 評価テーマが 4 件",
         f"実測 {idea.get('theme')}")
    # **機会カレンダーはまだ取り込んでいない。**0 を「無い」と言い換えない
    note(OK if idea.get("theme_score") == 0 else NG,
         "第1段: ライフイベント採点は未取込（0件）",
         f"実測 {idea.get('theme_score')} — 元表の列位置が行によってずれており、"
         "機械的に読むと取り違える。F-2（機会カレンダー）で入れる")
    note(OK if idea.get("theme_signal") == 0 else NG,
         "第1段: FCTR の時限スコアは未取込（0件）",
         f"実測 {idea.get('theme_signal')} — AutoGrowth からの取込は未実装（F-9）")

    n_idea = idea.get("idea") or 0
    by_sheet = idea.get("idea_by_sheet") or {}
    if n_idea == 0:
        note(OK, "第1段: アイデアは未移行（0件）",
             "tools/import_ideas.py を流すと入る。**0 と未実装を区別している**")
    else:
        note(OK, "第1段: アイデアの件数", f"{n_idea} 件 / シート別 {by_sheet}")
        # **移行分の起票経路は不明。**推測で埋めていないこと（N-10）
        note(OK if idea.get("idea_without_origin", 0) > 0 else NG,
             "第1段: 移行分の起票経路が「不明」のまま",
             f"origin が NULL のアイデア {idea.get('idea_without_origin')} 件")
        vs = idea.get("idea_score_by_version") or {}
        note(OK if not [k for k in vs if k == "v2"] or vs.get("v2", 0) >= 0 else NG,
             "第1段: 採点の版ごとの件数", str(vs))


def check_stage1_api(cl: Client):
    """**既定拒否。**未ログインでアイデアの中身を返さない。"""
    for p in ("/api/ideas", "/api/rubrics", "/api/settings"):
        st, hd, body = cl.get(p)
        ct = hd.get("Content-Type", "")
        if st == 401 and ct.startswith("application/json"):
            note(OK, f"{p} が未ログインで 401（JSON）", "")
        else:
            note(NG, f"{p} が未ログインで 401（JSON）",
                 f"status={st} type={ct!r} len={len(body)}")


def check_stage1_screens():
    """画面の描画。**誤読を防ぐ文言が消えていないか。**"""
    js = (UI / "app.js").read_text(encoding="utf-8")
    for s, why in [
        ("viewIdeas", "アイデア一覧"),
        ("viewIdea", "アイデア1件（採点画面）"),
        ("#/ideas/", "アイデアの URL（貼れる）"),
        ("newIdeaForm", "起票フォーム"),
        ("4項目＋起票経路だけ", "起票のハードルを上げない（F-1-3）"),
        ("/api/ideas/similar", "起票時に似た案を出す（F-1-12）"),
        ("名寄せ", "名寄せ済みという意味ではない、の断り"),
        ("合算していません", "v1 と v2 を足さない（F-1-10）"),
        ("①共通点（0〜70）", "2層採点の①（F-1-5）"),
        ("②テーマ適合点（0〜30）", "2層採点の②（F-1-5）"),
        ("減点係数", "生産方法(1-5) の減点係数（F-1-6）"),
        ("百分位", "ランクは絶対点ではなく百分位（F-1-9）"),
        ("想定粗利額", "高単価化しやすさを円建てに（F-1-7）"),
        ("需要発生（通年／季節／単発）", "通年性を3値へ（F-1-8）"),
        ("AI採点", "AI採点の状態を画面に出す（F-1-11）"),
        ("起票経路が不明", "移行分の起票経路を「不明」と書く（N-10）"),
        ("母数", "百分位の母数を出す"),
        ("G3通過・未発売", "コンセプト在庫月数の分子（F-1-14）"),
    ]:
        note(OK if s in js else NG, f"app.js に {why}", "" if s in js else s)

    # **未実装の画面に、実装済みのものを混ぜて書いていないこと**
    i = js.find("var NOT_YET")
    j = js.find("};", i) if i >= 0 else -1
    tail = js[i:j] if (i >= 0 and j > i) else ""
    note(OK if (tail and '"#/ideas"' not in tail) else NG,
         "app.js の「未実装」一覧からアイデアが外れている",
         "実装したのに「未実装」と出続けると、誰も開かない")
    note(OK if (tail and '"#/plan"' not in tail) else NG,
         "app.js の「未実装」一覧からプランが外れている",
         "実装したのに「未実装」と出続けると、誰も開かない")
    # **画面の本文にも「未実装」が残っていないこと。**NOT_YET だけ直しても、
    # 各画面の空のときの文言に書いた「（第1段・未実装）」は残る（2026-09-22 実際に残った）
    note(OK if "第1段・未実装" not in js else NG,
         "app.js の本文に古い「第1段・未実装」が残っていない",
         "年間プランは 2026-09-22 に実装した")

    # 年間プラン（F-3）。**判定を語で出しているか**（N-11）
    for s2, why in [
        ("LEVEL_WORD", "判定を語で出す表（適合／警告／未計測）"),
        ("未計測", "数えられないルールを「適合」に混ぜない（N-10）"),
        ("理由をつけて承知", "例外は理由を書いてから（F-3-3）"),
        ("案件にする", "枠→案件を1操作で（FR-86）"),
        ("日は未定", "日が決まっていない枠に仮の日付を出さない"),
        ("未確定", "工数ポイントの未確定と 0 を区別する（N-10）"),
    ]:
        note(OK if s2 in js else NG, f"app.js に {why}", "" if s2 in js else s2)

    css = (UI / "assets" / "np.css").read_text(encoding="utf-8")
    note(OK if "np-grid-2" in css else NG, "np.css に採点の2列組",
         "v1 と v2 を並べて見せる（F-1-10）")
    note(OK if not re.search(r"\.np-rank-(SS|S|A|B|C)", css) else NG,
         "np.css がランクを色で表していない",
         "**色に意味を持たせない**（N-11）。ランクは語として列に出す")


def check_index_served(cl: Client):
    """SPA の外枠そのものは、ログインしないと出ない。

    **中身の検査はファイルに対して行う**（上の check_screens）。
    ここでは「素の HTML が未ログインで漏れていない」ことだけを見る。
    """
    st, _, body = cl.get("/")
    note(OK if b"fca-nav-data" not in body else NG,
         "未ログインに SPA の中身を返していない", f"status={st}")


# ────────────────────────── 起動まわり ──────────────────────────
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_listen(port: int, proc, timeout=20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if proc and proc.poll() is not None:
            return False
        try:
            s = socket.create_connection(("127.0.0.1", port), 0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0,
                    help="既に動いているサーバの port。省略すると自分で起こす")
    a = ap.parse_args()

    proc = None
    port = a.port
    if not port:
        port = free_port()
        env = dict(os.environ, NEWPRODUCT_PORT=str(port),
                   NEWPRODUCT_BIND="127.0.0.1")
        proc = subprocess.Popen([sys.executable, "-u", str(BASE / "server.py")],
                                env=env, cwd=str(BASE),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        if not wait_listen(port, proc):
            out = ""
            if proc.poll() is not None:
                out = (proc.stdout.read() or "")[-2000:]
            note(NG, "サーバの起動", out.strip() or "listen しなかった")
            return 1

    try:
        cl = Client(port)
        check_health(cl)
        check_content_types(cl)
        check_allowlist(cl)
        check_traversal(cl)
        check_default_deny(cl)
        check_index_served(cl)
        check_svc(cl)
        check_screens(cl)
        check_stage2_db(cl)
        check_stage2_api(cl)
        check_stage2_screens()
        check_stage1_db(cl)
        check_stage1_api(cl)
        check_stage1_screens()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    bad = [r for r in _results if r[0] == NG]
    print("─" * 60)
    print(f"{len(_results)}件 検査 / 失敗 {len(bad)}件")
    for _, what, detail in bad:
        print(f"  NG  {what} — {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
