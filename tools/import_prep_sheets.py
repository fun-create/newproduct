#!/usr/bin/env python3
"""
準備シート（Drive の「新商品追加準備シート」）の本文を、案件のカルテの節へ入れる（F-4-8 の残り・ADR-080）。

    python3 tools/import_prep_sheets.py FILE.json --dry-run   # 何をどこへ入れるかを出すだけ
    python3 tools/import_prep_sheets.py FILE.json             # 入れる

FILE.json は {案件ID: {タブ名: 本文}}。十文字さんの選択（2026-10-09「私が Drive を開いて読んで移す」）で、
Chrome から読み取ったもの（読むだけ・Drive は書き換えていない）。

- **カルテに既に書いてある節は上書きしない**（画面で入れたものを守る）。空の節だけ埋める
- タブ → 節の対応は下の TAB_SECTION。同じ節に2つ以上のタブが入るときは【タブ名】を見出しにして続ける
- 入れないタブ: チェックリスト・スケジュール／商品改訂履歴／LP依頼用のチェックリスト（作業の段取りで、カルテの中身ではない）
- テンプレートの説明の行（「制限時間：」「この作業の目的」）と社内のファイルサーバーの場所（\\\\New-terastation…）は落とす
- 履歴（G 節）に「準備シートから移行」と残す
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import store  # noqa: E402

TAB_SECTION = [
    (r"競合調査", "C.competitor"), (r"ターゲット選定", "C.target"), (r"ニーズまとめ", "C.needs"),
    (r"セット内容", "C.needs"), (r"資材の検討", "D.material"), (r"商品名", "F.name"), (r"差別化", "C.diff"),
    (r"製造工程", "E.method"), (r"製造時間・納期", "E.method"), (r"原価算出", "D.cost"), (r"販売価格", "D.price"),
    (r"目標設定", "D.goal"), (r"コンセプトまとめ", "C.concept"), (r"品質", "E.quality"), (r"サンプル検討", "E.sample"),
    (r"LP依頼用.*(商品内容|コンテンツ構成|ワイヤーフレーム)", "F.lp"),
]
SKIP = (r"チェックリスト・スケジュール", r"商品改訂履歴", r"LP依頼用.*チェックリスト")
DROP_LINE = re.compile(r"制限時間：|この作業の目的|\\\\New-terastation|^・$")
WHO = "移行（準備シート）"


def section_for(tab: str) -> str | None:
    if any(re.search(p, tab) for p in SKIP):
        return None
    for p, key in TAB_SECTION:
        if re.search(p, tab):
            return key
    return None


def plan(data: dict) -> list[dict]:
    out = []
    for pid, tabs in data.items():
        if store.one("SELECT 1 FROM project WHERE id=?", (pid,)) is None:
            out.append({"pid": pid, "skip": "その案件がありません"})
            continue
        have = {r["section_key"]: (r["body"] or "").strip() for r in store.q(
            "SELECT section_key, body FROM project_section WHERE project_id=?", (pid,))}
        merged: dict[str, list[tuple[str, str]]] = {}
        for tab, body in tabs.items():
            key = section_for(tab)
            if key is None:
                continue
            lines = [ln for ln in (body or "").splitlines() if ln.strip() and not DROP_LINE.search(ln)]
            if lines:
                merged.setdefault(key, []).append((tab, "\n".join(lines)))
        for key, parts in merged.items():
            text = parts[0][1] if len(parts) == 1 else "\n\n".join(f"【{t.strip()}】\n{b}" for t, b in parts)
            out.append({"pid": pid, "key": key, "chars": len(text), "text": text,
                        "skip": "既に書いてある（上書きしない）" if have.get(key) else None})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    store.migrate()
    rows = plan(data)
    from app import project
    n = 0
    for r in rows:
        mark = r["skip"] or "入れる"
        print(f"{r['pid']} {r.get('key', '—'):<13} {r.get('chars', 0):>6}字  {mark}")
        if not a.dry_run and not r["skip"] and r.get("key"):
            project.save_section(r["pid"], r["key"], r["text"], WHO)
            n += 1
    print(("入れる予定: " if a.dry_run else "入れた: ") + str(sum(1 for r in rows if not r["skip"] and r.get("key")) if a.dry_run else n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
