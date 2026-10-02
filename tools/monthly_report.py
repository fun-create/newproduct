#!/usr/bin/env python3
"""月次レポートを作って保存する（FR-116・systemd timer `newproduct-monthly` から毎月2日 10:00）。

    sudo -u newproduct python3 tools/monthly_report.py              # 前の月
    sudo -u newproduct python3 tools/monthly_report.py --month 2026-09
    sudo -u newproduct python3 tools/monthly_report.py --print      # 保存せず表示だけ
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import report, store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()
    m = a.month or report.prev_month()
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", m):
        print("月は YYYY-MM で", file=sys.stderr)
        return 2
    store.migrate()
    if a.print:
        body, problems = report.build(m)
        print(body)
        return 1 if problems else 0
    r = report.save(m, "timer")
    print(f"月次レポート {m} を保存しました" + (f"（作れなかった節 {len(r['problems'])}: {r['problems']}）" if r["problems"] else ""))
    return 1 if r["problems"] else 0     # 一部が作れなかったら失敗として残す（落ちない失敗にしない）


if __name__ == "__main__":
    sys.exit(main())
