#!/usr/bin/env python3
"""展開の表（スキンシール本体モデル × 展開）を seed/09_*.tsv から取り込む（FR-140・ADR-102）。

    sudo -u newproduct python3 tools/import_device_models.py            # 取り込む（画面で直した行は触らない）
    sudo -u newproduct python3 tools/import_device_models.py --report   # いま何件入っているか
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import device, store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    store.migrate()
    if a.report:
        for r in store.q("SELECT sheet, COUNT(*) n, SUM(edited_at IS NOT NULL) e FROM device_model GROUP BY sheet ORDER BY sheet"):
            print(f"{r['sheet']}: {r['n']} 行（画面で直した {r['e']}）")
        print("候補:", {r[0]: r[1] for r in store.q("SELECT state, COUNT(*) FROM device_candidate GROUP BY state")})
        return 0
    n = device.import_models()
    print(f"取り込み: {n['sheets']} シート・足した {n['inserted']}・直した {n['updated']}・画面で直した行を残した {n['kept_edited']}"
          + (f"・無いシート {n['missing']}" if n["missing"] else ""))
    return 1 if n["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
