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
    # HUB へ指標を書き出す（FR-123・ADR-091）。送っていない点だけ送る
    from app import hubmetrics
    h = hubmetrics.push()
    print(f"HUB の指標: 新しく送った {h['sent']} 点／値が変わった点 {len(h['changed'])}（送っていない）"
          + (f"／{h['error']}" if h["error"] else ""))
    for n in h["notes"]:
        print("  " + n)
    # 発売から60日が締まった「試算 vs 確定」を残す（FR-93・ADR-095）。画面を誰も開かなくても残るように
    from app import costcheck
    cc = costcheck.record_due()
    print(f"原価の突き合わせ: 発売済み {cc['projects']} 件・今回残した {cc['recorded_now']} 件・状態 {cc['counts']}")
    # 新機種調査（FR-140・ADR-102）。AI が使える状態なら毎月1回、裏で走らせる（候補は画面で人が決める）
    from app import device
    try:
        dr = device.start_run("timer")
        print("新機種調査:", "始めた" if dr.get("started") else f"始めなかった（{dr.get('reason')}）")
    except Exception as e:                                   # noqa: BLE001
        print(f"新機種調査: 始められなかった（{e}）")
    return 1 if (r["problems"] or h["error"] or h["changed"]) else 0     # 落ちない失敗にしない


if __name__ == "__main__":
    sys.exit(main())
