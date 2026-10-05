#!/usr/bin/env python3
"""
利用者ごとの表示設定（2026-10-06 十文字さん指示・ADR-057）。

- **本人の見た目だけ**を変える。権限にも他の人の画面にも影響しない（だから誰でも自分の分を変えられる）
- 配色は共通部品 `auth.set_theme`（他のアプリと同じ置き場所・同じ3状態）。ここでは扱わない
- 値は**許可リストのものだけ**。画面の属性・URL に入るので、任意の文字列を通さない
- 既定は**今までの見え方**（標準・標準・ダッシュボード・上位3・客層の順・減衰中も出す）
- サーバーにも持つのは、別の端末でも同じ見え方にするため
"""
from __future__ import annotations

from app import store

# key → (選択肢 [(値, 表示名)], 既定値, 見出し)
PREFS = {
    "font": ([("normal", "標準"), ("large", "大きい"), ("xlarge", "とても大きい")], "normal", "文字の大きさ"),
    "density": ([("compact", "詰める"), ("normal", "標準"), ("roomy", "ゆったり")], "normal", "表の詰め具合"),
    "start": ([("#/", "ダッシュボード"), ("#/ideas", "アイデア"), ("#/plan", "プラン"), ("#/projects", "案件"),
               ("#/tasks", "タスク"), ("#/sales", "売上・原価"), ("#/trends", "トレンド（FCTR）")],
              "#/", "最初に開く画面"),
    "trend_top": ([("1", "上位1"), ("2", "上位2"), ("3", "上位3"), ("5", "上位5")], "3", "トレンドで印を付ける数"),
    "trend_order": ([("segment", "客層の順（サイト・客層の並び）"), ("score", "点の高い客層から")], "segment",
                    "トレンドの客層の並び"),
    "trend_faded": ([("show", "出す"), ("hide", "出さない")], "show", "今週観測されなかったテーマ（減衰中）"),
}


def get(user_id: str) -> dict:
    """その人の設定（未設定は既定値）。**知らない値は既定に倒す**（古いデータ・手書き対策）。"""
    got = {r["key"]: r["value"] for r in store.q("SELECT key, value FROM user_pref WHERE user_id=?",
                                                 (user_id or "",))}
    out = {}
    for k, (opts, default, _label) in PREFS.items():
        v = got.get(k)
        out[k] = v if v in {o[0] for o in opts} else default
    return out


def options() -> list[dict]:
    return [{"key": k, "label": label, "default": default,
             "options": [{"value": v, "label": l} for v, l in opts]}
            for k, (opts, default, label) in PREFS.items()]


def save(user_id: str, f: dict) -> dict:
    """渡された項目だけ変える。**1つでも許可リストに無ければ何も変えない。**"""
    if not user_id:
        raise PermissionError("ログインし直してください")
    vals = {}
    for k, v in (f or {}).items():
        if k not in PREFS:
            continue
        opts = {o[0] for o in PREFS[k][0]}
        if v not in opts:
            raise ValueError(f"{PREFS[k][2]}の指定が不正です")
        vals[k] = v
    if not vals:
        raise ValueError("変える項目がありません")
    now = store.now_s()
    with store.tx() as c:
        for k, v in vals.items():
            c.execute("INSERT INTO user_pref (user_id,key,value,updated_at) VALUES (?,?,?,?) "
                      "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                      (user_id, k, v, now))
    return {"ok": True, "prefs": get(user_id)}
