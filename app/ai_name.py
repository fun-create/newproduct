#!/usr/bin/env python3
"""
商品名の案出し（FR-149 の1つ目・2026-10-09 十文字さんの選択・ADR-082）。

採点の案（`app/ai_score.py`・ADR-081）と同じ作法:
- **AI は案を出すだけ。**カルテの「商品名の検討」（F.name）には書かない。人が選んだ名前だけを1行ずつ足す
- 渡すのはカルテの文面（分類・社内呼称・概要・ターゲット・ニーズ・差別化・コンセプト・これまでの検討）だけ。
  CIP の商品名は読まない（N-6-2）。お客さまの情報は扱わない
- 名前ごとに「なぜこの名前か」を付けさせ、根拠の無い名前は案にしない。**商標の登録状況は AI には確かめられない**ので、
  必ず「未確認」に並べる（FR-148）
- 実際に答えたモデル・日時・渡した項目を残す（FR-147）。予算は `newproduct-text` に記録し、記録できなければ保存しない

**このファイル自体は外部と通信しない。**呼ぶのは `ai_cli`（`claude` を子プロセスで起動）。
"""
from __future__ import annotations

import json
import re
import threading

from . import ai_budget
from . import ai_cli
from . import ai_score
from . import store

KIND = "name"
N_CANDIDATES = 8
SECTION_LIMIT = 1500          # 1節あたり渡す字数（長い準備シートをそのまま渡さない）
INPUT_SECTIONS = (("C.target", "ターゲット・使用シーン"), ("C.needs", "ニーズ"), ("C.diff", "差別化"),
                  ("C.concept", "コンセプト"), ("F.name", "これまでの商品名の検討"))
TRADEMARK = "商標の登録状況は未確認（登録の前に調べてください）"

SYSTEM = """あなたは FUN-CREATE株式会社（愛知県西尾市）の新商品の商品名づけの補助です。
同社は推し活・ライフイベント向けのカスタムグッズ（アクリルスタンド・うちわ・オリジナルグッズ）を自社で製造し、
自社ECと楽天・Amazon・Yahoo!ショッピング・ギフトモールで販売しています。

役割: 渡されたカルテの文面から、商品名の「案」を出します。決めるのは人です。

守ること:
- 他社の商標・ブランド名・キャラクター名・作品名・人名を商品名に入れない
- 「日本初」「No.1」「最高」など、確かめられない言い切りを入れない
- 何の商品か（分類）が名前から分かるようにする。1つの名前は40字以内
- 名前ごとに、なぜその名前かを1文・60字以内で書く（カルテのどの点に合わせたか）
- 確かめないと言えないことは unverified に短く並べる
- 答えは JSON のオブジェクトだけ。前後に文章やコードブロックを付けない"""


def can_run(user_id: str) -> bool:
    return ai_score.can_run(user_id)


def inputs(project_id: str) -> dict:
    from . import project as project_m
    p = store.one("SELECT * FROM project WHERE id=?", (project_id,))
    if p is None:
        raise LookupError("案件がありません")
    p = dict(p)
    secs = {r["section_key"]: (r["body"] or "").strip() for r in store.q(
        "SELECT section_key, body FROM project_section WHERE project_id=?", (project_id,))}
    d = {"分類": project_m.product_label(p)}
    for k, lab in (("internal_name", "社内呼称"), ("summary", "概要")):
        if (p.get(k) or "").strip():
            d[lab] = p[k].strip()
    for k, lab in INPUT_SECTIONS:
        if secs.get(k):
            d[lab] = secs[k][:SECTION_LIMIT]
    return d


def build_prompt(inp: dict) -> str:
    return (f"次のカルテの新商品に、商品名の案を{N_CANDIDATES}つ出してください。"
            "分かりやすさ重視のものと、世界観重視のものを混ぜてください。"
            "「これまでの商品名の検討」にある名前と同じものは出さないでください。\n\n"
            '答えの形: {"candidates":[{"name":"…","why":"…"}],"unverified":["…"]}\n\n'
            "カルテ:\n" + json.dumps(inp, ensure_ascii=False, indent=1))


def parse_answer(text: str) -> dict:
    m = re.search(r"\{.*\}", text.strip(), re.S)
    if not m:
        raise ai_score.EnvFailure("返事に JSON がありませんでした")
    try:
        d = json.loads(m.group(0))
    except ValueError:
        raise ai_score.EnvFailure("返事の JSON が読めませんでした") from None
    cands, seen = [], set()
    for c in d.get("candidates") or []:
        if not isinstance(c, dict):
            continue
        name, why = str(c.get("name") or "").strip(), str(c.get("why") or "").strip()
        if not name or not why or len(name) > 60 or name in seen:     # 根拠の無い名前は案にしない
            continue
        seen.add(name)
        cands.append({"name": name, "why": why[:120]})
    if not cands:
        raise ai_score.EnvFailure("根拠つきの名前が1つもありませんでした")
    unv = [str(x).strip()[:120] for x in d.get("unverified") or [] if str(x).strip()]
    if TRADEMARK not in unv:
        unv.insert(0, TRADEMARK)                                       # AI には確かめられない
    return {"candidates": cands[:12], "unverified": unv[:10]}


