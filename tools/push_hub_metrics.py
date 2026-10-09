#!/usr/bin/env python3
"""HUB（Auto GROWTH の metrics）へ指標を書き出す（FR-123・ADR-091）。毎月2日の月次レポートの後にも同じものが流れる。

    sudo -u newproduct env HOME=/opt/newproduct python3 tools/push_hub_metrics.py --dry-run   # 何を送るか出すだけ
    sudo -u newproduct env HOME=/opt/newproduct python3 tools/push_hub_metrics.py             # 送っていない点だけ送る
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import hubmetrics, store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    store.migrate()
    p = hubmetrics.plan() if a.dry_run else hubmetrics.push()
    by: dict = {}
    for r in p["new"]:
        by.setdefault(r["metric"], []).append(r)
    for m, rs in by.items():
        print(f"{m}: {len(rs)} 点（{rs[0]['captured_at'][:7]}〜{rs[-1]['captured_at'][:7]}）例 {rs[-1]['value']}")
    for r in p["changed"]:
        print(f"値が変わった点（送っていない）: {r['metric']} {r['captured_at']} {r['sent_value']} → {r['value']}")
    for n in p["notes"]:
        print("  " + n)
    if a.dry_run:
        print(f"送る予定: {len(p['new'])} 点（全 {p['total']} 点のうち）")
        return 0
    print(f"送った: {p['sent']} 点" + (f"／{p['error']}" if p.get("error") else ""))
    return 1 if p.get("error") or p["changed"] else 0


if __name__ == "__main__":
    sys.exit(main())
