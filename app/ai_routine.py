#!/usr/bin/env python3
"""
繰り返しの仕事の自動化の AI 案（FR-201・ADR-116・2026-10-19 十文字さんの選択）。

本番の目視チェックで「AIによる自動化提案機能がほしい」。繰り返しの仕事（ADR-108）それぞれに、
AI が「自動化できるか・どうやって・人が残すところ・効果の大きさ」の案を出す:

- **AI は案を出すだけ**（FR-145）。案は `routine_ai_proposal` に置く。人が「自動化依頼にする」を押したときだけ、
  自動化依頼（FR-159〜）の下書きができる（ChatWork へは送らない。送るのは依頼の画面で人が押したとき）
- **時間（分）は作らせない。**効果は「大・中・小」の語だけ。月あたりの時間は、自動化依頼の
  「1回あたり何分 × 月に何回」（FR-165）で人が入れる（N-10）
- 渡すのは、繰り返しの仕事の名前・時期・実行理由・マニュアルの名前・担当ロール・1回の時間（人が入れた値）だけ。
  マニュアルの URL の中身は見られない（道具を渡していない）
- 確かめないと言えないことは「未確認」に並べさせる。予算は `newproduct-text` に記録し、記録できなければ保存しない
"""
from __future__ import annotations

import json
import re
import threading

from . import ai_budget
from . import ai_cli
from . import ai_score
from . import routine as routine_m
from . import store

KIND = "routine_auto"
VERDICTS = ("できる", "一部できる", "難しい")
IMPACTS = ("大", "中", "小")
MAX_PER_RUN = 40

SYSTEM = """あなたは FUN-CREATE株式会社（愛知県西尾市）の業務改善の補助です。
同社は推し活・ライフイベント向けのカスタムグッズを自社で製造し、自社ECと楽天・Amazon・Yahoo!ショッピング・ギフトモールで販売しています。
社内には Google スプレッドシート・ChatWork・自社の業務アプリ（新商品開発・売上の集計・カレンダー）があり、AI（Claude）も使えます。

役割: 商品開発部の繰り返しの仕事それぞれについて、自動化（または AI で軽く）できるかの「案」を出します。決めるのは人です。

守ること:
- 渡された仕事の情報と、一般に知られていることだけから書く
- 時間（分・時間）・件数・金額などの数字を作らない。効果は impact に「大」「中」「小」のどれかで書く
- 確かめないと言えないこと（使っている画面や API があるか・データの置き場など）は unverified に短く並べる
- 判断・承認・お客様に出る文面の最終確認など、人が残すべきところは keep_human に書く
- 各欄は1〜3文・120字以内
- 答えは JSON の配列だけ。前後に文章やコードブロックを付けない"""


def _item(r: dict) -> dict:
    d = {"id": r["id"], "仕事": r["title"], "時期": routine_m.rule_text(r)}
    for k, lab in (("reason", "実行理由"), ("manual", "マニュアル・記入シート"), ("role_label", "担当ロール")):
        v = r.get(k)
        if v and str(v).strip():
            d[lab] = str(v).strip()[:200]
    if r.get("hours") is not None:
        d["1回の時間（人が入れた値・時間）"] = r["hours"]
    return d


def _routines(ids: list[int] | None = None) -> list[dict]:
    rows = store.rows(store.q("SELECT r.*, ro.label AS role_label FROM routine r LEFT JOIN role ro ON ro.code=r.role ORDER BY r.id"))
    return [r for r in rows if ids is None or r["id"] in ids]


def candidates() -> list[int]:
    """既定の対象: 案がまだ無い（提案中・依頼にした・見送り のどれも無い）繰り返しの仕事。"""
    done = {r[0] for r in store.q("SELECT DISTINCT routine_id FROM routine_ai_proposal")}
    return [r["id"] for r in _routines() if r["id"] not in done]


def build_prompt(items: list[dict]) -> str:
    return ("次の繰り返しの仕事それぞれについて、自動化できるかの案を出してください。\n\n"
            "答えの形（配列・仕事の数だけ）:\n"
            '[{"id":1,"verdict":"できる|一部できる|難しい","how":"どう自動化できるか",'
            '"keep_human":"人が残すところ","impact":"大|中|小","reason":"効果をそう見た理由","unverified":["…"]}]\n\n'
            "仕事:\n" + json.dumps(items, ensure_ascii=False, indent=1))


