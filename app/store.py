#!/usr/bin/env python3
"""
DBアクセス。SQLite 1ファイル・WAL・**ORM を足さない**（keiei の作法）。

**スキーマはここに書かない。**`migrations/*.sql` を名前順に流すだけ
（secretary の作法）。前に進む方向しか用意しない。

**「今日」をここに閉じる。**`NEWPRODUCT_TODAY` があればそれを使う。
期間フィルタの境界は日付ひとつで結果が変わるので、テストで固定できないと
「境界を検査した」と言えない。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MIGRATIONS = BASE / "migrations"

_local = threading.local()


def db_path() -> Path:
    return Path(os.environ.get("NEWPRODUCT_DB") or (BASE / "data" / "newproduct.db"))


def today() -> _dt.date:
    """**テストで固定できる今日。**境界の検査に要る。"""
    s = os.environ.get("NEWPRODUCT_TODAY")
    if s:
        return _dt.date.fromisoformat(s)
    return _dt.date.today()


def today_s() -> str:
    return today().isoformat()


def now_s() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conn() -> sqlite3.Connection:
    """スレッドごとに1本。`ThreadingHTTPServer` なので使い回さない。"""
    c = getattr(_local, "conn", None)
    key = str(db_path())
    if c is not None and getattr(_local, "key", None) == key:
        return c
    if c is not None:
        c.close()
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    _local.conn = c
    _local.key = key
    return c


def close():
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None
        _local.key = None


def migrate(c: sqlite3.Connection | None = None) -> list[str]:
    """`migrations/*.sql` を名前順に流す。**何度流しても同じ結果**。

    どれを流したかを `schema_migration` に残す。残さないと、
    「入っているはずの列が無い」ときに、どこまで進んだのか分からない。
    """
    c = c or conn()
    c.execute("CREATE TABLE IF NOT EXISTS schema_migration ("
              "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = []
    for p in sorted(MIGRATIONS.glob("*.sql")):
        c.executescript(p.read_text(encoding="utf-8"))
        c.execute("INSERT OR IGNORE INTO schema_migration (name, applied_at) "
                  "VALUES (?,?)", (p.name, now_s()))
        applied.append(p.name)
    c.commit()
    return applied


# ── 問い合わせ ────────────────────────────────────────────
def q(sql: str, params=()) -> list[sqlite3.Row]:
    return list(conn().execute(sql, params))


def one(sql: str, params=()):
    r = conn().execute(sql, params).fetchone()
    return r


def val(sql: str, params=(), default=None):
    r = one(sql, params)
    return default if r is None else r[0]


def ex(sql: str, params=()):
    return conn().execute(sql, params)


@contextmanager
def tx():
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


def new_id(table: str = "project", width: int = 4) -> str:
    """URL に貼る短い16進（`#/projects/2f91`）。衝突したら引き直す。"""
    for _ in range(64):
        s = secrets.token_hex(width // 2)
        if one(f"SELECT 1 FROM {table} WHERE id=?", (s,)) is None:
            return s
    return secrets.token_hex(8)


def audit(user_id: str | None, action: str, target: str = "",
          detail=None, ip: str = ""):
    """**全操作を残す。**`detail` は JSON にして入れる。"""
    ex("INSERT INTO audit (at, user_id, action, target, detail, ip) "
       "VALUES (?,?,?,?,?,?)",
       (now_s(), user_id, action, target,
        json.dumps(detail, ensure_ascii=False) if detail is not None else None,
        ip))
    conn().commit()


def rows(rs) -> list[dict]:
    return [dict(r) for r in rs]
