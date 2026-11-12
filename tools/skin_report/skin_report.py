#!/usr/bin/env python3
"""スキンシール展開判断レポート（週次・12シリーズ）。

毎週月曜 8:00 JST に、加熱式たばこ・スマホ・ゲーム機・ペンライトの12シリーズについて
「新発売があったか」「カットデータをどこで調達できるか」を調べ、
BUY / ALT / REAL / WAIT の4判定にしてHTMLで配る。

**2026-11 に Auto GROWTH（`connectors/ec/skin_report.py`・origin/main 42ed877 と同じ中身）から
NEW PRODUCT へ移した**（十文字さん 11-04・Auto GROWTH ADR-063・NEW PRODUCT ADR-140）。変えたのは
置き場・トークンの読み方・`claude` の呼び方（`data/aihome`・コネクタを読まない）だけ。調査の中身は変えない。

**その前の移行元は渡邊さんのPCの `C:\\skin-sticker-report`。**（2026-09-24 取り込み）
PCがスリープだと配信されない、という運用上の弱点をなくすために VPS へ移した。
移行元から持ってきた正本は3つ:

    research_prompt.md         → 調査手順 Step1〜5。build_prompt() に写してある
    config/series.yaml         → tools/skin_report/skin_series.yaml（12シリーズ定義）
    config/sites.yaml          → tools/skin_report/skin_sites.yaml（6サイト定義）
    templates/report_template.html → tools/skin_report/templates/skin_report.html
    src/chatwork_sender.py     → send_file()（リトライの作法をそのまま踏襲）

経緯と未決事項は Auto GROWTH の docs/growth-engine/2026-09-24_スキンシール週次レポートの移行.md。

    python3 tools/skin_report/skin_report.py              # 既定は dry-run（送らない）
    python3 tools/skin_report/skin_report.py --send       # ChatWork へ添付送信
    python3 tools/skin_report/skin_report.py --json a.json  # 調査済みJSONで描画だけ試す

**サーバー本体（server.py・app/）から import しない。**`socket.getaddrinfo` を IPv4 優先に
差し替えるので、import するとサーバー全体の名前解決が変わる。画面は app/skin_report.py が
出力のファイルを読むだけ。

■ このファイルを触る前に知っておくこと

1. **12シリーズと6サイトはコードに直書きしない。**`tools/skin_report/skin_*.yaml` が正本。
   移行元と同じ内容にしてある。片方だけ直すと、調査の対象が静かにずれる。

2. **AI は Claude Code CLI（`claude -p`）で呼ぶ。Anthropic API は使わない。**
   サブスクリプション認証で動くので従量課金が発生しない（ログインは app/ai_cli.py と同じ
   `data/aihome`）。**API に寄せるなら費用の見張り（app/ai_budget.py）を必ず通すこと。**

3. **既定は dry-run。**`--send` を付けたときだけ配信する。
   本番の部屋（FUN 商品開発）に練習を流さないため。
   移行元の `DRY_RUN_MODE=true` 既定と同じ考え方。

4. **巡回できなかったサイトの判定は作らない。**（Auto GROWTH の CLAUDE.md 約束4）
   6サイトの生死はこちらで実測し、その結果をAIに渡す。届かなかったサイトに
   ついて「直接見た」と主張する回答は検証で弾く。レポートには
   「確認できませんでした」と明記する。

5. **検証に落ちたら送らずに終了コードを立てる。**中途半端なレポートを配るより、
   届かないほうが誤解が少ない。黙って成功にはしない。

外部依存は PyYAML だけ（`/usr/lib/python3/dist-packages` に入っている）。
それ以外は標準ライブラリで書く。requests は使わない。

終了コード:
    0 正常（dry-run 成功を含む）
    1 前提が足りない（設定・テンプレート無し・トークン無しなど）
    2 調査（claude -p）が失敗した
    3 返ってきたJSONが検証に落ちた
    4 生成はできたが ChatWork への送信に失敗した（ローカルのHTMLは残る）
"""
import argparse
import html
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[1]                                # 本番では /opt/newproduct
SERIES_YAML = Path(os.environ.get("SKIN_SERIES_YAML", HERE / "skin_series.yaml"))
SITES_YAML = Path(os.environ.get("SKIN_SITES_YAML", HERE / "skin_sites.yaml"))
TEMPLATE = Path(os.environ.get(
    "SKIN_REPORT_TEMPLATE", Path(__file__).resolve().parent / "templates" / "skin_report.html"))
OUT_DIR = Path(os.environ.get("SKIN_REPORT_OUT", BASE / "data" / "skin_report"))

# 配信先: ChatWork「FUN 商品開発」。送信者は FUN-CREATE BOT
# （トークンは NEW PRODUCT の config/chatwork.env。読み出しは app/chatwork.py に合わせる）
ROOM_ID = os.environ.get("SKIN_REPORT_ROOM_ID", "407742634")
# 移行元の CHATWORK_MENTION_TO と同じ書式: "5865584:石原 千恵子,6224035:増地 洋子"
# **未設定のまま配信すると、本文と添付は届くのに [To:] だけが消える。**
# PC版は付けていたので、黙って落とすと移行による後退になる（2026-09-25 指摘）。
MENTION_TO = os.environ.get("SKIN_REPORT_MENTION_TO", "")

CLAUDE_BIN = os.environ.get("NEWPRODUCT_CLAUDE_BIN", os.environ.get("CLAUDE_BIN", "/usr/bin/claude"))
# ログインは NEW PRODUCT の AI と同じ場所（app/ai_cli.py・ADR-086）。本番は ProtectSystem=strict で data/ に書ける
AI_HOME = os.environ.get("NEWPRODUCT_AI_HOME", str(BASE / "data" / "aihome"))
MODEL = os.environ.get("SKIN_REPORT_MODEL", "opus")
TIMEOUT_SEC = int(os.environ.get("SKIN_REPORT_TIMEOUT", "2400"))   # 40分

# 移行元 .env の RESEARCH_PAST_DAYS / RESEARCH_FUTURE_DAYS と同じ既定
PAST_DAYS = int(os.environ.get("RESEARCH_PAST_DAYS", "30"))
FUTURE_DAYS = int(os.environ.get("RESEARCH_FUTURE_DAYS", "180"))

# 送信リトライ。移行元 src/chatwork_sender.py と同じ回数・同じ待ち時間
MAX_RETRIES = 3
RETRY_BACKOFF = (2, 5, 10)

JUDGMENTS = ("BUY", "ALT", "REAL", "WAIT")
BADGE_CLASS = {"BUY": "buy", "ALT": "alt", "REAL": "real", "WAIT": "wait", "UNKNOWN": "none"}
JUDGMENT_MEANING = {"BUY": "即購入", "ALT": "代替サイト購入", "REAL": "実機購入",
                    "WAIT": "様子見", "UNKNOWN": "判定なし"}
ARM_BADGE = {"ok": ("ok", "掲載あり"), "partial": ("partial", "部分対応"),
             "ng": ("ng", "未掲載"), "none": ("none", "―"), "unknown": ("none", "確認できず")}
JST = timezone(timedelta(hours=9))

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


# ══════════════════════════════════════════════════════════════════
# 外向きの通信は IPv4 を優先する
# ══════════════════════════════════════════════════════════════════
# **このVPSにはグローバルな IPv6 アドレスが無い。**
# それなのに cutout.shop は AAAA だけを返すことがあり、そのとき名前解決は
# 成功するのに接続できず、「到達できない」と記録されてしまう
# （2026-09-25 実測: curl -6 は code=000、curl -4 は 200）。
#
# **これは調達先の生死を誤って記録する。**Cutout.shop はリル系カットデータの
# 唯一の調達先なので、落ちたことにすると「調達できない」と誤った判断材料を配る。
#
# socket.getaddrinfo を差し替えて、A レコードがあるときは必ずそちらを先に使う。
# IPv4 が1つも無いときは元の結果をそのまま返す（IPv6 しか無いサイトを
# こちらの都合で切り捨てない。その場合は接続失敗として正しく記録される）。
#
# **プロセス全体に効く差し替えなので、ここが唯一の導入箇所。**
# 呼び出し側が family を明示しているときは触らない。
_ORIG_GETADDRINFO = socket.getaddrinfo


def _getaddrinfo_ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
    res = _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
    if family not in (0, socket.AF_UNSPEC):
        return res                      # 呼び出し側の指定を尊重する
    v4 = [r for r in res if r[0] == socket.AF_INET]
    return v4 or res


