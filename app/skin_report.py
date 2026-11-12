#!/usr/bin/env python3
"""
スキンシール展開判断レポートを**見る**ところ（2026-11 に Auto GROWTH から引き取り・ADR-140・FR-251）。

作るのは `tools/skin_report/skin_report.py`（毎週月曜 8:00・`newproduct-skin-report.timer`）。
ここは `data/skin_report/` に出来たファイルを**読むだけ**。

- **tools/skin_report を import しない。**あちらは `socket.getaddrinfo` を IPv4 優先に差し替えるので、
  import するとサーバー全体の名前解決が変わる。伏せた名前（型の新設が不要）は保存した JSON の
  `mold_hidden` を読むだけ（規則を写さない）
- 日付は8桁の数字だけ受ける（パス抜け防止）
- 1本も無くても落とさない（「まだ無い」と「読めない」を分けて返す）
- 共通ログインの内側だけ。外へは出さない（Auto GROWTH 2026-09-25 十文字さん判断を引き継ぐ）
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
HTML_RE = re.compile(r"^スキンシール展開判断レポート_(\d{8})\.html$")
JUDGMENTS = (("BUY", "即購入"), ("ALT", "代替サイト"), ("REAL", "実機購入"), ("WAIT", "様子見"), ("UNKNOWN", "判定なし"))
TIMER = "newproduct-skin-report.timer"
WATCH_HOUR = 12                       # 月曜 8:00 起動。遅れても昼までは待つ（Auto GROWTH FR-68 と同じ）


def out_dir() -> Path:
    return Path(os.environ.get("SKIN_REPORT_OUT") or BASE / "data" / "skin_report")


def _ja(d: str) -> str:
    try:
        x = dt.datetime.strptime(d, "%Y%m%d")
    except ValueError:
        return d
    return f"{x.year}年{x.month}月{x.day}日（{'月火水木金土日'[x.weekday()]}）"


def reports() -> tuple[list, str]:
    """(一覧, 読めなかった理由)。HTML と根拠の JSON を組に。JSON が壊れていても HTML の一覧は返す。"""
    d = out_dir()
    try:
        if not d.is_dir():
            return [], ""
        files = sorted(d.glob("スキンシール展開判断レポート_*.html"), reverse=True)
    except OSError as e:
        return [], f"{type(e).__name__}: {e}"
    out = []
    for f in files:
        m = HTML_RE.match(f.name)
        if not m:
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue
        out.append({"date": m.group(1), "ja": _ja(m.group(1)), "size": size,
                    "has_json": (d / f"skin_report_{m.group(1)}.json").is_file()})
    return out, ""


def _meta(date8: str) -> tuple[dict | None, str]:
    j = out_dir() / f"skin_report_{date8}.json"
    if not j.is_file():
        return None, ""
    try:
        return json.loads(j.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"


def file_bytes(date8: str) -> bytes | None:
    """レポート本体。無ければ None。**8桁の数字だけ**。"""
    if not re.fullmatch(r"\d{8}", date8 or ""):
        return None
    root = out_dir().resolve()
    f = (root / f"スキンシール展開判断レポート_{date8}.html").resolve()
    try:
        if f.parent != root or not f.is_file():
            return None
        return f.read_bytes()
    except OSError:
        return None


def timer_enabled() -> bool | None:
    """タイマーが有効か。分からなければ None（systemctl が無い・開発機）。"""
    try:
        r = subprocess.run(["systemctl", "is-enabled", TIMER], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() == "enabled"


def missing_this_week(rows: list, now: dt.datetime | None = None, enabled: bool | None = None) -> str | None:
    """今週の分が無ければ理由（FR-68 を引き継ぐ）。タイマーが有効なときだけ・月曜の正午までは待つ。"""
    if enabled is not True:
        return None
    now = now or dt.datetime.now()
    monday = (now - dt.timedelta(days=now.weekday())).date()
    if now.weekday() == 0 and now.hour < WATCH_HOUR:
        return None
    if any(r["date"] == f"{monday:%Y%m%d}" for r in rows):
        return None
    last = rows[0]["ja"] if rows else None
    return (f"{monday:%Y-%m-%d}（月）の分が出ていません。" + (f"直近は {last} です。" if last else "過去のレポートも1本もありません。")
            + "検証に落ちて配信を見送った可能性があります（journalctl -u newproduct-skin-report に理由が出ます）")


def view(sel: str = "") -> dict:
    rows, err = reports()
    en = timer_enabled()
    out = {"reports": rows, "error": err or None, "timer_enabled": en, "missing": missing_this_week(rows, enabled=en),
           "cur": None}
    if err or not rows:
        return out
    cur = next((r for r in rows if r["date"] == sel), rows[0])
    meta, meta_err = _meta(cur["date"])
    c = {"date": cur["date"], "ja": cur["ja"], "meta_err": meta_err or None, "has_meta": meta is not None,
         "counts": None, "n_new": None, "reach": [], "unverified": 0, "mention_missing": False}
    if meta:
        data = meta.get("data") or {}
        series = data.get("series") or []
        if series:                                       # JSON が無い週は数えない（0 にしない）
            cnt = {k: 0 for k, _ in JUDGMENTS}
            for s in series:
                if s.get("judgment") in cnt:
                    cnt[s["judgment"]] += 1
            c["counts"] = [{"key": k, "label": ja, "n": cnt[k]} for k, ja in JUDGMENTS]
            hidden = {str(h.get("name")) for h in (meta.get("mold_hidden") or [])}
            c["n_new"] = len([r for r in (data.get("releases") or [])
                              if r.get("bucket") in ("past", "future") and str(r.get("name")) not in hidden])
        c["reach"] = [{"name": r.get("name"), "ok": bool(r.get("ok")), "reason": r.get("reason") or r.get("url")}
                      for r in (meta.get("reach") or [])]
        c["unverified"] = len([u for u in (data.get("unverified") or []) if str(u).strip()])
        c["mention_missing"] = bool(meta.get("mention_missing"))
    out["cur"] = c
    return out
