#!/usr/bin/env python3
"""
発売済みにしたときの CIP との連携（2026-11-10 石原さんの追加修正依頼書 4-5・十文字さんの選択・ADR-137）。

**いまの CIP の口は「新規登録」と「在るかを確かめる」だけ**（既存の商品を更新する口は無い）。
十文字さんの選択（11-10）で、NEW PRODUCT 側は**確認まで**にする:

- 発売済みにしたとき（G5 を通したとき）、案件の登録の単位（バリエーションがあればその1つずつ）について
  ①共通商品コードが記録されているか ②CIP にそのコードが在るか、を確かめる
  （同じコードを2つの単位に記録することは、seisan_registration の一意の制約で先に止まる）
- 未登録なら、登録票の下書きで足りない項目を出す（**推測で埋めない**）
- G5 より前に CIP の画面で登録した商品は、今の「CIP で登録した」（コードの記録）で結び付ける。発売時に新しく作らない
- 結果は `launch_link`（kind=cip）に残す。**発売済みのステージとは別**。確かめられなかったものだけ「もう一度確かめる」
- 発売日・販売中などを CIP へ**送る**のは、CIP 側に更新の口ができてから（HUB tasks/to-cip.md で依頼）。ここでは送らない
"""
from __future__ import annotations

import json

from app import store

KIND_CIP = "cip"
STATES = ("済", "要確認", "失敗")


def _save(pid: str, kind: str, state: str, summary: str, detail, user_id: str) -> dict:
    now = store.now_s()
    with store.tx() as c:
        c.execute("INSERT INTO launch_link (project_id,kind,state,summary,detail,tries,checked_at,checked_by) "
                  "VALUES (?,?,?,?,?,1,?,?) ON CONFLICT(project_id,kind) DO UPDATE SET state=excluded.state, "
                  "summary=excluded.summary, detail=excluded.detail, tries=launch_link.tries+1, "
                  "checked_at=excluded.checked_at, checked_by=excluded.checked_by",
                  (pid, kind, state, summary[:300], json.dumps(detail, ensure_ascii=False), now, user_id))
    store.audit(user_id, f"launch_link.{kind}", pid, {"state": state, "summary": summary[:300]})
    return {"kind": kind, "state": state, "summary": summary, "detail": detail, "checked_at": now}


def check_cip(pid: str, user_id: str) -> dict:
    """CIP の登録を確かめる。**CIP には書かない**（在るかを読むだけ）。何が起きても例外を外へ出さない。"""
    from app import seisan
    try:
        targets = seisan._targets(pid)
        ok, why = seisan.configured()
        items = []
        for t in targets:
            r = seisan._row(pid, t["variant_id"]) or {}
            it = {"label": t["label"], "code": r.get("product_code") if r.get("state") == "登録済" else None}
            if not it["code"]:
                d = json.loads(r["draft_json"]) if r.get("draft_json") else {}
                errs = [e for e in seisan.validate(d, seisan.vocab())[0]] if d else ["登録票の下書きがありません"]
                it.update(state="未登録", why="CIP に登録されていません" + ("（不足: " + "／".join(errs[:6]) + "）" if errs else ""))
            elif not ok:
                it.update(state="確かめられない", why=why or "CIP の口が使えません")
            else:
                try:
                    it.update(state="CIP に在る" if seisan.exists(it["code"]) else "CIP に無い")
                    if it["state"] == "CIP に無い":
                        it["why"] = f"共通商品コード「{it['code']}」が CIP に見つかりません（消された・打ち間違いの疑い）"
                except Exception as e:                     # noqa: BLE001 口の不調。発売済みは戻さない
                    it.update(state="確かめられない", why=f"CIP に問い合わせられませんでした（{type(e).__name__}）")
            items.append(it)
        bad = [i for i in items if i["state"] != "CIP に在る"]
        unsure = [i for i in bad if i["state"] == "確かめられない"]
        if not bad:
            state, summary = "済", f"CIP の登録を確かめました（{len(items)} 件すべて CIP に在る）"
        elif len(unsure) == len(bad):
            state, summary = "失敗", f"CIP に問い合わせられませんでした（{len(unsure)} 件）。あとで「もう一度確かめる」"
        else:
            state, summary = "要確認", f"CIP の登録に確認が要るものがあります（{len(bad)} / {len(items)} 件）"
        detail = {"items": items, "sent": None,
                  "note": "発売日などを CIP へ送る口はまだありません（CIP 側に依頼中）。ここでは登録の確認だけをしています"}
        return _save(pid, KIND_CIP, state, summary, detail, user_id)
    except Exception as e:                                 # noqa: BLE001
        return _save(pid, KIND_CIP, "失敗", f"確かめる途中で止まりました（{type(e).__name__}）", {"items": []}, user_id)


def on_launched(pid: str, user_id: str) -> dict:
    return {"cip": check_cip(pid, user_id)}


def of_project(pid: str) -> list[dict]:
    out = []
    for r in store.q("SELECT * FROM launch_link WHERE project_id=? ORDER BY kind", (pid,)):
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        out.append(d)
    return out


def retry(pid: str, kind: str, user_id: str) -> dict:
    """失敗・要確認のものを、もう一度。**済のものはやり直さない**（同じ処理を重ねない）"""
    if store.one("SELECT 1 FROM project WHERE id=?", (pid,)) is None:
        raise LookupError("案件がありません")
    r = store.one("SELECT state FROM launch_link WHERE project_id=? AND kind=?", (pid, kind))
    if kind != KIND_CIP:
        raise ValueError("その連携はありません")
    if r is not None and r["state"] == "済":
        raise ValueError("もう済んでいます")
    return check_cip(pid, user_id)