def _only_ipv6(host: str) -> bool:
    """A レコードが無く AAAA だけか。

    IPv4 優先にしても、**そもそも A が無いサイトには繋がらない**。
    そのとき「接続できませんでした」とだけ出ると、次に見る人がまた
    DNS とプロキシを疑って時間を使う。理由を名指しできるようにしておく。
    """
    try:
        res = _ORIG_GETADDRINFO(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except Exception:
        return False
    return bool(res) and not any(r[0] == socket.AF_INET for r in res)


def prefer_ipv4() -> bool:
    """IPv4 優先を有効にする。二重に適用しない。"""
    if socket.getaddrinfo is _getaddrinfo_ipv4_first:
        return False
    socket.getaddrinfo = _getaddrinfo_ipv4_first
    return True


# import した時点で効かせる。到達確認も ChatWork への送信も、
# ここを通ってからでないと同じ落とし穴を踏む
prefer_ipv4()


class Fail(Exception):
    """理由つきで止まる。**黙って握りつぶさない**ための型。"""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


# ══════════════════════════════════════════════════════════════════
# 設定（12シリーズ・6サイト）
# ══════════════════════════════════════════════════════════════════
def load_config():
    """移行元から引き継いだ yaml をそのまま読む。

    **シリーズ数が12でなければ止める。**「12シリーズ完全網羅版」という
    レポートの前提が崩れるため。増減させるならテンプレートの見出しも直す。
    """
    for p in (SERIES_YAML, SITES_YAML):
        if not p.is_file():
            raise Fail(1, f"設定がありません: {p}")
    series = (yaml.safe_load(SERIES_YAML.read_text(encoding="utf-8")) or {}).get("series") or []
    doc = yaml.safe_load(SITES_YAML.read_text(encoding="utf-8")) or {}
    sites = doc.get("sites") or []
    if len(series) != 12:
        raise Fail(1, f"{SERIES_YAML} のシリーズが {len(series)} 件です（12件のはず）")
    if not sites:
        raise Fail(1, f"{SITES_YAML} にサイトがありません")
    sites.sort(key=lambda s: s.get("priority", 99))
    return series, sites, doc.get("official_watch") or []


# ══════════════════════════════════════════════════════════════════
# ① 6サイトの到達確認
# ══════════════════════════════════════════════════════════════════
def reach_check(sites, timeout=20, attempts=2):
    """6サイトへ実際にアクセスして生死を測る。

    **AIに「巡回できた」と言わせない。**こちらで測った結果だけを事実として扱う。
    Cutout.shop は1回目が落ちて2回目に通ることがあるので2回試す
    （2026-09-24 実測）。403 は「サーバーは在るが拒否された」なので、
    到達失敗（DNSが引けない等）と区別して記録する。

    名前解決は **IPv4 を優先する**（モジュール先頭の prefer_ipv4 を参照）。
    このVPSにはグローバルな IPv6 が無く、AAAA だけを返すサイトを
    「到達できない」と誤記録していた。
    """
    out = []
    for s in sites:
        rec = {"id": s.get("id"), "name": s.get("name"), "url": s.get("url"),
               "role": s.get("role"), "notes": s.get("notes") or "",
               "status": None, "ok": False, "reason": "", "reason_detail": ""}
        for _ in range(attempts):
            try:
                req = urllib.request.Request(rec["url"], headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    rec["status"] = r.status
                    # **3xx も「到達」。**リダイレクトを返せるのはサーバーが生きている証拠。
                    # urllib は通常たどるのでここに 3xx が出るのは稀だが、たどらない種類
                    # （304 など）や上限超過で素通りすることがある。2xx だけを見ていると
                    # 生きているサイトを「確認できず」にしてしまう
                    # （十文字さんの実測では Cutout.shop は 302。2026-09-25 指摘）
                    rec["ok"] = 200 <= r.status < 400
                    rec["reason"] = "" if rec["ok"] else f"HTTP {r.status}"
                break
            except urllib.error.HTTPError as e:
                rec["status"] = e.code
                # 3xx が例外として上がることもある（urllib がたどらない種類）。
                # 応答を返せている以上「到達」とみなす
                rec["ok"] = 300 <= e.code < 400
                if rec["ok"]:
                    rec["reason"] = ""
                elif e.code in (401, 403, 406):
                    rec["reason"] = f"HTTP {e.code}（ブロック）"
                else:
                    rec["reason"] = f"HTTP {e.code}"
                break                      # 応答は返っているので再試行しない
            except Exception as e:         # URLError / timeout / DNS など
                # **例外の文字列をそのままレポートに出さない。**
                # 「URLError: <urlopen error [Errno -3] ...>」は読む人の役に立たない
                # （2026-09-24 の初回実走で実際に配布物へ出た）。
                # 原因の切り分けに要る詳細は reason_detail に残し、journal には出る
                rec["reason_detail"] = f"{type(e).__name__}: {e}"
                s_e = str(e).lower()
                if "name resolution" in s_e or "nodename" in s_e or "name or service" in s_e:
                    rec["reason"] = "ドメイン名を解決できませんでした（DNS）"
                elif "timed out" in s_e or "timeout" in s_e:
                    rec["reason"] = f"{timeout}秒以内に応答がありませんでした"
                elif "certificate" in s_e or "ssl" in s_e:
                    rec["reason"] = "TLS証明書を検証できませんでした"
                elif _only_ipv6(urllib.parse.urlsplit(s["url"]).hostname or ""):
                    # このVPSにグローバルな IPv6 が無いので、AAAA だけのサイトには
                    # どうやっても繋がらない。DNSやプロキシを疑わせないよう名指しする
                    rec["reason"] = ("IPv6（AAAA）しか公開されておらず、"
                                     "このサーバーからは接続できません")
                else:
                    rec["reason"] = "接続できませんでした"
        out.append(rec)
    return out


# ══════════════════════════════════════════════════════════════════
# ② claude -p に調査させて JSON で受け取る（移行元 research_prompt.md Step1〜3）
# ══════════════════════════════════════════════════════════════════
# --json-schema を付けると CLI 側でも構造を検査してくれるが、**これに頼り切らない。**
# 12シリーズ揃っているか・判定が4種のいずれかかは validate() で必ず見る。
SCHEMA = {
    "type": "object",
    "required": ["summary_title", "summary_paragraphs", "series", "releases", "cutdata",
                 "chatwork_message"],
    "properties": {
        "summary_title": {"type": "string"},
        "summary_paragraphs": {"type": "array", "items": {"type": "string"}},
        "chatwork_message": {"type": "string"},
        "series": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "name", "arm_status", "new_release", "judgment",
                             "judgment_note"],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "arm_status": {"enum": ["ok", "partial", "ng", "none", "unknown"]},
                    "links": {"type": "array", "items": {
                        "type": "object", "required": ["label", "url"],
                        "properties": {"label": {"type": "string"}, "url": {"type": "string"}}}},
                    "new_release": {"type": "string"},
                    "new_release_note": {"type": "string"},
                    "judgment": {"enum": ["BUY", "ALT", "REAL", "WAIT", "UNKNOWN"]},
                    "judgment_note": {"type": "string"},
                },
            },
        },
        "releases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["bucket", "series", "name", "judgment", "info",
                             "requires_new_mold", "mold_reason"],
                "properties": {
                    "bucket": {"enum": ["past", "future", "reference", "rumor"]},
                    # 新しい型（カットデータ）が要るか。**false はレポートに出さない**
                    "requires_new_mold": {"enum": [True, False, "unknown"]},
                    "mold_reason": {"type": "string"},
                    "series": {"type": "string"},
                    "name": {"type": "string"},
                    "meta": {"type": "string"},
                    "judgment": {"enum": ["BUY", "ALT", "REAL", "WAIT", "UNKNOWN"]},
                    "judgment_note": {"type": "string"},
                    "info": {"type": "string"},
                    "links": {"type": "array", "items": {
                        "type": "object", "required": ["label", "url"],
                        "properties": {"label": {"type": "string"}, "url": {"type": "string"}}}},
                },
            },
        },
        "cutdata": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["heading", "name", "judgment", "reason", "rows",
                             "requires_new_mold", "mold_reason"],
                "properties": {
                    "heading": {"type": "string"},
                    "requires_new_mold": {"enum": [True, False, "unknown"]},
                    "mold_reason": {"type": "string"},
                    "num": {"type": "string"},
                    "name": {"type": "string"},
                    "judgment": {"enum": ["BUY", "ALT", "REAL", "WAIT", "UNKNOWN"]},
                    "judgment_note": {"type": "string"},
                    "reason": {"type": "string"},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["site", "hit", "via", "note"],
                            "properties": {
                                "site": {"type": "string"},
                                "hit": {"enum": ["あり", "なし", "要相談", "不明"]},
                                # index = 検索インデックス経由（調査中はこれが基本）
                                # direct = 直接見た（**調査中は禁止**。到達確認の結果を
                                #          引くときだけ使える）/ none = 確認できず
                                "via": {"enum": ["direct", "index", "none"]},
                                "note": {"type": "string"},
                                "price": {"type": "string"},
                                "url": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
        "unverified": {"type": "array", "items": {"type": "string"}},
    },
}

# 新発売が無いときの既定判定（移行元 research_prompt.md Step 3「シリーズ別の既定判定」）
DEFAULT_JUDGMENT = {
    "01": ("ALT", "代替サイトメイン"),
    "02": ("REAL", "カットデータ未調達"),
    "03": ("ALT", "Cutout.shop経由"),
    "04": ("ALT", "新型番は代替サイト"),
    "11": ("REAL", "実機購入方針（固定）"),
    "12": ("REAL", "実機購入方針（固定）"),
}