def start_run(project_id: str, user_id: str, *, runner=None, sync: bool = False, force: bool = False) -> dict:
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (project_id,))
    if p is None:
        raise LookupError("案件がありません")
    if not force:
        if not can_run(user_id):
            raise PermissionError("AI に案を出させられるのは、商品開発部・管理者・社長の業務ロールの人です")
        if p["source_of_truth"] != "app":
            raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
        b, pre = ai_budget.check(ai_score.JOB), ai_cli.preflight()
        if not ai_score.enabled(b, pre):
            return {"started": False, "reason": ai_score.reason_off(b, pre)}
        if store.val("SELECT COUNT(*) FROM ai_run WHERE stage IN ('待ち','実行中')", (), 0):
            return {"started": False, "reason": "いま別の回が走っています。終わってから押してください"}
    with store.tx() as c:
        rid = c.execute("INSERT INTO ai_run (job,kind,stage,target_json,requested_by,requested_at) "
                        "VALUES (?,?,?,?,?,?)", (ai_score.JOB, KIND, "待ち", json.dumps([project_id]),
                                                 user_id, store.now_s())).lastrowid
    if sync:
        _work(rid, project_id, runner, record=not force)
    else:
        threading.Thread(target=_work, args=(rid, project_id, runner), daemon=True, name=f"ai-name-{rid}").start()
    return {"started": True, "run_id": rid, "n": 1}


def _work(run_id: int, project_id: str, runner=None, record: bool = True) -> None:
    store.ex("UPDATE ai_run SET stage='実行中' WHERE id=?", (run_id,))
    store.conn().commit()
    model, cost = None, 0.0
    try:
        inp = inputs(project_id)
        r = ai_cli.ask(build_prompt(inp), SYSTEM, runner=runner)
        model, cost = r["model"], float(r.get("cost_usd") or 0.0)
        if not r["ok"]:
            raise ai_score.EnvFailure(r["error"])
        got = parse_answer(r["text"])
        if record:
            try:
                ai_budget.record(ai_score.JOB, model, cost, note=f"商品名の案 回{run_id}",
                                 request_id=f"newproduct-ai-run{run_id}")
            except (ai_budget.BudgetUnavailable, ai_budget.OverCap) as e:
                raise ai_score.EnvFailure(f"使った額を記録できませんでした: {e}") from None
        now = store.now_s()
        with store.tx() as c:
            c.execute("UPDATE project_ai_proposal SET state='見送り', decided_by='（自動）', decided_at=?, "
                      "decided_note='新しい案に置き換えた' WHERE project_id=? AND kind=? AND state='提案'",
                      (now, project_id, KIND))
            c.execute("INSERT INTO project_ai_proposal (project_id,run_id,kind,body,unverified,inputs,model,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (project_id, run_id, KIND, json.dumps({"candidates": got["candidates"]}, ensure_ascii=False),
                       json.dumps(got["unverified"], ensure_ascii=False),
                       json.dumps({"出所": "NEW PRODUCT の案件カルテ", "項目": list(inp.keys())}, ensure_ascii=False),
                       model, now))
        ai_score._finish(run_id, "完了", 1, 0, model, cost)
    except ai_score.EnvFailure as e:
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "env", str(e))
    except Exception as e:                                   # 想定外も「実行中」のまま残さない
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "bug", f"{type(e).__name__}: {e}")


def _prop(r) -> dict:
    d = dict(r)
    d["candidates"] = json.loads(d.pop("body")).get("candidates", [])
    d["unverified"] = json.loads(d["unverified"]) if d.get("unverified") else []
    d["inputs"] = json.loads(d["inputs"]) if d.get("inputs") else {}
    return d


def proposals_of(project_id: str) -> list[dict]:
    return [_prop(r) for r in store.q("SELECT * FROM project_ai_proposal WHERE project_id=? AND kind=? "
                                      "ORDER BY id DESC LIMIT 5", (project_id, KIND))]


def adopt(proposal_id: int, user_id: str, picks: list[int]) -> dict:
    """選んだ名前だけを「商品名の検討」（F.name）の末尾に1行ずつ足す。**既に書いてある行は消さない。**"""
    from . import project as project_m
    p = store.one("SELECT * FROM project_ai_proposal WHERE id=?", (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    cands = json.loads(p["body"]).get("candidates", [])
    names = []
    for i in picks:
        if not 0 <= i < len(cands):
            raise ValueError("選んだ番号がありません")
        if cands[i]["name"] not in names:
            names.append(cands[i]["name"])
    if not names:
        raise ValueError("名前を1つ以上選んでください")
    cur = store.one("SELECT body FROM project_section WHERE project_id=? AND section_key='F.name'", (p["project_id"],))
    body = ((cur["body"] if cur else "") or "").rstrip()
    have = {ln.strip() for ln in body.splitlines()}
    add = [n for n in names if n not in have]
    if add:
        project_m.save_section(p["project_id"], "F.name", (body + "\n" if body else "") + "\n".join(add), user_id)
    note = f"AI案（{p['model']}・{p['created_at']}）から採用: " + "、".join(names)
    with store.tx() as c:
        c.execute("UPDATE project_ai_proposal SET state='採用', decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (user_id, store.now_s(), note, proposal_id))
    return {"added": add, "already": [n for n in names if n not in add], "project_id": p["project_id"]}


def reject(proposal_id: int, user_id: str, note: str = "") -> dict:
    p = store.one("SELECT * FROM project_ai_proposal WHERE id=?", (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    with store.tx() as c:
        c.execute("UPDATE project_ai_proposal SET state='見送り', decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (user_id, store.now_s(), (note or "").strip() or None, proposal_id))
    return {"ok": True, "project_id": p["project_id"]}
