#!/usr/bin/env python3
"""
過去の新商品の「発売から12か月」の売上を取り込む（販売計画シミュレーションの歩留まりの元・F-14-4・ADR-072）。

    python3 tools/import_past_products.py --dry-run   # 数えるだけ
    python3 tools/import_past_products.py             # 入れる（何度流しても同じ結果）

元の表: `seed/04_新商品売上状況__{2022..2025}年度.tsv`（新商品売上状況のスプレッドシート・2026-09-20 の写し）。
各年度の表は、商品ごとに**月の合計＋チャネル別**が横に並ぶ（2行目に「YYYY年M月（月次合計＋チャネル別）」の見出し）。

- 発売月から12か月の合計を数える。発売前の月は「-」
- **表に数字がある月だけ数える。**表は先の月に 0 を入れてあるので、全商品の合計が 0 でない最後の月（`data_end`）までを数える。
  12か月そろわない商品（最近発売したもの）は `complete=0` にし、分布に入れない
  （そろわない分を 0 として足すと、売れない商品に見える）
- 同じ商品IDが複数の年度の表に出たら、月を足し合わせる（年度をまたぐ12か月）
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import store  # noqa: E402

FILES = [BASE / "seed" / f"04_新商品売上状況__{y}年度.tsv" for y in (2022, 2023, 2024, 2025)]
CHANNELS = ["楽天", "Yahoo", "うちわ", "グッズ", "ギフトモール", "amazon"]


def _yen(s):
    s = (s or "").strip().replace(",", "").replace("¥", "")
    if s in ("", "-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _ym(label):
    m = re.match(r"(\d{4})年(\d{1,2})月", label)
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}" if m else None


def read() -> dict:
    """{code: {"name","launch","fy","months": {ym: {"total", ch...}}}}"""
    out = {}
    for f in FILES:
        rows = list(csv.reader(f.open(encoding="utf-8"), delimiter="\t"))
        hdr = next(i for i, r in enumerate(rows) if "商品ID" in r)
        mrow = next(i for i, r in enumerate(rows) if any("月次合計" in c for c in r))
        months = [(j, _ym(c)) for j, c in enumerate(rows[mrow]) if "月次合計" in c and _ym(c)]
        for r in rows[hdr + 1:]:
            if len(r) < 4 or not re.match(r"P\d+$", (r[1] or "").strip()):
                continue
            code = r[1].strip()
            ent = out.setdefault(code, {"name": r[2].strip(), "launch": r[3].strip(), "months": {}})
            for j, ym in months:
                tot = _yen(r[j]) if j < len(r) else None
                if tot is None:
                    continue
                ch = {c: (_yen(r[j + 1 + k]) if j + 1 + k < len(r) else None) or 0 for k, c in enumerate(CHANNELS)}
                cur = ent["months"].setdefault(ym, {"total": 0.0, **{c: 0.0 for c in CHANNELS}})
                cur["total"] += tot
                for c in CHANNELS:
                    cur[c] += ch[c]
    return out


def data_end(raw: dict) -> str | None:
    """**表に実際の数字がある最後の月。**それより後の月は、表が先回りして 0 を入れているだけ（実測 2026-10-09:
    2026-04 以降は全商品 0）。この月より後は「まだ見ていない月」として数えない。"""
    tot = {}
    for e in raw.values():
        for ym, v in e["months"].items():
            tot[ym] = tot.get(ym, 0) + v["total"]
    live = [ym for ym, v in tot.items() if v > 0]
    return max(live) if live else None


def build(raw: dict) -> list[dict]:
    end = data_end(raw)
    rows = []
    for code, e in sorted(raw.items()):
        m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", e["launch"])
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        launch = f"{y:04d}-{mo:02d}-{int(m.group(3)):02d}"
        window = [f"{y + (mo - 1 + k) // 12:04d}-{(mo - 1 + k) % 12 + 1:02d}" for k in range(12)]
        seen = [ym for ym in window if ym in e["months"] and (end is None or ym <= end)]
        total = sum(e["months"][ym]["total"] for ym in seen)
        ch = {c: round(sum(e["months"][ym][c] for ym in seen)) for c in CHANNELS}
        rows.append({"code": code, "name": e["name"], "launch_date": launch,
                     "fy": y if mo >= 5 else y - 1, "first12_yen": round(total), "months_seen": len(seen),
                     "complete": 1 if len(seen) == 12 else 0, "channels": ch,
                     # 発売月を 0 として 0〜11 か月目の売上（見ていない月は null）
                     "months": [round(e["months"][ym]["total"]) if ym in seen else None for ym in window]})
    return rows


def apply(rows: list[dict]) -> int:
    now = store.now_s()
    with store.tx() as c:
        for r in rows:
            c.execute("INSERT INTO past_product (code,name,launch_date,fy,first12_yen,months_seen,complete,channels_json,"
                      "months_json,source,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                      "name=excluded.name,launch_date=excluded.launch_date,fy=excluded.fy,first12_yen=excluded.first12_yen,"
                      "months_seen=excluded.months_seen,complete=excluded.complete,channels_json=excluded.channels_json,"
                      "months_json=excluded.months_json,source=excluded.source,imported_at=excluded.imported_at",
                      (r["code"], r["name"], r["launch_date"], r["fy"], r["first12_yen"], r["months_seen"],
                       r["complete"], json.dumps(r["channels"], ensure_ascii=False), json.dumps(r["months"]),
                       "新商品売上状況（2022〜2025年度の表・2026-09-20 の写し）", now))
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rows = build(read())
    comp = [r for r in rows if r["complete"]]
    vals = sorted(r["first12_yen"] for r in comp)
    med = vals[len(vals) // 2] if vals else None
    print(f"表の最後の月: {data_end(read())}")
    print(f"商品 {len(rows)} 件（12か月そろった {len(comp)} 件・そろわない {len(rows) - len(comp)} 件）")
    if vals:
        print(f"  12か月の売上: 中央値 {med:,.0f} 円・0円 {sum(1 for v in vals if v == 0)} 件・最大 {vals[-1]:,.0f} 円")
    if a.dry_run:
        return 0
    store.migrate()
    print("入れた:", apply(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
