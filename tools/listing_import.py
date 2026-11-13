#!/usr/bin/env python3
"""
店の出力ファイルを台帳に取り込む（段階1・FR-287〜289・ADR-141）。**既定は書かない。**件数を出すだけ。

    sudo -u newproduct env ... python3 tools/listing_import.py preview  goods  <フォルダかファイル…>
    sudo -u newproduct env ... python3 tools/listing_import.py apply    goods  <フォルダかファイル…> --by masateru
    sudo -u newproduct env ... python3 tools/listing_import.py undo     <取り込みの番号> --by masateru
    sudo -u newproduct env ... python3 tools/listing_import.py list     [goods]
    sudo -u newproduct env ... python3 tools/listing_import.py purge-private [--apply --by masateru]   # 専用ページ（PL-64）を消す

店の記号: goods うちわ=uchiwa frame rakuten yahoo_goods yahoo_frame giftmall amazon overseas
**値は出さない**（件数・ファイル名・列の数だけ）。取り込めるのは社長の業務ロールの人だけ（--by masateru）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import listing, store  # noqa: E402


def show(pv: dict) -> None:
    s = pv["shop"]
    print(f"# {s['label']}（{s['code']}・{s['cart']}）")
    for f in pv["files"]:
        if f.get("used"):
            print(f"  読む   {f['name']}: {f['rows']} 行（数えた {f['accounted']}・列 {f['columns']}・落とした列 {f['dropped']}・{f['encoding']}）")
        else:
            print(f"  読まない {f['name']}: {f['reason']}")
    c = pv["counts"]
    print(f"出品 {c['listings']} 件 → 増える {c['added']}・変わる {c['changed']}（うち戻った {c['returned']}）・同じ {c['same']}・消える {c['gone']}")
    print(f"選択肢の行 {c['options']}・店の選択肢のマスタ {c['masters']}（増える {c['m_added']}・変わる {c['m_changed']}・消える {c['m_gone']}）")
    print("公開の状態: " + "・".join(f"{k} {v}" for k, v in sorted(pv["states"].items()))
          + ("" if pv["state_checked"] else "（この店の 0/1 などの読み方は画面で確かめていません）"))
    print(f"親の無い行 {c['orphans']} {json.dumps(pv['orphans'], ensure_ascii=False) if pv['orphans'] else ''}")
    print(f"専用ページとして外した出品 {c['excluded']}（台帳から消す {c['purge']}）")
    print(f"お客さまの情報らしい値: メールアドレスの形 {pv['pii']['email']}・電話番号の形 {pv['pii']['phone']}")
    for e in pv["errors"]:
        print("  ✗ " + e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["preview", "apply", "undo", "list", "purge-private"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--by", default="")
    ap.add_argument("--apply", action="store_true", help="purge-private で本当に消す")
    a = ap.parse_args()
    store.migrate()
    if a.what in ("preview", "apply"):
        if len(a.args) < 2:
            print("店の記号と、フォルダかファイルを渡してください")
            return 2
        code, paths = a.args[0], a.args[1:]
        pv = listing.preview(code, paths)
        show(pv)
        if a.what == "preview":
            return 1 if pv["errors"] else 0
        if not a.by:
            print("apply には --by が要ります")
            return 2
        r = listing.apply(code, paths, a.by)
        print(f"\n書きました: 取り込み #{r['import_id']}")
        return 0
    if a.what == "purge-private":
        # 台帳に入っている「○○様専用」「専用ページ」の出品（PL-64）を消す。既定は数えるだけ
        if a.apply and not a.by:
            print("--apply には --by が要ります")
            return 2
        print(listing.purge_private(a.apply, a.by))
        return 0
    if a.what == "undo":
        if not a.args or not a.by:
            print("取り込みの番号と --by が要ります")
            return 2
        print(listing.undo(int(a.args[0]), a.by))
        return 0
    for r in listing.imports(a.args[0] if a.args else ""):
        print(f"#{r['id']} {r['at']} {r['shop_label']} {r['state']} {r['by_user']} {json.dumps(r['counts'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