def build_prompt(series, sites, watch, reach, basis, past_from, future_to):
    """調査の指示文。移行元 research_prompt.md の Step 1〜3 を写したもの。

    元は「Claude Code が手順書を読んで自分でHTMLまで作る」形だったが、
    こちらでは **Step 4（HTML生成）と Step 5（配信）は Python がやる**。
    AIに任せるのは調査と判定まで。理由は、テンプレートの差し替えと配信を
    検証可能にするため（AIが直接HTMLを書くと、12行あるかを機械で確かめられない）。

    **到達確認の結果を先に渡す**のが肝。「巡回できたことにして書く」余地を
    最初に潰しておく。後から検証で弾くより無駄が少ない。
    """
    series_lines = []
    for s in series:
        kw = " / ".join(s.get("search_keywords") or [])
        extra = ""
        if s.get("auto_judgment"):
            extra = f"　★自動{s['auto_judgment']}判定・カットデータ巡回はスキップ"
        series_lines.append(
            f"  {s['id']}. {s['name']}（{s.get('category')}）\n"
            f"      包含範囲: {s.get('scope')}\n"
            f"      検索語: {kw}\n"
            f"      備考: {s.get('notes')}{extra}")

    site_lines = []
    for r, s in zip(reach, sites):
        state = "到達OK（直接見てよい）" if r["ok"] else f"到達NG（{r['reason']}）"
        cats = s.get("category_urls") or {}
        cat = ("\n      カテゴリURL: "
               + " / ".join(f"{k}={v}" for k, v in cats.items())) if cats else ""
        site_lines.append(
            f"  優先{s.get('priority')} {s['name']}（{s['url']}・{s.get('role')}）: {state}\n"
            f"      {s.get('notes')}{cat}")

    watch_lines = "\n".join(f"  - {w['series']}: {w['name']}（{w['url']}）" for w in watch)
    ng = [r["name"] for r in reach if not r["ok"]]
    defaults = "\n".join(
        f"  {sid}: 新発売が無ければ {j}（{note}）" for sid, (j, note) in DEFAULT_JUDGMENT.items())

    return f"""あなたは FUN-CREATE株式会社の調査アシスタントです。
スキンシール（デバイス用カットデータ）展開判断のための週次調査を行い、
指定のJSONだけを返してください。基準日は {basis.isoformat()}（JST）です。

判断に迷っても人に確認は求めないでください（自動配信のため）。
確認できなかったことは「不明」と書けば、それが正しい答えです。

■ 調査する期間
  過去: {past_from.isoformat()} 〜 {basis.isoformat()}（発売済み・{PAST_DAYS}日）
  未来: {basis.isoformat()} 〜 {future_to.isoformat()}（発売予定・予約・{FUTURE_DAYS}日）

━━━━━━━━━━ Step 1: 新機種調査（12シリーズ） ━━━━━━━━━━
1件も省略しないこと。各シリーズの検索語で WebSearch し、窓内の発売情報について
製品名（型番含む）・発売日・メーカー公式URL・価格を拾う。
噂・リーク段階のものは「噂レベル」として分け、後段で WAIT 判定にする。

{chr(10).join(series_lines)}

ペンライト系（11・12）は **新製品が見つかっても自動的に REAL 判定**とし、
カットデータサイトの巡回はスキップする。公式の新発表だけ見ればよい:
{watch_lines}

━━━━━━━━━━ Step 2: カットデータ調達確認（6サイト） ━━━━━━━━━━
Step 1 で見つけた新機種について、**まず ARMobileSkin**（メイン）を確認し、
該当が無ければ代替サイト4社（VecRas / TEMPLAZ / ULTRASkins / Cutout.shop）を
優先度順に確認する。UltraGuardz は競合分析の参考で、調達先ではない。

  - 加熱式たばこ系（01〜04）は ARMobileSkin に少ないため、
    **VecRas E-Cigarette と Cutout.shop を優先**して確認する
  - ペンライト系（11・12）は巡回しない

{chr(10).join(site_lines)}

**調達先サイトには直接アクセスしないこと（WebFetch は禁止されています）**
  6サイトの中身は **WebSearch（検索インデックス）経由だけ**で確認する。
  商品ページを直接開こうとしても拒否されるので、試さずに検索で済ませること。
  直接アクセスは、このレポートを作る側が**冒頭の到達確認で1回だけ**行っている
  （その結果は上に書いてある）。

  理由: 短時間に何度も直接見に行った翌週から、**IP単位で 429 が返るようになった**。
  09-28 の調査で VecRas と TEMPLAZ を直接見たところ 10-05 から両方 429 になり、
  2週間解けなかった。10-05 に ARMobileSkin を17回見たら 10-12 に 429 になった。
  **1回の調査の便利さのために、翌週以降の判断材料を失う。**

  そのため、掲載の有無は via="index" が基本になる。
  検索で確かめられなければ hit="不明" / via="none" とすること。
  **見ていないものを見たことにしない。**

**約束（守れないときは、守れないと書く）**
  1. **到達NGのサイトを「直接見た」と書かないこと。** 今週の到達NGは
     {("、".join(ng) if ng else "ありません")}。
     検索インデックス（Google等）経由で掲載状況を推測した場合は via="index"、
     何も確認できなければ hit="不明" / via="none" とすること。
  2. 取得に失敗したサイトは、その機種の行に「要確認」として残し、処理は続ける。
     **そのサイトを根拠にした判定は作らない。**
  3. 確認できなかったことは「不明」と書く。それらしい数字や価格を作らない。
     分からないまま残した点は unverified 配列に1行ずつ書く。
     ただし**「どのサイトに到達できなかったか」は書かなくてよい。**
     こちらで実測してレポートに載せるので、書くと同じことが二度並ぶ。
     unverified に書くのは「調べたが確定できなかった中身」（掲載の有無・価格・
     発売日など）だけにすること。

━━━━━ Step 2.5: その発売に新しい型（カットデータ）が要るか ━━━━━
**判定より先にここを通す。**当社は「新しい型が必要なときにスキンシールを
新しく出す」運用なので、型の要らない発売はレポートに載せない
（2026-09-28 商品開発部の依頼・渡邊承認）。

発売1件ごと、および調達カード1件ごとに requires_new_mold と mold_reason を付ける。

  true（型が必要）
      新形状・新モデル・寸法が変わるもの。
      **色違い・柄違いでも外形寸法が変わるなら true。**
      例: Ploom CUBE の刷新は、旧アクセサリーとの互換性が失われている
          ＝外形寸法が変わっているので true

  false（型は不要）
      筐体形状が既存モデルと同一の、限定カラー／コラボデザイン／同梱版／記念版。
      例: ウルヴァリン限定版 PS5、GTA6 限定 DualSense、
          ゼルダ40周年 Switch 2、モンハン同梱 Switch 2

  "unknown"（判断できない）
      公表情報から寸法変更の有無が判断できないもの。
      **迷ったら unknown にすること。**unknown はレポートに載る。
      false は「筐体が同一だと確かに言える」ときだけ使う

**次の2つは false にしてはいけない（true か "unknown" のどちらかにする）:**

  (a) 加熱式たばこ系（01 アイコス・02 プルームテック・03 リルハイブリッド・04 グロー）
      限定色やデザイン違いであっても、**外形寸法の細部が変わることがある**。
      消してしまうと取り返しがつかないので、確信が無ければ "unknown" にする。
      例: IQOS ILUMA i リミックスモデルは色替えに見えるが、false にしない

  (b) 噂レベル・未発表のもの（bucket が rumor のもの、判定が WAIT のもの）
      形状が変わるかどうかを判断する材料がまだ無い。
      **「分からない」を「不要」と読み替えない。**必ず "unknown" にする

mold_reason には、そう判断した理由を1文で書く（false のものも必ず書く。
後から「なぜ消えたか」を辿るために保存される）。

**false と判断したものの扱い:**
  - releases にも cutdata にも**入れてよい**（こちらで保存し、表示だけ外す）
  - ただし **series の new_release 欄には書かないこと。**
    それしか無いシリーズは「―（該当発売なし）」と書き、既定判定に従う
  - **summary_paragraphs と chatwork_message でも触れないこと。**
    最大トピックに選ばない

━━━━━━━━━━ Step 3: 判定（BUY / ALT / REAL / WAIT） ━━━━━━━━━━
    新発売機種を発見
      ↓ ペンライト系か？        → YES: REAL（自動）
      ↓ 未発売・噂レベルか？    → YES: WAIT
      ↓ ARMobileSkin に該当あり？ → YES: BUY
      ↓ 代替サイト4社のいずれかに該当あり？ → YES: ALT
      ↓ すべて該当なし          → REAL（実機購入＋自社型起こし）

  BUY  … ARMobileSkin に掲載あり。既存メインサイトで購入し即着手できる
  ALT  … ARMobileSkin になし。代替サイトにあり。そちらで購入
  REAL … どのサイトにもカットデータなし。実機購入＋自社で型起こし
  WAIT … 未発売／噂レベルのため判断保留。発売後に再調査

どうしても決められないときだけ UNKNOWN を使い、judgment_note に
「何が確認できれば決まるか」を書く。

新発売が無かったシリーズの既定判定（**型が不要なものを除いた結果**
「該当発売なし」になった場合もこれに従う）:
{defaults}
  05〜10（Apple・ゲーム系）: new_release に「―（該当発売なし）」と書き、
      judgment は直近の調達可否に従う（ARM掲載済みシリーズなので通常 BUY）

■ 文章の書き方
  本文（summary_paragraphs / info / reason）では、強調したい語を **太字** で、
  特に読ませたい語を ==ハイライト== で囲んでよい。それ以外のHTMLタグは書かない
  （こちらでエスケープするため、そのまま文字として出てしまう）。

■ 返すもの（JSONオブジェクトだけ。前後に説明文やコードフェンスを付けない）
    summary_title        今週の要点を1行
    summary_paragraphs   3〜5段落。最重要から順に。段落ごとに1要素
    series               12件。id（"01"〜"12"）/ name / arm_status
                         (ok|partial|ng|none|unknown) / links[] / new_release /
                         new_release_note / judgment / judgment_note（全角14字程度）
    releases             発売情報カード。bucket は past（過去窓）/ future（未来窓）/
                         reference（窓外だが参考）/ rumor（噂レベル・WAIT）。
                         series / name / meta（発売日・価格）/ judgment /
                         judgment_note / info（本文2〜4文）/ links[] /
                         **requires_new_mold**(true|false|"unknown") / mold_reason
    cutdata              調達可否の表。heading（節見出し）/ num / name / judgment /
                         judgment_note / reason（判定理由）/
                         **requires_new_mold**(true|false|"unknown") / mold_reason /
                         rows[]（site / hit(あり|なし|要相談|不明) /
                         via(direct|index|none) / note / price / url）
    unverified           確認できなかった事項（0件なら空配列）
    chatwork_message     配信本文。1〜3文。次の形で書く:
                         「週次スキンシール展開判断レポート（{basis.year}/{basis.month}/{basis.day}基準）をお届けします。」
                         で始め、**今週の最大トピック**を判定つきで1件書き、
                         発売間近なら「あと◯日」と緊急度を添え、
                         「詳細は添付HTMLをご確認ください。」で締める。
                         装飾記法（** や ==）は使わない（そのまま文字として出る）
"""