def parse_answer(text: str, ids: list[int]) -> dict:
    m = re.search(r"\[.*\]", (text or "").strip(), re.S)
    if not m:
        raise ai_score.EnvFailure("返事に JSON の配列がありませんでした")
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        raise ai_score.EnvFailure("返事の JSON が読めませんでした") from None
    out = {}
    for x in arr if isinstance(arr, list) else []:
        try:
            k = int(x.get("id")) if isinstance(x, dict) else None
        except (TypeError, ValueError):
            k = None
        if k in ids:
            out[k] = x
    for i in ids:
        out.setdefault(i, {"error": "返事にこの仕事がありませんでした"})
    return out


def check(res: dict) -> dict | str:
    """1件分を確かめる。**決めた語の外は案にしない**（推測で直さない）。how が無ければ案にしない。"""
    if "error" in res:
        return res["error"]
    v = str(res.get("verdict") or "").strip()
    if v not in VERDICTS:
        return f"できるかどうかが決めた語の外です（{v!r}）"
    imp = str(res.get("impact") or "").strip()
    if imp not in IMPACTS:
        return f"効果の大きさが決めた語の外です（{imp!r}）"
    how = str(res.get("how") or "").strip()
    if not how:
        return "どう自動化できるかが空です"
    unv = [str(x).strip()[:120] for x in (res.get("unverified") or []) if str(x).strip()][:8]
    return {"verdict": v, "impact": imp, "how": how[:300], "keep_human": str(res.get("keep_human") or "").strip()[:300] or None,
            "reason": str(res.get("reason") or "").strip()[:300] or None, "unverified": unv}


def start_run(routine_ids: list[int] | None, user_id: str, *, runner=None, sync: bool = False,
              force: bool = False) -> dict:
    if not force:
        if not routine_m.can_edit(user_id):
            raise PermissionError("AI に自動化の案を出させられるのは、商品開発部・管理者・社長の業務ロールの人です")
        b, pre = ai_budget.check(ai_score.JOB), ai_cli.preflight()
        if not ai_score.enabled(b, pre):
            return {"started": False, "reason": ai_score.reason_off(b, pre)}
        if store.val("SELECT COUNT(*) FROM ai_run WHERE stage IN ('待ち','実行中')", (), 0):
            return {"started": False, "reason": "いま別の回が走っています。終わってから押してください"}
    ids = list(dict.fromkeys(int(x) for x in (routine_ids or candidates())))
    if not ids:
        return {"started": False, "reason": "対象の繰り返しの仕事がありません（案のまだ無いものが0件）"}
    if len(ids) > MAX_PER_RUN:
        return {"started": False, "reason": f"1回に出せるのは {MAX_PER_RUN} 件までです（{len(ids)} 件）"}
    with store.tx() as c:
        rid = c.execute("INSERT INTO ai_run (job,kind,stage,target_json,requested_by,requested_at) VALUES (?,?,?,?,?,?)",
                        (ai_score.JOB, KIND, "待ち", json.dumps(ids), user_id, store.now_s())).lastrowid
    if sync:
        _work(rid, runner, record=not force)
    else:
        threading.Thread(target=_work, args=(rid, runner), daemon=True, name=f"ai-routine-{rid}").start()
    return {"started": True, "run_id": rid, "n": len(ids)}


def _work(run_id: int, runner=None, record: bool = True) -> None:
    store.ex("UPDATE ai_run SET stage='実行中' WHERE id=?", (run_id,))
    store.conn().commit()
    ids = json.loads(store.val("SELECT target_json FROM ai_run WHERE id=?", (run_id,)))
    model, cost, ok, ng = None, 0.0, 0, 0
    try:
        rows = _routines(ids)
        ng += len(ids) - len(rows)
        if not rows:
            raise ai_score.EnvFailure("対象の繰り返しの仕事がありません")
        items = [_item(r) for r in rows]
        r = ai_cli.ask(build_prompt(items), SYSTEM, runner=runner)
        model, cost = r.get("model"), float(r.get("cost_usd") or 0.0)
        if not r["ok"]:
            raise ai_score.EnvFailure(r["error"])
        res = parse_answer(r["text"], [x["id"] for x in rows])
        if record:
            try:
                ai_budget.record(ai_score.JOB, model, cost, note=f"繰り返しの仕事の自動化案 回{run_id}",
                                 request_id=f"newproduct-ai-run{run_id}")
            except (ai_budget.BudgetUnavailable, ai_budget.OverCap) as e:
                raise ai_score.EnvFailure(f"使った額を記録できませんでした: {e}") from None
        now = store.now_s()
        with store.tx() as c:
            for row, item in zip(rows, items):
                got = check(res.get(row["id"]) or {"error": "返事がありません"})
                if isinstance(got, str):
                    ng += 1
                    continue
                c.execute("UPDATE routine_ai_proposal SET state='見送り', decided_by='（自動）', decided_at=?, "
                          "decided_note='新しい案に置き換えた' WHERE routine_id=? AND state='提案'", (now, row["id"]))
                c.execute("INSERT INTO routine_ai_proposal (routine_id,run_id,verdict,how,keep_human,impact,reason,unverified,"
                          "inputs,model,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (row["id"], run_id, got["verdict"], got["how"], got["keep_human"], got["impact"], got["reason"],
                           json.dumps(got["unverified"], ensure_ascii=False),
                           json.dumps({"出所": "NEW PRODUCT の繰り返しの仕事", "項目": item}, ensure_ascii=False), model, now))
                ok += 1
        ai_score._finish(run_id, "完了", ok, ng, model, cost)
    except ai_score.EnvFailure as e:
        ai_score._finish(run_id, "失敗", ok, ng, model, cost, "env", str(e))
    except Exception as e:                                   # 想定外も「実行中」のまま残さない
        ai_score._finish(run_id, "失敗", ok, ng, model, cost, "bug", f"{type(e).__name__}: {e}")


