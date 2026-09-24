#!/usr/bin/env python3
"""
自動化依頼の初期リスト（F-15 ／ FR-159）。2026-09-24 十文字さん経由・商品開発 増地さん。

    python3 tools/import_automation.py --dry-run
    python3 tools/import_automation.py
    python3 tools/import_automation.py --report

**原文を書き換えない。**`raw_request` に増地さんの言葉のまま入れる。
「→」で始まる行は、増地さんご自身が書いた**どうなってほしいか**なので、
これも原文のまま残す（要約して消すと、要望の中身が最初の整理で失われる）。

## 標準タスク178行との突き合わせ（2026-09-24 実測）

**15件中11件は、178行のどこにも無い。**「やっていない」のではなく、
**標準タスク表のほうが現場に追いついていない。**

| 当たる4件 | 178行側 |
|---|---|
| 新規商品のシミュレーションデータ作成 | Webdecoシミュレーション提出用データを作成（4フロー・AI削減40%） |
| 新商品発売時の作成方法検討（アクション等） | 作成方法を検討 ／ データ作成方法を検討 |
| 作成用テンプレート作成 | データ作成用テンプレートを作成（70%）／データ入稿テンプレート作成 |
| LP依頼シート作成 | 商品ページ依頼準備（LP依頼シート）（4フロー・70%・1.40h） |

**紛らわしかったもの**（当たらないと判断した）:

- 「スタンプ登録」… 178行にあるのは「この商品用のスタンプ・背景を**検討**」。
  **検討と登録は別の作業。**最初に数えたとき、ここを取り違えて「5件」と数えた
- 「シミュレーションの修正」… 178行には 作成・提出・確認・共有はあるが**修正が無い**

**`template_id` は結び付けていない。**178行はフローごとに同じ作業が別行で入っており
（LP依頼シートは4行）、1件に決められない。`in_template` で「当たるか」だけを持つ。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import automation as m    # noqa: E402
from app import store              # noqa: E402

REQUESTER = "増地さん"
DEPT = "商品開発部"

# (作業名, 原文, カテゴリ, 標準タスクに当たるか, 178行側のメモ)
ITEMS = [
    ("背景登録", "背景登録", "登録作業", 0, None),
    ("スタンプ登録",
     "スタンプ登録\n→元となる画像を作成したらあとは各サイズ作ってくれて登録もしてほしい",
     "登録作業", 0,
     "「この商品用のスタンプ・背景を検討」はあるが、**検討であって登録ではない**"),
    ("季節ごとにスタンプのカテゴリを修正",
     "季節ごとにスタンプのカテゴリを修正\n"
     "→今変更がある月に自分宛に内容が通知が来るようになっていますが、変更までしてほしい",
     "登録作業", 0, "**通知だけは既にある。**足りないのは変更そのもの"),
    ("売れた商品のチェック（その中から新商品のチェック）",
     "売れた商品のチェック（その中から新商品のチェック）\n"
     "→この作業でデザインや入稿画像まで同時にわかったらうれしいです。",
     "調査", 0, None),
    ("アイコス系商品のデータ作成",
     "アイコス系商品のデータ作成\n"
     "→電子タバコの写真を送ったらデータ作成してくれて、微調整から取り掛かりたい。",
     "データ作成", 0, None),
    ("サンプル作成",
     "サンプル作成\n→ウェブデコ上でできる商品に合った魅力的なサンプルの提案",
     "データ作成", 0, "178行の「試作・調整」は実物の試作で、別の作業"),
    ("新規商品のシミュレーションデータ作成", "新規商品のシミュレーションデータ作成",
     "データ作成", 1,
     "Webdecoシミュレーション提出用データを作成（4フロー・2.00h・AI削減40%）"),
    ("シミュレーションの修正", "シミュレーションの修正", "データ作成", 0,
     "178行には 作成・提出・確認・共有はあるが、**修正が無い**"),
    ("スキンシール貼り方説明書作成", "スキンシール貼り方説明書作成", "書類作成", 0, None),
    ("新商品発売時の作成方法検討（アクション等）",
     "新商品発売時の作成方法検討（アクション等）", "検討", 1,
     "作成方法を検討 ／ データ作成方法を検討"),
    ("作成用テンプレート作成", "作成用テンプレート作成", "データ作成", 1,
     "データ作成用テンプレートを作成（2.00h・AI削減70%）／データ入稿テンプレート作成"),
    ("新商品の説明書・保証書作成", "新商品の説明書・保証書作成", "書類作成", 0, None),
    ("LP依頼シート作成", "LP依頼シート作成", "書類作成", 1,
     "商品ページ依頼準備（LP依頼シート）（4フロー・2.00h・AI削減70%・1.40h）"),
    ("ワイヤーフレーム作成", "ワイヤーフレーム作成", "書類作成", 0, None),
    ("治具作成", "治具作成", "その他", 0, None),
]


def apply(user_id: str = "import") -> dict:
    n = {"created": 0, "kept": 0}
    for title, raw, cat, in_tpl, note in ITEMS:
        if store.one("SELECT 1 FROM automation_request WHERE title=?", (title,)):
            n["kept"] += 1
            continue
        r = m.create(user_id, title=title, raw_request=raw, requester=REQUESTER,
                     dept=DEPT, category=cat, note=note)
        store.ex("UPDATE automation_request SET in_template=? WHERE id=?",
                 (in_tpl, r["id"]))
        n["created"] += 1
    store.conn().commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        d = m.listing()
        print(f"  自動化依頼 {d['total']} 件 / 状態別 {d['by_stage']}")
        print(f"  標準タスクに無い {d['not_in_template']} 件")
        print("  " + d["hours_note"])
        return 0
    print(f"{len(ITEMS)} 件（標準タスクに当たる {sum(x[3] for x in ITEMS)} ／ "
          f"当たらない {sum(1 for x in ITEMS if not x[3])}）")
    if a.dry_run:
        print("[dry-run] DB は触っていません")
        return 0
    store.migrate()
    print("入れました:", apply())
    d = m.listing()
    print(f"  合計 {d['total']} 件 / 標準タスクに無い {d['not_in_template']} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