def fetch_deny(sites_cfg) -> list:
    """調達先サイトへの WebFetch を禁じる指定を作る。

    **プロンプトで頼むだけでは守られない。**（2026-10-14）
    調査中の Claude が調達先を短時間に何度も直接見に行き、
    **その翌週からIP単位で 429 が返るようになった**:

        09-28 の調査で VecRas 5回・TEMPLAZ 1回 → 10-05 から両方 429（2週間解けない）
        10-05 の調査で ARMobileSkin 17回        → 10-12 に 429

    10-12 は6サイト中2サイトしか判定を作れていない。
    ブラウザ風UAの curl でも 429 なので、見ているのは UA ではなく IP。
    **ツール側で塞ぐ**（渡邊承認・ADR）。

    apex と サブドメインの両方を並べる。2026-10-14 に実測した CLI の挙動:

        domain:example.com     → example.com は拒否、www.example.com は**通る**
        domain:*.example.com   → www.example.com は拒否、example.com は**通る**

    片方だけでは穴が残るので必ず2つ出す。
    """
    out = []
    for s in sites_cfg:
        host = urllib.parse.urlsplit(s.get("url") or "").hostname or ""
        apex = host[4:] if host.startswith("www.") else host
        if not apex:
            continue
        for pat in (f"WebFetch(domain:{apex})", f"WebFetch(domain:*.{apex})"):
            if pat not in out:
                out.append(pat)
    return out


def claude_cmd(prompt, sites_cfg) -> list:
    """`claude -p` のコマンドラインを組む。**組み立てだけ**（起動しない）。

    分けてあるのは `--show-command` で目で見て確かめられるようにするため。
    禁止の指定を取り違えても静かに通ってしまう（実際に429を招いた）ので、
    **本番に出す前に引数を読めるようにしておく。**
    """
    # 禁止はカンマ区切りの1引数で渡す。空白区切りだと可変長引数が
    # 後続の --model まで飲み込む形になり得るため
    # --strict-mcp-config・--no-session-persistence は app/ai_cli.py と同じ（コネクタを読まない・履歴を残さない）
    return [CLAUDE_BIN, "-p", prompt,
            "--no-session-persistence", "--strict-mcp-config",
            "--output-format", "json",
            "--json-schema", json.dumps(SCHEMA, ensure_ascii=False),
            "--allowed-tools", "WebSearch", "WebFetch",
            "--disallowed-tools", ",".join(fetch_deny(sites_cfg)),
            "--model", MODEL]


def ask_claude(prompt, sites_cfg, log=print):
    """`claude -p` を起動して JSON を受け取る。

    cwd をテンポラリにするのは、レポート調査に NEW PRODUCT の CLAUDE.md を
    読ませないため（自動で拾われると指示が混ざる）。HOME はログインのある data/aihome。
    """
    cmd = claude_cmd(prompt, sites_cfg)
    with tempfile.TemporaryDirectory(prefix="skin-report-") as wd:
        try:
            env = {"HOME": AI_HOME, "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "ja_JP.UTF-8",
                   "ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
            p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                               timeout=TIMEOUT_SEC, env=env)
        except FileNotFoundError:
            raise Fail(2, f"{CLAUDE_BIN} が見つかりません（Claude Code CLI 未導入）")
        except subprocess.TimeoutExpired:
            raise Fail(2, f"調査が {TIMEOUT_SEC} 秒で終わりませんでした（claude -p）")
    if p.returncode != 0:
        raise Fail(2, f"claude -p が異常終了しました（exit {p.returncode}）: "
                      f"{(p.stderr or p.stdout or '')[:400]}")
    try:
        env = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise Fail(2, f"claude -p の出力がJSONではありません: {p.stdout[:400]}")
    if env.get("is_error"):
        raise Fail(2, f"調査が失敗しました（{env.get('subtype')}）: "
                      f"{'; '.join(env.get('errors') or [])[:400]}")
    # 費用は請求ではなく目安（サブスク認証のため）。それでも桁が跳ねたら気づけるよう残す
    log(f"  調査完了: {env.get('num_turns')}ターン / "
        f"{env.get('duration_ms', 0) / 1000:.0f}秒 / API換算 ${env.get('total_cost_usd', 0):.2f}")
    # **禁止が働いた回数を出す。**0件が続くなら指示文が効いている証拠、
    # 増えるなら調査が直接見に行こうとしている証拠。どちらも知りたい
    den = [d for d in env.get("permission_denials") or [] if d.get("tool_name") == "WebFetch"]
    if den:
        hosts = sorted({urllib.parse.urlsplit((d.get("tool_input") or {}).get("url") or "")
                        .hostname or "?" for d in den})
        log(f"  調達先への直接アクセスを {len(den)} 回止めました: {' / '.join(hosts)}")
    data = env.get("structured_output")
    if isinstance(data, dict):
        return data
    return _loads_loose(env.get("result") or "")