def _prop(r) -> dict:
    d = dict(r)
    d["unverified"] = json.loads(d["unverified"]) if d.get("unverified") else []
    d["inputs"] = json.loads(d["inputs"]) if d.get("inputs") else {}
    return d


def latest_by_routine() -> dict:
    """繰り返しの仕事ごとの、いちばん新しい案（提案中・依頼にした・見送り のどれでも）。"""
    out = {}
    for r in store.q("SELECT * FROM routine_ai_proposal ORDER BY id"):
        out[r["routine_id"]] = _prop(r)
    return out


def to_request(proposal_id: int, user_id: str) -> dict:
    """案から自動化依頼の下書きを作る（**送らない**）。作業名は繰り返しの仕事の名前（同名があれば「（繰り返し）」を付ける）。"""
    from . import automation
    if not routine_m.can_edit(user_id):
        raise PermissionError("自動化依頼にできるのは、商品開発部・管理者・社長の業務ロールの人です")
    p = store.one("SELECT p.*, r.title AS rtitle, r.hours AS rhours FROM routine_ai_proposal p JOIN routine r ON r.id=p.routine_id WHERE p.id=?",
                  (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    title = p["rtitle"]
    if store.one("SELECT 1 FROM automation_request WHERE title=?", (title,)):
        title = f"{title}（繰り返しの仕事から）"
    raw = (f"繰り返しの仕事「{p['rtitle']}」を自動化したい。\n"
           f"AI の案（{p['model']}・{p['created_at']}・人が確かめる前のもの）: {p['how']}"
           + (f"\n人が残すところ（AI の案）: {p['keep_human']}" if p["keep_human"] else ""))
    r = automation.create(user_id, title=title, raw_request=raw, requester=user_id, dept="商品開発部",
                          note=f"繰り返しの仕事 #{p['routine_id']} の AI 案 #{proposal_id} から作った下書き")
    with store.tx() as c:
        c.execute("UPDATE routine_ai_proposal SET state='依頼にした', request_id=?, decided_by=?, decided_at=? WHERE id=?",
                  (r["id"], user_id, store.now_s(), proposal_id))
    return {"ok": True, "request_id": r["id"], "title": title}


def reject(proposal_id: int, user_id: str, note: str = "") -> dict:
    if not routine_m.can_edit(user_id):
        raise PermissionError("見送れるのは、商品開発部・管理者・社長の業務ロールの人です")
    p = store.one("SELECT * FROM routine_ai_proposal WHERE id=?", (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    with store.tx() as c:
        c.execute("UPDATE routine_ai_proposal SET state='見送り', decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (user_id, store.now_s(), (note or "").strip() or None, proposal_id))
    return {"ok": True}


def status(user_id: str | None = None) -> dict:
    pre = ai_cli.preflight()
    b = ai_budget.check(ai_score.JOB)
    on = ai_score.enabled(b, pre)
    last = store.one("SELECT * FROM ai_run WHERE kind=? ORDER BY id DESC LIMIT 1", (KIND,))
    return {"enabled": on, "reason": None if on else ai_score.reason_off(b, pre),
            "can_run": bool(user_id) and routine_m.can_edit(user_id), "candidates": len(candidates()),
            "pending": store.val("SELECT COUNT(*) FROM routine_ai_proposal WHERE state='提案'", (), 0),
            "last_run": ai_score.run_view(last["id"]) if last else None}
