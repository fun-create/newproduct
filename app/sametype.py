#!/usr/bin/env python3
"""
同型の検知（FR-139・F-9-14・ADR-104・2026-10-18 十文字さんの選択「同型の検知を足す」）。

F-9-14 の問題: FCTR の12案は4種類の基本セットの使い回しだった（132スロット中132が例外なし・79.2%が再提示）。
**テンプレートを持たない**のはこのアプリの設計（トレンドからの起票はテーマ名と市場性の点だけ。構成は人が自分の言葉で書く）。
ここでは、それでも同じ構成（「どの形の商品の組み合わせか」）を違うテーマに使い回していないかを**数えて見せる**（止めない・警告だけ）。

構成の印（signature）＝商品案名と概要に出てくる**商品の形の語**の集合（アクスタ・うちわ・缶バッジ…）。
印が同じ案は「同型」。**テーマが違うのに印が同じ**ものを特に数える（使い回しの疑い）。
過去の新商品（売上状況表）の商品名とも比べる（既にある構成か）。

守ること: 形の語に無い新しい形は印が空になり「判定できない」と出す（0件と区別）。語の一覧はここに置き、足すのは人。
"""
from __future__ import annotations

import re

from app import store

# 商品の形の語（同じ行は同じ形）。**判定のためだけ。**商品構成のテンプレートではない
FORMS = [
    ("アクスタ", ("アクスタ", "アクリルスタンド")),
    ("アクキー", ("アクキー", "アクリルキーホルダー")),
    ("キーホルダー", ("キーホルダー",)),
    ("うちわ", ("うちわ", "ウチワ")),
    ("缶バッジ", ("缶バッジ", "缶バッチ", "バッジ")),
    ("ステッカー", ("ステッカー", "シール")),
    ("スキンシール", ("スキンシール",)),
    ("マグ", ("マグ", "マグカップ")),
    ("Tシャツ", ("tシャツ", "ｔシャツ")),
    ("タオル", ("タオル",)),
    ("ペンライト", ("ペンライト", "キンブレ", "ペンラ")),
    ("クッション", ("クッション",)),
    ("ポスター", ("ポスター",)),
    ("フレーム", ("フレーム", "額")),
    ("スマホケース", ("スマホケース", "iphoneケース")),
    ("ストラップ", ("ストラップ",)),
    ("ハメパチ", ("ハメパチ",)),
    ("トートバッグ", ("トート", "バッグ")),
    ("ポーチ", ("ポーチ",)),
    ("クリアファイル", ("クリアファイル",)),
    ("フォトカード", ("フォトカード", "フォトカ", "トレカ")),
    ("カード", ("カード",)),
    ("法被", ("法被", "はっぴ")),
    ("ランヤード", ("ランヤード", "ネックストラップ")),
    ("リボン", ("リボン",)),
    ("ボックス", ("ボックス", "box")),
    ("フラッグ", ("フラッグ", "ゲーフラ", "旗")),
    ("マスク", ("マスク",)),
    ("ネイル", ("ネイル",)),
    ("スタンプ", ("スタンプ",)),
]
SET_WORDS = ("セット", "set", "詰め合わせ")


def _text(*parts) -> str:
    return " ".join(p for p in parts if p).lower()


def signature(title: str, summary: str = "") -> list[str]:
    """商品の形の語の集合（並びは FORMS の順）。見つからなければ空。"""
    t = _text(title, summary)
    out = [name for name, words in FORMS if any(w.lower() in t for w in words)]
    # 「カード」はフォトカードの一部でもある。より具体の語があれば一般の語は外す
    if "フォトカード" in out and "カード" in out:
        out.remove("カード")
    if "アクキー" in out and "キーホルダー" in out:
        out.remove("キーホルダー")
    if "スキンシール" in out and "ステッカー" in out and not re.search(r"ステッカー", t):
        out.remove("ステッカー")
    if any(w in t for w in SET_WORDS) and out:
        out.append("セット")
    return out


def check(title: str, summary: str = "", theme_id: str | None = None, exclude_id: str | None = None) -> dict:
    """同型の案（印が同じ）と、過去の新商品で印が同じもの。"""
    sig = signature(title, summary)
    if not sig:
        return {"signature": [], "state": "判定できない", "why": "商品案名・概要に商品の形の語（アクスタ・うちわ・缶バッジ など）が無いので、同型かどうかを判定できません",
                "ideas": [], "other_theme": 0, "same_theme": 0, "past": []}
    rows = []
    for r in store.q("SELECT id, title, summary, stage, theme_id FROM idea ORDER BY id"):
        if exclude_id and r["id"] == exclude_id:
            continue
        if signature(r["title"], r["summary"] or "") == sig:
            rows.append({"id": r["id"], "title": r["title"], "stage": r["stage"], "theme_id": r["theme_id"],
                         "other_theme": bool(theme_id) and bool(r["theme_id"]) and r["theme_id"] != theme_id})
    past = [{"code": p["code"], "name": p["name"], "launch_date": p["launch_date"], "first12_yen": p["first12_yen"]}
            for p in store.q("SELECT code, name, launch_date, first12_yen FROM past_product ORDER BY launch_date")
            if signature(p["name"] or "") == sig]
    other = sum(1 for r in rows if r["other_theme"])
    return {"signature": sig, "state": "同型あり" if rows or past else "同型なし",
            "why": None, "ideas": rows[:30], "other_theme": other, "same_theme": len(rows) - other, "past": past[:20]}
