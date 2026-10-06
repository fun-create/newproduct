#!/usr/bin/env python3
"""
アイデアの一括登録（設定ページ・2026-10-06 十文字さんの選択・ADR-062）。

流れは「貼り付け → 確認（何も保存しない）→ 登録」。
- 表計算ソフトからの貼り付け（タブ区切り）か CSV。**列は見出しの語で読む**（位置で読まない）
- 行ごとに: 入る／止まる（理由）／既にある（同じ名前・表記ゆれ込み。既定で入れない）／
  似た案あり（既定で入れる・外せる）／貼り付けた中で重複／個人情報らしい文字（止める）
- **起票経路が空の行は止める**（FR-67）。ただし「空欄の行はすべて○○とする」を人が選べる。推測では埋めない
- 登録は1回のトランザクション。確認した内容に札（内容のハッシュ）を付け、**同じ札では二度登録しない**。
  各行は `source_sheet='一括:<札>'`・`source_key`＝正規化した名前（既存の一意索引が二重登録を防ぐ）
- **取り消しは手が入っていない行だけ消す**（十文字さんの選択）。採点済み・編集済み・案件や枠につながった行は残し、理由を出す
- 登録できるのは 商品開発部・管理者・社長 の業務ロール。1回 300 行まで（似た案の計算は全件と比べるため）
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re

from app import store

EDITORS = ("devdept", "admin", "president")
MAX_ROWS = 300
HEAD = {
    "title": ("商品案名", "商品案", "アイデア名", "アイデア", "タイトル", "名前"),
    "summary": ("概要・仕様", "概要", "仕様"),
    "target_scene": ("想定ターゲット", "ターゲット", "使用シーン"),
    "origin": ("起票経路", "経路"),
    "theme": ("テーマ",),
}
PII = (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), re.compile(r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b"), re.compile(r"\b0\d{9,10}\b"))


def can_edit(user_id: str) -> bool:
    from app import gate
    return bool(set(gate.roles_of(user_id)) & set(EDITORS))


def _need(user_id):
    if not can_edit(user_id):
        raise PermissionError("一括登録できるのは、商品開発部・管理者・社長の業務ロールの人です")


def _parse(text: str) -> list[dict]:
    text = (text or "").strip("﻿\n\r ")
    if not text:
        raise ValueError("貼り付けた内容がありません")
    first = text.split("\n", 1)[0]
    rows = list(csv.reader(io.StringIO(text), delimiter="\t" if "\t" in first else ","))
    head = [h.strip() for h in rows[0]]
    col = {}
    for k, words in HEAD.items():
        for i, h in enumerate(head):
            if h in words and k not in col:
                col[k] = i
    if "title" not in col:
        raise ValueError("1行目に見出しを入れてください（商品案名・概要・想定ターゲット・起票経路・テーマ。商品案名は必須）")
    body = [r for r in rows[1:] if any(c.strip() for c in r)]
    if len(body) > MAX_ROWS:
        raise ValueError(f"1回に登録できるのは {MAX_ROWS} 行までです（{len(body)} 行あります）")
    out = []
    for n, r in enumerate(body, 2):
        g = lambda k: (r[col[k]].strip() if k in col and col[k] < len(r) else "")      # noqa: E731
        out.append({"line": n, **{k: g(k) for k in HEAD}})
    return out


def preview(text: str, default_origin: str = "") -> dict:
    """**何も保存しない。**行ごとの判定と札を返す。"""
    from app import idea
    rows = _parse(text)
    origins = {c: c for c in idea.ORIGIN_CODES}
    origins.update({lab: c for c, lab in idea.ORIGINS})
    if default_origin and default_origin not in idea.ORIGIN_CODES:
        raise ValueError("空欄の行に入れる起票経路が不正です")
    themes = {r["label"]: r["id"] for r in store.q("SELECT id, label FROM theme WHERE kind='評価テーマ'")}
    existing = {}
    for r in store.q("SELECT id, title FROM idea"):
        existing.setdefault(idea.normalize(r["title"]), r["id"])
    seen = {}
    for x in rows:
        why, state = [], "入る"
        key = idea.normalize(x["title"])
        x["key"] = key
        if not x["title"]:
            why.append("商品案名が空です")
        o = x["origin"]
        if o:
            if o not in origins:
                why.append(f"知らない起票経路「{o}」")
            else:
                x["origin_code"] = origins[o]
        elif default_origin:
            x["origin_code"] = default_origin
            x["origin_note"] = "空欄だったので、選んだ経路にしました"
        else:
            why.append("起票経路が空です（空欄の行に入れる経路を選ぶこともできます）")
        if x["theme"]:
            if x["theme"] not in themes:
                why.append(f"知らないテーマ「{x['theme']}」")
            else:
                x["theme_id"] = themes[x["theme"]]
        joined = " ".join(x[k] for k in ("title", "summary", "target_scene"))
        if any(p.search(joined) for p in PII):
            why.append("メールアドレスか電話番号らしい文字があります（お客さまの情報は入れません）")
        if why:
            state = "止まる"
        elif key in seen:
            state, why = "貼り付けた中で重複", [f"{seen[key]}行目と同じ名前です"]
        elif key in existing:
            state, why = "既にある", [f"同じ名前のアイデアがあります（{existing[key]}）"]
        else:
            sim = idea.similar_to_title(x["title"])
            if sim:
                state = "似た案あり"
                x["similar"] = [{"id": s["id"], "title": s["title"], "score": s["score"]} for s in sim[:3]]
        seen.setdefault(key, x["line"])
        x["state"], x["why"] = state, why
        x["include"] = state in ("入る", "似た案あり")
    token = hashlib.sha256(json.dumps([[x["line"], x["title"], x["summary"], x["target_scene"],
                                        x.get("origin_code"), x.get("theme_id")] for x in rows],
                                      ensure_ascii=False).encode()).hexdigest()[:12]
    count = {}
    for x in rows:
        count[x["state"]] = count.get(x["state"], 0) + 1
    return {"token": token, "rows": rows, "count": count,
            "done": store.val("SELECT COUNT(*) FROM idea WHERE source_sheet=?", ("一括:" + token,), 0) > 0}


def register(text: str, default_origin: str, token: str, exclude: list, user_id: str, ip: str = "") -> dict:
    """確認した内容（札が一致するもの）だけを、1回のトランザクションで登録する。"""
    _need(user_id)
    p = preview(text, default_origin)
    if p["token"] != token:
        raise ValueError("確認した内容と違います。もう一度「確認する」を押してください")
    if p["done"]:
        raise ValueError("この内容はもう登録済みです")
    skip = {int(x) for x in exclude or [] if str(x).isdigit()}
    go = [x for x in p["rows"] if x["state"] in ("入る", "似た案あり") and x["line"] not in skip]
    if not go:
        raise ValueError("登録する行がありません")
    sheet, now, ids = "一括:" + token, store.now_s(), []
    from app import idea
    with store.tx() as c:
        for x in go:
            iid = store.new_id("idea")
            c.execute("INSERT INTO idea (id,title,summary,target_scene,origin,origin_note,theme_id,stage,source_sheet,"
                      "source_key,source_row,created_at,created_by,updated_at,updated_by) "
                      "VALUES (?,?,?,?,?,?,?,'起票',?,?,?,?,?,?,?)",
                      (iid, x["title"], x["summary"] or None, x["target_scene"] or None, x["origin_code"],
                       x.get("origin_note"), x.get("theme_id"), sheet, x["key"], x["line"], now, user_id, now, user_id))
            ids.append(iid)
    for iid, x in zip(ids, go):
        idea.cache_similar(iid, idea.similar_to_title(x["title"], exclude_id=iid))
    store.conn().commit()
    store.audit(user_id, "ideas.import", sheet, {"n": len(ids), "token": token,
                                                 "label": f"アイデアの一括登録（{len(ids)}件）"}, ip)
    return {"ok": True, "n": len(ids), "token": token}


def batches() -> list[dict]:
    return store.rows(store.q(
        "SELECT source_sheet, substr(source_sheet,4) AS token, COUNT(*) AS n, MIN(created_at) AS at, MIN(created_by) AS by "
        "FROM idea WHERE source_sheet LIKE '一括:%' GROUP BY source_sheet ORDER BY at DESC"))


def _why_kept(r) -> str | None:
    if r["stage"] != "起票":
        return f"ステージが「{r['stage']}」に進んでいます"
    if store.val("SELECT COUNT(*) FROM idea_score WHERE idea_id=?", (r["id"],), 0):
        return "採点されています"
    # 画面からの操作はすべて監査に残る（idea.fields・idea.score など）。時刻は秒までなので、両方で見る
    if r["updated_at"] != r["created_at"] or store.val(
            "SELECT COUNT(*) FROM audit WHERE target=? AND action LIKE 'idea.%'", (r["id"],), 0):
        return "登録後に直されています"
    if store.val("SELECT COUNT(*) FROM plan_slot WHERE idea_id=?", (r["id"],), 0):
        return "年間プランの枠につながっています"
    if store.val("SELECT COUNT(*) FROM project WHERE idea_id=?", (r["id"],), 0):
        return "案件につながっています"
    return None


def undo(token: str, user_id: str, ip: str = "") -> dict:
    """**手が入っていない行だけ消す**（十文字さんの選択）。残した行は理由つきで返す。"""
    _need(user_id)
    sheet = "一括:" + (token or "")
    rows = store.q("SELECT * FROM idea WHERE source_sheet=?", (sheet,))
    if not rows:
        raise LookupError("その一括登録はありません")
    gone, kept = [], []
    for r in rows:
        why = _why_kept(r)
        (kept if why else gone).append({"id": r["id"], "title": r["title"], "why": why})
    with store.tx() as c:
        for x in gone:
            c.execute("DELETE FROM idea_similar WHERE idea_id=? OR other_id=?", (x["id"], x["id"]))
            c.execute("DELETE FROM idea WHERE id=?", (x["id"],))
    store.audit(user_id, "ideas.import.undo", sheet, {"removed": len(gone), "kept": len(kept),
                                                      "label": f"一括登録を取り消した（消した {len(gone)}・残した {len(kept)}）"}, ip)
    return {"ok": True, "removed": len(gone), "kept": kept}