def _loads_loose(text):
    """コードフェンスや前置きが付いていても JSON を取り出す。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        i, j = text.find("{"), text.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                pass
    raise Fail(2, f"調査結果をJSONとして読めませんでした: {text[:400]}")


# ══════════════════════════════════════════════════════════════════
# ③ 検証
# ══════════════════════════════════════════════════════════════════
def name_mismatches(data, series_cfg):
    """AIが返したシリーズ名が yaml と違う箇所を拾う。**配信は止めない。**

    止めない理由は validate() のコメントのとおり。ただし黙って捨てると、
    AIが別のシリーズを混同していても気づけない（id は合っているのに中身が
    別物、という壊れ方は名前にしか出ない）。保存するJSONに残して、
    後から人が見られるようにしておく。
    """
    got = {str(s.get("id") or "").strip(): s for s in data.get("series") or []}
    out = []
    for cfg in series_cfg:
        s = got.get(cfg["id"])
        if s is None:
            continue
        name = str(s.get("name") or "").strip()
        if name and name != cfg["name"]:
            out.append({"id": cfg["id"], "expected": cfg["name"], "got": name,
                        # yaml 側の名前を含んでいれば言い換えの範囲。
                        # 含まないものは「別のシリーズかもしれない」ので目印を付ける
                        "contains_expected": cfg["name"] in name})
    return out


def mold_rule_violations(data, series_cfg):
    """「false にしてはいけないもの」に false が付いていないか。

    **これは配信を止めない。**`needs_mold()` が既に伏せずに載せているので、
    ここで止めても救えるものが無く、止めた分だけレポートが1本も出なくなる。
    2026-09-24 と 09-25 に、検証で配信が2度消えた（どちらも名前の照合）。
    **同じ形を作らない。**指示文が守られなかったことは記録と表示で残す。

    止める側に倒したくなったら、この戻り値を validate() の bad に足せばよい
    （1行。そうすると規則違反の週はレポートごと出なくなる）。
    """
    return [f"{o['kind']}「{o['name']}」に requires_new_mold=false が付いています"
            f"（{o['rule']}）。載せる側で扱いました"
            for o in mold_overrides(data, series_cfg)]


def validate(data, series_cfg, reach):
    """おかしなJSONで配信しないための関門。落ちた理由は全部返す。

    移行元の「実行完了の判定」（12行あるか・各機種に判定が付いているか）を
    機械で確かめる形にしたもの。加えて Auto GROWTH 側の約束を2つ足している:
      - 到達NGのサイトについて「直接見た」と言っていないか
      - ペンライト系（auto_judgment）が勝手に別の判定になっていないか
    """
    bad = []
    ng_sites = {r["name"] for r in reach if not r["ok"]}
    want = {s["id"]: s for s in series_cfg}

    for key in ("summary_title", "summary_paragraphs", "series", "releases", "cutdata"):
        if key not in data:
            bad.append(f"{key} がありません")
    if bad:
        return bad

    if not str(data.get("summary_title") or "").strip():
        bad.append("summary_title が空です")
    if not [p for p in data.get("summary_paragraphs") or [] if str(p).strip()]:
        bad.append("summary_paragraphs が空です")
    if not str(data.get("chatwork_message") or "").strip():
        # 移行元で「本文なしでファイルだけ届く」事故があった。ここで止める
        bad.append("chatwork_message が空です（本文なしで添付だけ送らない）")

    series = data.get("series") or []
    if len(series) != len(want):
        bad.append(f"シリーズが {len(series)} 件です（{len(want)} 件必要）")
    got = {str(s.get("id") or "").strip(): s for s in series}
    for sid, cfg in want.items():
        s = got.get(sid)
        if s is None:
            bad.append(f"シリーズ {sid}（{cfg['name']}）がありません")
            continue
        # **名前では落とさない。**（2026-09-25）
        # 識別子は id で、描画に使うのも yaml 側の名前。AIが返す name はどこにも
        # 使われないので、ここで照合しても何も守っていない。
        # 一方で落ちると週まるごと配信が消える。実際に2回とも配信を止めた:
        #   2026-09-24「アイコス（IQOS）」 … 英名の併記で完全一致に失敗
        #   2026-09-25「プルーム（Ploom）」 … 略称で部分一致にも失敗
        # 2回目で分かったのは、**AIの言い換え方を先回りして書き切れない**こと。
        # 照合を緩め続けるより、検証から外して name_mismatches() で記録に残す。
        j = s.get("judgment")
        if j not in JUDGMENTS and j != "UNKNOWN":
            bad.append(f"{cfg['name']}: 判定が {j!r}（BUY/ALT/REAL/WAIT のいずれかにする）")
        if j == "UNKNOWN" and not str(s.get("judgment_note") or "").strip():
            bad.append(f"{cfg['name']}: UNKNOWN なのに理由がありません")
        auto = cfg.get("auto_judgment")
        if auto and j != auto:
            bad.append(f"{cfg['name']}: 自動{auto}判定のシリーズですが {j!r} になっています")
        if s.get("arm_status") not in ARM_BADGE:
            bad.append(f"{cfg['name']}: arm_status が {s.get('arm_status')!r}")
    for sid in got:
        if sid not in want:
            bad.append(f"対象外のシリーズ id: {sid!r}")

    for r in data.get("releases") or []:
        if r.get("judgment") not in JUDGMENTS + ("UNKNOWN",):
            bad.append(f"発売情報 {r.get('name')!r}: 判定が {r.get('judgment')!r}")
        if r.get("bucket") not in ("past", "future", "reference", "rumor"):
            bad.append(f"発売情報 {r.get('name')!r}: bucket が {r.get('bucket')!r}")

    # ── 巡回できなかったサイトの判定を作っていないか ──
    for c in data.get("cutdata") or []:
        if c.get("judgment") not in JUDGMENTS + ("UNKNOWN",):
            bad.append(f"調達 {c.get('name')!r}: 判定が {c.get('judgment')!r}")
        for row in c.get("rows") or []:
            site = str(row.get("site") or "")
            if row.get("hit") not in ("あり", "なし", "要相談", "不明"):
                bad.append(f"調達 {c.get('name')!r}: {site} の該当が {row.get('hit')!r}")
            if row.get("via") == "direct":
                for ng in ng_sites:
                    if ng and ng.lower() in site.lower():
                        bad.append(
                            f"調達 {c.get('name')!r}: {site} は今週巡回できていないのに "
                            f"「直接確認した」となっています")
    return bad


# ══════════════════════════════════════════════════════════════════
# ④ HTML を作る（移行元 research_prompt.md Step 4）
# ══════════════════════════════════════════════════════════════════
def mold_state(item) -> str:
    """requires_new_mold を "true" / "false" / "unknown" に正規化する。

    **値が無いものは "unknown" 扱い＝残す。**
    この項目より前に保存されたJSONを読み直したとき、黙って全部消えてしまうと
    「直したら消えた」という最悪の壊れ方になる。**消すのは false と
    はっきり書いてあるときだけ。**
    """
    v = item.get("requires_new_mold", "unknown")
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).strip().lower()
    if s in ("true", "yes", "1"):
        return "true"
    if s in ("false", "no", "0"):
        return "false"
    return "unknown"


# ── 何があっても伏せないもの（2026-09-28 渡邊の追加指示）──
#
# **消したものは誰も見ない。**だから「消していいか怪しいもの」は残す側に倒す。
# 次の2つは requires_new_mold が false でも伏せない:
#
#   1. 加熱式たばこ系（01〜04）
#      限定色やデザイン違いでも**外形寸法が変わることがある**。
#      実例: IQOS ILUMA i リミックスモデルを 09-28 に false と判定したが、
#      残すのが正解だった。加熱式は false を許さず true か unknown だけにする
#   2. 噂レベル・未発表のもの
#      形状が変わるかどうかを判断する材料がまだ無い。
#      「分からない」を「不要」と読み替えてはいけない
#
# **指示文にも書いてあるが、それだけに頼らない。**AIが間違えた週に
# 静かに消えるのを、ここで止める。


def heated_tokens(series_cfg) -> set:
    """加熱式たばこ系を見分ける語を yaml から作る。**コードに直書きしない。**

    シリーズ名だけでは足りない（調達カードの名前は「IQOS ILUMA i（…）」のように
    ブランド名しか入らない）ので、`search_keywords` からも語を拾う。
    年号や「新機種」のような共通語は落とす。
    **取りこぼすより拾いすぎるほうが安全**な向きの判定なので、広めに取る。
    """
    toks = set()
    for s in series_cfg:
        if s.get("category") != "heated_tobacco":
            continue
        toks.add(s["name"])
        for kw in s.get("search_keywords") or []:
            for w in re.split(r"[\s/・]+", kw):
                if len(w) >= 3 and not re.search(r"\d", w) and w not in (
                        "新機種", "新型", "新発売"):
                    toks.add(w)
    return toks


def is_heated(item, series_cfg) -> bool:
    text = " ".join(str(item.get(k) or "") for k in ("series", "heading", "name"))
    if "加熱式たばこ" in text:
        return True
    return any(tok in text for tok in heated_tokens(series_cfg))


def is_unsettled(item) -> bool:
    """噂レベル・未発表か。WAIT は「未発売／噂レベルのため判断保留」の意味。"""
    return item.get("bucket") == "rumor" or item.get("judgment") == "WAIT"


def keep_regardless(item, series_cfg) -> str:
    """伏せてはいけない理由。該当しなければ空文字。"""
    if is_heated(item, series_cfg):
        return "加熱式たばこ系（限定色でも外形寸法が変わることがある）"
    if is_unsettled(item):
        return "噂レベル・未発表（形状が変わるか判断する材料がまだ無い）"
    return ""


def needs_mold(item, series_cfg) -> bool:
    """レポートに載せるか。**型が不要と明言されたものだけ落とす。**

    当社は「新しい型が必要なときにスキンシールを新しく出す」運用なので、
    型の要らない発売（限定カラー・コラボ・同梱版）は判断材料にならない。
    2026-09-28 商品開発部の依頼・渡邊承認。
    **注記も出さずに完全に消す**（同依頼）。消えた分は保存するJSONに残る。

    ただし上の2種類は false でも載せる（同日の渡邊の追加指示）。
    """
    if keep_regardless(item, series_cfg):
        return True
    return mold_state(item) != "false"


def visible(items, series_cfg):
    return [x for x in items or [] if needs_mold(x, series_cfg)]


def mold_overrides(data, series_cfg):
    """「false と書かれていたが伏せなかったもの」を拾う。

    AIが規則を外したことの記録。**配信は止めない**（止めても何も残らない）。
    保存するJSONと実行ログに出し、次に指示文を直すときの材料にする。
    """
    out = []
    for kind, items in (("releases", data.get("releases")),
                        ("cutdata", data.get("cutdata"))):
        for it in items or []:
            why = keep_regardless(it, series_cfg)
            if why and mold_state(it) == "false":
                out.append({"kind": kind, "name": it.get("name"), "rule": why,
                            "mold_reason": it.get("mold_reason") or ""})
    return out


def _esc(s):
    return html.escape(str(s or ""), quote=True)


def _inline(s):
    """本文の軽い装飾だけ通す。

    **AIが返した文字列をそのままHTMLに入れない。**必ずエスケープしてから
    **強調** と ==ハイライト== の2つだけタグへ戻す。生HTMLを許すと、
    崩れたタグ1つでレポート全体が壊れる。
    """
    t = _esc(s)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"==(.+?)==", r'<em class="hl">\1</em>', t)
    return t.replace("\n", "<br>")


def _link_btns(links, sep="<br>"):
    out = []
    for l in links or []:
        url = str(l.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue                      # javascript: 等を入れない
        out.append(f'<a href="{_esc(url)}" target="_blank" rel="noopener" '
                   f'class="link-btn">{_esc(l.get("label") or url)}</a>')
    return sep.join(out)


def _badge(j):
    j = j if j in BADGE_CLASS else "UNKNOWN"
    label = j if j != "UNKNOWN" else "判定なし"
    return f'<span class="action-badge {BADGE_CLASS[j]}">▸ {_esc(label)}</span>'


def _judgment_block(j, note):
    note = note or JUDGMENT_MEANING.get(j, "")
    return (f'<div class="judgment-block">{_badge(j)}'
            f'<span class="judgment-meaning">{_esc(note)}</span></div>')


def _summary(data):
    return "\n".join(f"    <p>{_inline(p)}</p>"
                     for p in data.get("summary_paragraphs") or [] if str(p).strip())


def _matrix_rows(data, series_cfg):
    """必ず12行出す。**新発売なし・巡回スキップでも省略しない。**（移行元 Step 4）"""
    got = {str(s.get("id") or "").strip(): s for s in data.get("series") or []}
    rows = []
    for cfg in series_cfg:
        s = got.get(cfg["id"], {})
        cls, label = ARM_BADGE.get(s.get("arm_status"), ARM_BADGE["unknown"])
        nr = _inline(s.get("new_release") or "確認できませんでした")
        if s.get("new_release_note"):
            nr += (f'<br><span style="font-size:11px;color:var(--muted);">'
                   f'{_inline(s["new_release_note"])}</span>')
        rows.append(f"""        <tr>
          <td class="col-num">{_esc(cfg["id"])}</td>
          <td>{_esc(cfg["name"])}</td>
          <td><span class="badge {cls}">{_esc(label)}</span></td>
          <td>{_link_btns(s.get("links")) or "—"}</td>
          <td>{nr}</td>
          <td>
            {_judgment_block(s.get("judgment"), s.get("judgment_note"))}
          </td>
        </tr>""")
    return "\n".join(rows)


def _release_cards(data, bucket, series_cfg):
    cards, n = [], 0
    for r in visible(data.get("releases"), series_cfg):
        if r.get("bucket") != bucket:
            continue
        n += 1
        j = r.get("judgment") if r.get("judgment") in BADGE_CLASS else "UNKNOWN"
        num = f"{bucket.upper()}-{n:02d} ／ {_esc(r.get('series') or '')}"
        sub = (f'<span class="ac-sub">{_inline(r["meta"])}</span>' if r.get("meta") else "")
        btns = _link_btns(r.get("links"), sep="\n      ")
        cards.append(f"""    <div class="action-card act-{BADGE_CLASS[j]}">
      <div class="ac-header">
        <div class="ac-title-block">
          <div class="ac-num">{num}</div>
          <h3 class="ac-name">{_esc(r.get("name"))}</h3>
          {sub}
        </div>
        {_judgment_block(r.get("judgment"), r.get("judgment_note"))}
      </div>
      <div class="ac-info">{_inline(r.get("info"))}</div>
      {btns}
    </div>""")
    if not cards:
        cards.append("    <p>該当なし</p>")      # 移行元の指定どおり
    return "\n\n".join(cards)


def _cutdata_cards(data, series_cfg):
    blocks = []
    for c in visible(data.get("cutdata"), series_cfg):
        j = c.get("judgment") if c.get("judgment") in BADGE_CLASS else "UNKNOWN"
        rows = []
        for row in c.get("rows") or []:
            hit = row.get("hit")
            cls = {"あり": "ok-text", "なし": "ng-text"}.get(hit, "partial-text")
            note = (_link_btns([{"label": row.get("note") or row.get("url"), "url": row["url"]}])
                    if row.get("url") else _inline(row.get("note")))
            via = ""
            if row.get("via") == "index":
                via = ('<br><span style="font-size:10px;color:var(--muted);">'
                       '検索インデックス経由（直接巡回はできていません）</span>')
            elif row.get("via") == "none":
                via = ('<br><span style="font-size:10px;color:var(--muted);">'
                       '確認できませんでした</span>')
            rows.append(f'          <tr><td>{_esc(row.get("site"))}</td>'
                        f'<td><span class="{cls}">{_esc(hit)}</span></td>'
                        f'<td>{note}{via}</td><td>{_esc(row.get("price") or "―")}</td></tr>')
        blocks.append(f"""    <h2 class="section-title">{_esc(c.get("heading"))}</h2>
    <div class="action-card act-{BADGE_CLASS[j]}">
      <div class="ac-header">
        <div class="ac-title-block">
          <div class="ac-num">{_esc(c.get("num") or "")}</div>
          <h3 class="ac-name">{_esc(c.get("name"))}</h3>
        </div>
        {_judgment_block(c.get("judgment"), c.get("judgment_note"))}
      </div>
      <div class="ac-info"><strong>判定理由：</strong>{_inline(c.get("reason"))}</div>
      <table class="cutdata">
        <thead><tr><th>サイト</th><th style="width:12%;">該当</th><th>商品ページ</th><th style="width:18%;">価格</th></tr></thead>
        <tbody>
{chr(10).join(rows)}
        </tbody>
      </table>
    </div>""")
    if not blocks:
        blocks.append("    <p>該当なし</p>")
    return "\n\n".join(blocks)


def _site_status_rows(reach):
    rows = []
    for r in reach:
        cls = "ok-text" if r["ok"] else "ng-text"
        state = "到達OK" if r["ok"] else "確認できず"
        why = r["reason"] or ""
        rows.append(
            f'        <tr><td><strong>{_esc(r["name"])}</strong></td>'
            f'<td><a href="{_esc(r["url"])}" target="_blank" rel="noopener" '
            f'class="link-btn">{_esc(r["url"].split("//")[-1].strip("/"))}</a></td>'
            f'<td><span class="{cls}">{_esc(state)}</span></td>'
            f'<td>{_esc(why + ("／" if why else "") + r["notes"])}</td></tr>')
    return "\n".join(rows)


def _unreachable_notice(reach, unverified):
    """巡回できなかったサイトと、確認できなかった事項を明記する。

    **全て巡回できた週は空にする。**毎週同じ注意書きが出ていると、
    本当に巡回できなかった週に誰も気づかなくなる。

    **2つを混ぜない。**（2026-09-24 の初回実走で混ざっていた）
      A. 到達できなかったサイト … そのサイトを根拠にした判定は作っていない
      B. 調べたが確定できなかった事項 … 判定は作ったが、詰め切れていない点
    ひとまとめにして「以下は判定を作っていません」と書くと、B にある
    「価格が分からなかった」まで判定を作っていないように読める。嘘になる。
    """
    ng = [r for r in reach if not r["ok"]]
    unverified = _dedupe_unverified(unverified, ng)
    if not ng and not unverified:
        return ""
    parts = []
    if ng:
        items = "\n".join(
            f'      <li><strong>{_esc(r["name"])}</strong>（{_esc(r["url"])}）: '
            f'{_esc(r["reason"] or "到達できませんでした")}</li>' for r in ng)
        parts.append(
            f"    <strong>巡回できなかったサイト（{len(ng)}件）。"
            f"このサイトを根拠にした判定は作っていません。</strong>\n"
            f"    <ul>\n{items}\n    </ul>")
    if unverified:
        items = "\n".join(f"      <li>{_inline(u)}</li>" for u in unverified)
        parts.append(
            f"    <strong>調べたが確定できなかった事項（{len(unverified)}件）。"
            f"判定は出していますが、下記は裏付けが取れていません。</strong>\n"
            f"    <ul>\n{items}\n    </ul>")
    return ("""  <div class="notice">
    <div class="notice-title">確認できませんでした</div>
