#!/usr/bin/env python3
"""
週次レポートを Windows から自動で受け取る（2026-11-10 石原さんの追加修正依頼書 2-5・十文字さんの選択・ADR-138）。

- 社内の Windows のタスクスケジューラが、週次レポートのフォルダーの `週次売上分析_*.xlsx` を送ってくる
  （`deploy/windows/send_weekly_report.ps1`）。**初回は保存済みの過去分も全部**送られる
- 口は `POST /api/feed/weekly-report`（外から届く・Caddy 経由）。**専用の鍵** `config/weekly_feed_token`
  （このファイルだけ・他の鍵と分ける）。届くのは週次売上分析の Excel だけ（明細の Excel は名前で断る）
- 取り込みは今の `newprod.ingest`（「新商品」シートの集計だけを読む・ファイルは保存しない）
- **二重に数えない**: 同じファイル（sha256）は取り込まない。同じ期間で中身が違えば訂正版として入れ替える。
  期間の違うレポートは別々に持ち、画面は最新の期間の1件だけを出す（累計や重なる期間を足さない）
- 受け取った結果（取り込み・同じ内容・訂正版・エラーと理由）を `weekly_feed_log` に残し、画面に最終成功日時と失敗を出す。
  失敗したファイルは「取り込み済み」にならないので、Windows の次の実行で自動で送り直される
"""
from __future__ import annotations

import hashlib
import re

from app import store

PREFIX = "週次売上分析_"
MAX_BYTES = 8 * 1024 * 1024
OK_RESULTS = ("取り込み", "訂正版で入れ替え", "取り込み済み（同じ内容）")


def _log(name, sha, pf, pt, result, error, source):
    with store.tx() as c:
        c.execute("INSERT INTO weekly_feed_log (file_name,sha256,period_from,period_to,result,error,source,received_at) "
                  "VALUES (?,?,?,?,?,?,?,?)", (name[:200], sha, pf, pt, result, (error or None) and error[:300],
                                               source[:60], store.now_s()))


def sheet_names(data: bytes) -> list[str]:
    """xlsx のシート名（workbook.xml）。壊れていれば ValueError"""
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            x = z.read("xl/workbook.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("Excel（xlsx）として読めません") from None
    import html
    return [html.unescape(n) for n in re.findall(r'<sheet\b[^>]*\bname="([^"]*)"', x)]


def receive(name: str, data: bytes, source: str, user_id: str, source_sha: str | None = None) -> dict:
    """1ファイル受け取る。結果を必ず記録する。取り込めなければ ValueError（理由つき）

    Windows は**集計だけの写し**（シート「新商品」だけ・明細の行を消したもの）を送る（2026-11-11 十文字さんの選択）。
    写しは毎回中身の指紋が変わるので、二重防止は**元のファイルの指紋**（source_sha）で行う。"""
    from app import newprod
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    sha = hashlib.sha256(data or b"").hexdigest()
    if source_sha and re.fullmatch(r"[0-9a-f]{64}", source_sha):
        sha = source_sha
    pf = pt = None
    try:
        if not name.startswith(PREFIX) or not name.lower().endswith(".xlsx") or name.startswith("~$"):
            raise ValueError(f"受け取るのは「{PREFIX}YYYYMMDD-YYYYMMDD.xlsx」だけです（{name or '名前なし'}）")
        if not data:
            raise ValueError("中身が空です")
        if len(data) > MAX_BYTES:
            raise ValueError(f"大きすぎます（{len(data) // 1024} KB）")
        pf, pt = newprod.period_of(name)
        if source == "windows" and sheet_names(data) != ["新商品"]:
            raise ValueError("集計だけの写し（シート「新商品」だけ）ではありません。明細を含むファイルは受け取りません"
                             "（送る側の send_weekly_report.ps1 を新しい版に）")
        if store.one("SELECT 1 FROM weekly_feed_log WHERE sha256=? AND result IN (?,?,?)", (sha, *OK_RESULTS)):
            _log(name, sha, pf, pt, "取り込み済み（同じ内容）", None, source)
            return {"ok": True, "result": "取り込み済み（同じ内容）", "period_from": pf, "period_to": pt}
        had = store.one("SELECT 1 FROM newprod_weekly WHERE period_from=? AND period_to=?", (pf, pt))
        r = newprod.ingest(name, data, user_id)          # 同じ期間は入れ替え（足さない）
        result = "訂正版で入れ替え" if had else "取り込み"
        _log(name, sha, pf, pt, result, None, source)
        return {**r, "result": result}
    except ValueError as e:
        _log(name or "（名前なし）", sha, pf, pt, "エラー", str(e), source)
        raise


def known() -> list[str]:
    """取り込み済み（同じ内容を含む）のファイルの sha256。Windows はこれに無いものだけ送る"""
    return [r["sha256"] for r in store.q(
        "SELECT DISTINCT sha256 FROM weekly_feed_log WHERE result IN (?,?,?)", OK_RESULTS)]


def status() -> dict:
    ok = store.one("SELECT received_at, file_name, source FROM weekly_feed_log WHERE result IN (?,?) "
                   "ORDER BY id DESC LIMIT 1", OK_RESULTS[:2])
    win = store.one("SELECT received_at FROM weekly_feed_log WHERE source='windows' ORDER BY id DESC LIMIT 1")
    err = store.rows(store.q(
        "SELECT l.* FROM weekly_feed_log l WHERE l.result='エラー' AND NOT EXISTS (SELECT 1 FROM weekly_feed_log m "
        "WHERE m.file_name=l.file_name AND m.id>l.id AND m.result IN (?,?,?)) ORDER BY l.id DESC LIMIT 10", OK_RESULTS))
    recent = store.rows(store.q("SELECT file_name, period_from, period_to, result, error, source, received_at "
                                "FROM weekly_feed_log ORDER BY id DESC LIMIT 15"))
    n = store.val("SELECT COUNT(*) FROM newprod_weekly", (), 0)
    return {"last_success": dict(ok) if ok else None, "last_windows": win["received_at"] if win else None,
            "open_errors": err, "recent": recent, "periods": n,
            "configured": token_configured()}


def token_path():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "config" / "weekly_feed_token"


def token_configured() -> bool:
    try:
        return bool(token_path().read_text(encoding="utf-8").strip())
    except OSError:
        return False


def token_ok(given: str) -> bool:
    import hmac
    try:
        want = token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return bool(want) and bool(given) and re.fullmatch(r"[A-Za-z0-9_\-]{20,200}", given or "") is not None \
        and hmac.compare_digest(given, want)
