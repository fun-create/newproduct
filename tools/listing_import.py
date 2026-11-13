#!/usr/bin/env python3
"""
店の出力ファイルを台帳に取り込む（段階1・FR-287〜289・ADR-141）。**既定は書かない。**件数を出すだけ。

    sudo -u newproduct env ... python3 tools/listing_import.py preview  goods  <フォルダかファイル…>
    sudo -u newproduct env ... python3 tools/listing_import.py apply    goods  <フォルダかファイル…> --by masateru
    sudo -u newproduct env ... python3 tools/listing_import.py undo     <取り込みの番号> --by masateru
    sudo -u newproduct env ... python3 tools/listing_import.py list     [goods]
    sudo -u newproduct env ... python3 tools/listing_import.py cip-link [--apply --by masateru]   # CIP の共通商品コードと結ぶ（段階2）
    sudo -u newproduct env ... python3 tools/listing_import.py roundtrip goods <フォルダ>     # 往復の検査（段階4）
    sudo -u newproduct env ... python3 tools/listing_import.py fs-check goods [--ext a,b]   # 書き出す前の検査
    sudo -u newproduct env ... python3 tools/listing_import.py fs-export goods --ext a --out <フォルダ>   # 試しの1件の書き出し
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
    ap.add_argument("what", choices=["preview", "apply", "undo", "list", "purge-private", "cip-link", "roundtrip", "fs-check", "fs-export"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--by", default="")
    ap.add_argument("--apply", action="store_true", help="purge-private で本当に消す")
    ap.add_argument("--ext", default="", help="fs-export: 書き出す商品URLコード（, 区切り）")
    ap.add_argument("--out", default="", help="fs-export: 書き出す先のフォルダ")
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
    if a.what == "cip-link":
        # 出品と CIP の共通商品コードを結び直す（段階2・ADR-146）。CIP は読むだけ。既定は数えるだけ
        if a.apply and not a.by:
            print("--apply には --by が要ります")
            return 2
        from app import listing_cip
        r = listing_cip.run(a.apply, a.by)
        print(f"CIP の対応表 {r['cip_rows']} 行（時点 {r['as_of']}）→ 結んだ行 {r['link_rows']}・出品 {r['listings']}・共通商品コード {r['product_codes']}"
              f"（{'書いた' if r['applied'] else '数えただけ'}）")
        for k, v in r["per_store"].items():
            print(f"  {k}: 店コード {v['codes']}・結べた {v['linked']}・台帳に無い {v['unmatched']}" + ("（知らない店）" if v.get("unknown_store") else ""))
        return 0
    if a.what in ("roundtrip", "fs-check", "fs-export"):
        # FutureShop の書き出し（段階4・ADR-151）。**取り込みの形は未確認**（試しの1件で確かめるまで店に入れない）
        from app import fs_export
        if not a.args:
            print("店の記号が要ります")
            return 2
        code = a.args[0]
        if a.what == "roundtrip" and store.val("SELECT cart FROM shop WHERE code=?", (code,)) in ("rakuten", "yahoo", "giftmall"):
            from app import mall_export
            r = mall_export.roundtrip(code, a.args[1:])
            print(f"往復の検査 {code}: {'同じ' if r['ok'] else '違いあり'}（専用ページとして除いた {r['excluded']}）")
            for k, v in (r.get("kinds") or {"item": r}).items():
                print(f"  {k}: 元 {v['orig']}・台帳 {v['mine']}・元だけ {v['only_in_file']}・台帳だけ {v['only_in_ledger']}・CSV 読み直し {'同じ' if v['csv_reread_same'] else '違う'}")
            return 0 if r["ok"] else 1
        if a.what == "roundtrip":
            r = fs_export.roundtrip(code, a.args[1:])
            print(f"往復の検査 {code}: {'同じ' if r['ok'] else '違いあり'}（専用ページとして除いた {r['excluded']}）")
            for k, v in r["kinds"].items():
                print(f"  {v['file']}: 元 {v['orig']}・台帳 {v['mine']}・元だけ {v['only_in_file']}・台帳だけ {v['only_in_ledger']}・CSV 読み直し {'同じ' if v['csv_reread_same'] else '違う'}")
            return 0 if r["ok"] else 1
        ids = None
        if a.ext:
            exts = [x.strip() for x in a.ext.split(",") if x.strip()]
            ids = [r["id"] for r in store.q(f"SELECT id FROM listing WHERE shop=? AND ext_id IN ({','.join('?' * len(exts))})", [code] + exts)]
            if len(ids) != len(exts):
                print("台帳に無い商品URLコードがあります")
                return 2
        c = fs_export.check(code, ids)
        print(f"検査 {code}: 出品 {c['listings']}・止めるもの {len(c['errors'])}")
        for e in c["errors"][:30]:
            print("  ✗ " + e)
        for w in c["warnings"]:
            print("  ・" + w)
        if a.what == "fs-check" or c["errors"]:
            return 1 if c["errors"] else 0
        if not a.out or ids is None:
            print("fs-export には --ext（商品URLコード）と --out（書き出す先）が要ります。全件は書き出さない")
            return 2
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = store.now_s().replace("-", "").replace(":", "").replace(" ", "")
        for kind in fs_export.KINDS:
            rs = fs_export.rows(code, kind, ids)
            if not rs:
                continue
            f = out / f"{fs_export.FILE_PREFIX[kind]}_NP{stamp}.csv"
            f.write_bytes(fs_export.to_csv(code, kind, rs))
            print(f"  書いた {f}（{len(rs)} 行）")
        store.audit(a.by or "cli", "fs_export.write", code, {"listings": len(ids), "out": str(out)})
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