"""
            + "\n".join(parts) + """
  </div>""")


def _dedupe_unverified(unverified, ng):
    """到達できなかったサイトの話が unverified にも書かれていたら落とす。

    AIは律儀に「ARMobileSkin は403で見られなかった」を unverified にも書く。
    こちらの実測と二重に並ぶので、サイト名とURLだけの重複行は落とす。
    **中身のある記述（何が確認できなかったか）は残す。**
    """
    out = []
    for u in unverified or []:
        u = str(u).strip()
        if not u:
            continue
        dup = False
        for r in ng:
            # 「そのサイトに到達できなかった」だけを述べている行を落とす
            if r["name"] in u and (r["url"] in u or "到達" in u or "ブロック" in u
                                   or "解決" in u or str(r["status"] or "") in u):
                if not any(k in u for k in ("掲載", "価格", "在庫", "確認が必要", "未確認",
                                            "推定", "分析")):
                    dup = True
                    break
        if not dup:
            out.append(u)
    return out


def render(data, series_cfg, reach, basis, past_from, future_to):
    """テンプレートの目印を差し替える。目印が残っていたら止める。

    プレースホルダー名は移行元 templates/report_template.html のまま
    （REPORT_DATE_JA / PAST_RANGE / KPI_BUY …）。勝手に改名しない。
    """
    if not TEMPLATE.is_file():
        raise Fail(1, f"テンプレートがありません: {TEMPLATE}")
    t = TEMPLATE.read_text(encoding="utf-8")
    # 先頭の説明コメントは配布物に要らない（目印の一覧なので、残すと紛らわしい）
    t = re.sub(r"^<!DOCTYPE html>\n<!--.*?-->\n", "<!DOCTYPE html>\n", t, count=1, flags=re.S)

    counts = {j: 0 for j in JUDGMENTS}
    for s in data.get("series") or []:
        if s.get("judgment") in counts:
            counts[s["judgment"]] += 1
    # **表示するものだけ数える。**消したものを数に残すと、
    # 「8件」と出ているのにカードが5枚しかない、という食い違いになる
    n_new = len([r for r in visible(data.get("releases"), series_cfg)
                 if r.get("bucket") in ("past", "future")])

    values = {
        "REPORT_DATE_JA": _ja_date(basis),
        "REPORT_DATE_ISO": basis.isoformat(),
        "REPORT_DATE_YMD": f"{basis:%Y%m%d}",
        "PAST_RANGE": f"{past_from:%Y/%m/%d} 〜 {basis:%m/%d}",
        "FUTURE_RANGE": f"{basis:%Y/%m/%d} 〜 {future_to:%Y/%m/%d}",
        "SUMMARY_TITLE": _esc(data.get("summary_title")),
        "KPI_BUY": str(counts["BUY"]),
        "KPI_ALT": str(counts["ALT"]),
        "KPI_REAL": str(counts["REAL"]),
        "KPI_NEW": str(n_new),
        "TAB_RELEASE_COUNT": f"{n_new}件",
        # **ラベルだけ実態に合わせる。数え方は変えない。**
        # 数えているのは past + future バケットの合計で、窓は過去30日／今後180日。
        # 「過去/未来1か月」は移行元から引き継いだ誤りだった（2026-09-25 指摘）。
        # 発売日が自由記述なので ±1か月では数え直せない。**数字を動かすと根拠が消える**ので、
        # 合わせるのはラベルの側にする。窓を環境変数で変えても釣られて直るようにしてある
        "KPI_NEW_LABEL": f"新発売（過去{PAST_DAYS}日／今後{FUTURE_DAYS}日）",
    }
    blocks = {
        "UNREACHABLE_NOTICE": _unreachable_notice(reach, data.get("unverified")),
        "SUMMARY_PARAGRAPHS": _summary(data),
        "MATRIX_ROWS": _matrix_rows(data, series_cfg),
        "RELEASE_PAST_CARDS": _release_cards(data, "past", series_cfg),
        "RELEASE_FUTURE_CARDS": _release_cards(data, "future", series_cfg),
        "RELEASE_REF_CARDS": _release_cards(data, "reference", series_cfg),
        "RELEASE_RUMOR_CARDS": _release_cards(data, "rumor", series_cfg),
        "CUTDATA_CARDS": _cutdata_cards(data, series_cfg),
        "SITE_STATUS_ROWS": _site_status_rows(reach),
    }
    for name, body in blocks.items():
        pat = re.compile(r"<!-- INSERT:%s_START -->.*?<!-- INSERT:%s_END -->" % (name, name), re.S)
        t, n = pat.subn(lambda m, b=body: b, t)
        if n != 1:
            raise Fail(1, f"テンプレートの目印 INSERT:{name} が {n} 件です")
    for k, v in values.items():
        t = t.replace("{{ %s }}" % k, v)

    left = re.findall(r"\{\{ *[A-Z_]+ *\}\}|<!-- INSERT:[A-Z_]+_(?:START|END) -->", t)
    if left:
        raise Fail(1, "差し替えきれていない目印があります: " + " / ".join(sorted(set(left))))
    return t


def _ja_date(d):
    return f"{d.year}年{d.month}月{d.day}日（{'月火水木金土日'[d.weekday()]}）"


# ══════════════════════════════════════════════════════════════════
# ⑤ ChatWork へファイル添付で送る（移行元 src/chatwork_sender.py の作法）
# ══════════════════════════════════════════════════════════════════
def _chatwork_token():
    """トークンの読み出しは app/chatwork.py の作法をそのまま使う（FUN-CREATE BOT）。

    同じ読み方を2箇所に書くと、片方だけ直したときに「BOTのはずが個人トークンで
    送られていた」が起きる。**写さずに呼ぶ。**app/chatwork は名前解決を変えない
    （こちらから向こうを import するのはよい。逆はしない）。
    """
    sys.path.insert(0, str(BASE))
    from app import chatwork                    # noqa: E402
    try:
        return chatwork._read_token(), str(chatwork.token_path())
    except chatwork.NotConfigured as e:
        raise Fail(1, str(e))


def warn_if_no_mention(raw=MENTION_TO, out=sys.stderr):
    """宛先が未設定なら警告する。**ただし配信は止めない。**

    止めるか警告に留めるかを考えた結果、**警告に留めた**:

      - 宛先が無くても、本文と添付は部屋に届く。読む人はいる
      - 止めると**誰にも届かない**。失うものが「個人通知が飛ばない」より大きい
      - [To:] の欠落は受け取る側が気づける（部屋には出ている）。
        対して配信そのものが消えると、気づけるのは「今週来ていない」と
        思ったときで、たいてい火曜以降になる

    ただし黙って落とすのはしない。**調査の前**に出すので、手で走らせた人は
    8分待つ前に気づく。systemd の journal にも残り、保存するJSONにも記録する。
    """
    if mention_prefix(raw):
        return False
    print("⚠️ 宛先（SKIN_REPORT_MENTION_TO）が未設定です。"
          "本文と添付は届きますが [To:] が付きません。\n"
          "   PC版は石原さん・増地さんを宛先にしていました。"
          "ユニットに次を入れてください（引用符が要ります）:\n"
          '   Environment="SKIN_REPORT_MENTION_TO=5865584:石原 千恵子,6224035:増地 洋子"',
          file=out)
    return True


def mention_prefix(raw=MENTION_TO):
    """"5865584:石原 千恵子,6224035:増地 洋子" → "[To:5865584] 石原 千恵子さん\\n…"

    移行元 utils.parse_mention_to と同じ書式・同じ寛容さ（壊れた項目は無視して
    配信自体は止めない）。未設定なら空文字で、本文だけが送られる。
    """
    lines = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        account_id, _, name = item.partition(":")
        account_id, name = account_id.strip(), name.strip()
        if not account_id.isdigit():
            continue
        lines.append(f"[To:{account_id}] {name}さん" if name else f"[To:{account_id}]")
    return "\n".join(lines) + "\n" if lines else ""


def _multipart(path, message):
    """multipart/form-data を標準ライブラリで組む（requests を増やさないため）。

    移行元は requests の files= に任せていた。ファイル名は日本語のまま
    UTF-8 で載せる（移行元と同じ扱い。requests もそうしていた）。
    """
    boundary = "----newproduct" + os.urandom(12).hex()
    b = boundary.encode()
    parts = []
    if message:
        parts.append(b"--" + b + b"\r\n"
                     b'Content-Disposition: form-data; name="message"\r\n'
                     b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
                     + message.encode("utf-8") + b"\r\n")
    parts.append(b"--" + b + b"\r\n"
                 b'Content-Disposition: form-data; name="file"; filename="'
                 + path.name.encode("utf-8") + b'"\r\n'
                 b"Content-Type: text/html; charset=utf-8\r\n\r\n"
                 + path.read_bytes() + b"\r\n")
    parts.append(b"--" + b + b"--\r\n")
    return b"".join(parts), boundary


def send_file(path, message, room_id=ROOM_ID, log=print):
    """POST /v2/rooms/{room_id}/files。失敗は最大3回まで再試行する。

    再試行の作法は移行元 src/chatwork_sender.py に合わせた（動作実績がある側へ寄せる）:
      - 4xx（429を除く）は**再試行しない**。トークンや部屋IDの誤りは待っても直らない
      - 5xx・429・通信エラーは再試行。待ちは 2 → 5 → 10 秒
      - 3回失敗したら理由つきで落とす。**ローカルのHTMLは残す**（手動再送できる）
    """
    tok, src = _chatwork_token()
    data, boundary = _multipart(path, message)
    last = ""
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(
            f"https://api.chatwork.com/v2/rooms/{room_id}/files",
            data=data, method="POST",
            headers={"X-ChatWorkToken": tok,
                     "Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Content-Length": str(len(data))})
        log(f"  送信試行 {attempt}/{MAX_RETRIES}: room_id={room_id} / {path.name}")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = (r.read() or b"").decode("utf-8", "replace")
                try:
                    return json.loads(body).get("file_id")
                except json.JSONDecodeError:
                    return None
        except urllib.error.HTTPError as e:
            detail = (e.read() or b"").decode("utf-8", "replace")[:300]
            last = f"HTTP {e.code}: {detail}"
            if 400 <= e.code < 500 and e.code != 429:
                raise Fail(4, f"ChatWorkのクライアントエラー（トークン: {src} / "
                              f"ルーム: {room_id}）。再試行しません: {last}")
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < MAX_RETRIES:
            wait = RETRY_BACKOFF[attempt - 1]
            log(f"  送信失敗（{last}）。{wait}秒待って再試行します")
            time.sleep(wait)
    raise Fail(4, f"ChatWorkへの添付送信が {MAX_RETRIES} 回失敗しました: {last}")


# ══════════════════════════════════════════════════════════════════
# main
# ══════════════════════════════════════════════════════════════════
def save_raw(out_dir, basis, data, reach, suffix="", mention_missing=False,
             mismatches=None, overrides=None, hidden=None):
    """調査結果を、検証より先に残す。

    **検証に落ちた週こそ中身を見たい。**ここで落とすと、何が悪かったのか
    追えないうえ、直したあと調査からやり直しになる（実測で8分かかる）。
    2026-09-24 の初回実走で実際に捨ててしまったので、保存を前に出した。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"skin_report_{basis:%Y%m%d}{suffix}.json"
    p.write_text(json.dumps({"basis": basis.isoformat(), "reach": reach, "data": data,
                             "mention_missing": mention_missing,
                             "name_mismatches": mismatches or [],
                             # 「型は不要」と書かれたが規則で伏せなかったもの
                             "mold_overrides": overrides or [],
                             # **実際にレポートから伏せたもの。**
                             # /skin-report はこれを読む（規則を写さずに済む）
                             "mold_hidden": hidden or []},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="スキンシール展開判断レポート（週次）")
    ap.add_argument("--send", action="store_true",
                    help="ChatWork へ添付送信する（付けなければ dry-run）")
    ap.add_argument("--json", type=Path,
                    help="調査済みJSONを読む（claude -p を呼ばずに描画だけ試す）")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help=f"出力先（既定 {OUT_DIR}）")
    ap.add_argument("--room-id", default=ROOM_ID)
    ap.add_argument("--basis", help="基準日 YYYY-MM-DD（既定は今日・JST）")
    ap.add_argument("--show-command", action="store_true",
                    help="claude -p に渡す引数を表示して終わる（調査は行わない）")
    args = ap.parse_args(argv)

    basis = date.fromisoformat(args.basis) if args.basis else datetime.now(JST).date()
    past_from = basis - timedelta(days=PAST_DAYS)
    future_to = basis + timedelta(days=FUTURE_DAYS)

    try:
        print(f"■ スキンシール週次レポート（基準日 {basis}）")
        # 調査（8分）を始める前に出す。手で走らせた人がすぐ気づけるように
        mention_missing = warn_if_no_mention()
        series_cfg, sites_cfg, watch = load_config()
        print(f"  設定: {len(series_cfg)}シリーズ / {len(sites_cfg)}サイト"
              f"（{SERIES_YAML.name} ・ {SITES_YAML.name}）")

        if args.show_command:
            # **禁止の指定は、間違っていても静かに通る。**
            # 2026-10-14 の 429 はそれで起きた（プロンプトで頼んでいただけ）。
            # 本番へ出す前に、渡す引数を目で読めるようにしておく。
            # 到達確認はしない（表示だけのために6サイトを叩かない）
            stub = [dict(s, status=None, ok=True, reason="",
                         notes=s.get("notes") or "") for s in sites_cfg]
            cmd = claude_cmd(build_prompt(series_cfg, sites_cfg, watch, stub,
                                          basis, past_from, future_to), sites_cfg)
            print("\n■ claude -p に渡す引数")
            for i, a_ in enumerate(cmd):
                if len(a_) > 160:
                    print(f"  [{i}] <{len(a_):,}文字> {a_.replace(chr(10), ' ')[:110]}…")
                else:
                    print(f"  [{i}] {a_}")
            deny = fetch_deny(sites_cfg)
            print(f"\n■ WebFetch を禁止するドメイン（{len(deny)}件・"
                  "apex と サブドメインの両方を並べる）")
            for d in deny:
                print(f"  {d}")
            return 0

        loaded = None
        if args.json:
            loaded = json.loads(args.json.read_text(encoding="utf-8"))

        # ── 保存した封筒を読み直すときは、**そのときの到達状況をそのまま使う** ──
        # 測り直すと、調査は「Cutout.shop は確認できず」と書いてあるのに
        # 到達表は「到達OK」になる、という食い違ったレポートが出る
        # （2026-09-25 の結果を翌日に読み直して実際に出た）。
        # レポートは**調査した時点の状況**を述べるものなので、保存された側を正とする。
        saved_reach = (loaded or {}).get("reach") if isinstance(loaded, dict) else None
        if saved_reach:
            reach = saved_reach
            print(f"① 到達確認は {args.json.name} に保存された結果を使います"
                  f"（測り直しません）")
            for r in reach:
                mark = "✓" if r.get("ok") else "✗"
                print(f"  {mark} {r.get('name'):<14} {r.get('reason') or 'HTTP %s' % r.get('status')}")
        else:
            print("① 6サイトの到達確認")
            reach = reach_check(sites_cfg)
            for r in reach:
                mark = "✓" if r["ok"] else "✗"
                why = r["reason"] or f"HTTP {r['status']}"
                print(f"  {mark} {r['name']:<14} {why}")
            if not any(r["ok"] for r in reach):
                raise Fail(1, "6サイトすべてに到達できませんでした。"
                              "VPSの外向き通信を確認してください（レポートは作りません）")

        if args.json:
            print(f"② 調査は省略（{args.json} を読みます）")
            # 保存したファイル（basis/reach/data の封筒）でも、素のJSONでも読める
            data = loaded.get("data") if isinstance(loaded.get("data"), dict) else loaded
        else:
            print(f"② claude -p で調査（model={MODEL} / 最大{TIMEOUT_SEC}秒）")
            data = ask_claude(build_prompt(series_cfg, sites_cfg, watch, reach,
                                           basis, past_from, future_to), sites_cfg)

        # 検証より先に残す。落ちた週も --json で読み直せるようにするため
        mismatches = name_mismatches(data, series_cfg)
        hidden = [{"kind": kind, "name": x.get("name")}
                  for kind, items in (("releases", data.get("releases")),
                                      ("cutdata", data.get("cutdata")))
                  for x in items or [] if not needs_mold(x, series_cfg)]
        raw = save_raw(args.out, basis, data, reach, mention_missing=mention_missing,
                       mismatches=mismatches,
                       overrides=mold_overrides(data, series_cfg),
                       hidden=hidden)
        print(f"  調査結果を保存: {raw}")
        if mismatches:
            # 配信は止めない。言い換えの範囲か、別物を指していそうかだけ添える
            odd = [m for m in mismatches if not m["contains_expected"]]
            print(f"  （シリーズ名が yaml と違うもの {len(mismatches)}件"
                  f"{f'／うち{len(odd)}件は別シリーズの可能性' if odd else ''}: "
                  + " / ".join(f"{m['id']} {m['got']}→{m['expected']}"
                               for m in mismatches[:4])
                  + ("…" if len(mismatches) > 4 else "") + "）")

        print("③ 返ってきたJSONを検証")
        for v in mold_rule_violations(data, series_cfg):
            # 止めない。止めるとレポートが1本も出ず、救えるものが無い
            print(f"  ⚠ {v}", file=sys.stderr)
        bad = validate(data, series_cfg, reach)
        if bad:
            raise Fail(3, "検証に落ちました（配信しません）:\n  - " + "\n  - ".join(bad)
                          + f"\n  調査結果は {raw} に残してあります"
                          + "（直したら --json で読み直せます。調査のやり直しは不要）")
        rel, cut = data.get("releases") or [], data.get("cutdata") or []
        hid_r = [r for r in rel if not needs_mold(r, series_cfg)]
        hid_c = [c for c in cut if not needs_mold(c, series_cfg)]
        assert len(hid_r) + len(hid_c) == len(hidden)    # 保存した内容と食い違わせない
        overrides = mold_overrides(data, series_cfg)
        if overrides:
            # **規則で救ったもの。**AIが「型は不要」と書いたが伏せなかった
            print(f"  規則により伏せなかったもの {len(overrides)}件"
                  f"（加熱式たばこ系／噂・未発表は false でも載せる）:")
            for o in overrides:
                print(f"    - {o['name']}（{o['rule']}）")
        print(f"  ✓ 12シリーズ / 発売{len(rel)}件 / 調達{len(cut)}件")
        if hid_r or hid_c:
            # **レポートには1行も出さない**（渡邊判断）。ここと保存するJSONにだけ残す
            print(f"  型の新設が不要なため伏せたもの: 発売{len(hid_r)}件 / 調達{len(hid_c)}件")
            for x in hid_r + hid_c:
                print(f"    - {x.get('name')}（{x.get('mold_reason') or '理由の記載なし'}）")
            print(f"  → 表示は 発売{len(rel) - len(hid_r)}件 / 調達{len(cut) - len(hid_c)}件"
                  f"（伏せた分は保存するJSONに残っています）")

        print("④ HTMLを生成")
        out_html = render(data, series_cfg, reach, basis, past_from, future_to)
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"スキンシール展開判断レポート_{basis:%Y%m%d}.html"
        path.write_text(out_html, encoding="utf-8")
        print(f"  ✓ {path}（{len(out_html):,}バイト）")

        ng = [r["name"] for r in reach if not r["ok"]]
        message = mention_prefix() + str(data.get("chatwork_message") or "").strip()
        if ng:
            message += (f"\n※ 今週巡回できなかったサイト: {'、'.join(ng)}"
                        f"（該当分の判定は作っていません）")

        print("⑤ ChatWork へ送信" if args.send else "⑤ dry-run のため送信しません")
        if args.send and mention_missing:
            # 直前にもう一度。送信のログと並べて残したい
            warn_if_no_mention()
        if not args.send:
            print("--- 送信予定の本文 ---")
            print(message)
            return 0
        file_id = send_file(path, message, args.room_id)
        print(f"  ✓ 送信しました（file_id={file_id}）")
        return 0

    except Fail as e:
        print(f"⚠️ {e}", file=sys.stderr)
        if e.code == 4:
            print("   ローカルのHTMLは残っています。手動で再送できます", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
