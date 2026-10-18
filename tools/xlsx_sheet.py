#!/usr/bin/env python3
"""Google スプレッドシートを .xlsx で書き出したものから、1シートを TSV に起こす（標準ライブラリだけ）。

    python3 tools/xlsx_sheet.py 進捗管理.xlsx 全タスク一覧 out.tsv
    python3 tools/xlsx_sheet.py 進捗管理.xlsx 年間プラン2026年度 out.tsv

- **折りたたまれた（非表示の）行も含めて全部出す。**表示ページ（htmlview）は折りたたまれたグループの行を出さないため、
  2026-10-18 の同期で .xlsx に切り替えた（ADR-105）
- セル内の改行は `\\n`（2文字）、タブは空白に置き換える（seed/ の TSV と同じ書式）
- 日付は Excel の通し番号のまま（46294 など）。読む側（tools/sync_sheet.py）が日付に直す
- 行番号はシートの行番号どおり（空行も空行として出す）
"""
from __future__ import annotations

import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_sheet(path: str, sheet_name: str) -> list[list[str]]:
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    names = [s.get("name") for s in wb.find("m:sheets", NS)]
    if sheet_name not in names:
        raise SystemExit(f"シート「{sheet_name}」がありません（{'・'.join(names)}）")
    rid = next(s.get("{%s}id" % NS["r"]) for s in wb.find("m:sheets", NS) if s.get("name") == sheet_name)
    target = next(r.get("Target") for r in rels if r.get("Id") == rid)
    target = target.lstrip("/")
    sh = ET.fromstring(z.read(target if target.startswith("xl/") else "xl/" + target))
    out: list[list[str]] = []
    for row in sh.find("m:sheetData", NS).findall("m:row", NS):
        cells = {}
        for c in row.findall("m:c", NS):
            t, v = c.get("t"), c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
            elif t == "b" and v is not None:
                val = "TRUE" if v.text == "1" else "FALSE"
            else:
                val = v.text if v is not None else ""
            cells[_col(c.get("r"))] = (val or "").replace("\t", " ").replace("\r\n", "\n").replace("\n", "\\n").strip()
        r = int(row.get("r"))
        while len(out) < r - 1:
            out.append([])
        line = [cells.get(i, "") for i in range(max(cells) + 1)] if cells else []
        while line and not line[-1]:
            line.pop()
        out.append(line)
    return out


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    rows = read_sheet(sys.argv[1], sys.argv[2])
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        for r in rows:
            f.write("\t".join(r) + "\n")
    print(f"{sys.argv[2]}: {len(rows)} 行 → {sys.argv[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
