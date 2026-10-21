"""
進捗管理シートからアプリへの切り替えの段階（2026-10-20 の点検 H-1・ADR-119）。

**11/8 まではシートが正**（並行運用。進捗・期限・新しいタスクはシートに書き、月曜の同期でアプリへ写す）。
11/9 からはアプリが正。画面の帯と「正本」の語はここから決める（各画面で日付を持たない）。

`NEWPRODUCT_CUTOVER`（YYYY-MM-DD）で日を差し替えられる（検査・延期のとき）。
"""
from __future__ import annotations

import datetime as _dt
import os

from app import store

APP_PRIMARY_FROM = "2026-11-09"


def app_primary_from() -> _dt.date:
    return _dt.date.fromisoformat(os.environ.get("NEWPRODUCT_CUTOVER") or APP_PRIMARY_FROM)


def phase() -> str:
    """`parallel`（シートが正）か `app`（アプリが正）。"""
    return "parallel" if store.today() < app_primary_from() else "app"


def last_sheet_day() -> str:
    return (app_primary_from() - _dt.timedelta(days=1)).isoformat()
