#!/usr/bin/env python3
"""
カルテの下書きを AI に出させる（FR-149 の2つ目・3つ目・2026-10-09 十文字さんの選択・ADR-087）。

- `lp`（LP依頼書）: キャッチコピーの案・ページの構成案・よくある質問の案。足す先は「LP依頼用」（F.lp）
- `competitor`（競合調査）: 比べる観点・探す競合の種類と検索語・いまの表から読めること。足す先は「競合調査」（C.competitor）
  **AI はウェブを見られない**（道具を渡していない）ので、競合の店名・価格・URL・レビュー件数は作らせない。
  競合の表（出典と確認日が必須・FR-141）に入れるのは人が調べた値だけ

採点の案・商品名の案（ADR-081・082）と同じ作法:
- **AI は案を出すだけ。**カルテには書かない。人が文面を直して「欄に足す」を押したときだけ、欄の末尾に足す（書いてある文は消さない）
- 渡すのはカルテの文面だけ（CIP の商品名は読まない・お客さまの情報は無い）。渡した項目・モデル・日時を残す
- 確かめないと言えないことは「未確認」に並べさせる。予算は `newproduct-text` に記録し、記録できなければ保存しない
"""
from __future__ import annotations

import json
import re
import threading

from . import ai_budget
from . import ai_cli
from . import ai_score
from . import store

SECTION_LIMIT = 1500

COMMON = """あなたは FUN-CREATE株式会社（愛知県西尾市）の新商品開発の補助です。
同社は推し活・ライフイベント向けのカスタムグッズ（アクリルスタンド・うちわ・オリジナルグッズ）を自社で製造し、
自社ECと楽天・Amazon・Yahoo!ショッピング・ギフトモールで販売しています。

役割: 渡されたカルテの文面から下書きの「案」を出します。決めるのは人です。

守ること:
- カルテに書いてあることと、一般に知られていることだけから書く。売上・件数・順位・価格などの数字を作らない
- ウェブも社内データも見られない。確かめないと言えないことは unverified に短く並べる
- 他社の商標・キャラクター名・作品名を使わない。「日本初」「No.1」「最高」など確かめられない言い切りをしない
- トーンは落ち着いた専門性・誠実さ・推し活文化へのリスペクト。丁寧語ベースで、過度な敬語は避ける
- 答えは JSON のオブジェクトだけ。前後に文章やコードブロックを付けない"""

KINDS = {
    "lp": {
        "label": "LP依頼書の下書き", "target": "F.lp", "target_label": "LP依頼用",
        "inputs": (("C.concept", "コンセプト"), ("C.target", "ターゲット・使用シーン"), ("C.needs", "ニーズ"),
                   ("C.diff", "差別化"), ("F.name", "商品名の検討"), ("F.lp", "これまでの LP依頼の内容")),
        "ask": ("このカルテの新商品の LP（商品ページ）を作る人に渡す、依頼の下書きを出してください。\n"
                "sections は次の3つ: 「キャッチコピーの案」（5つ・1行ずつ）、「ページの構成案」（上から順に、各ブロックで伝えること）、"
                "「よくある質問の案」（5つ・問いと答えの方向）。答えに数字や事実が要る所は「（要確認）」と書く。"),
    },
    "competitor": {
        "label": "競合調査の下書き", "target": "C.competitor", "target_label": "競合調査",
        "inputs": (("C.target", "ターゲット・使用シーン"), ("C.needs", "ニーズ"), ("C.diff", "差別化"),
                   ("C.concept", "コンセプト"), ("C.competitor", "これまでの競合調査のメモ")),
        "ask": ("このカルテの新商品について、競合調査の進め方の下書きを出してください。\n"
                "sections は次の3つ: 「比べる観点」（価格・仕様・デザイン傾向・レビューのほか、この商品で特に見るべき点）、"
                "「探す競合の種類と検索語」（どんな商品・店を探すか、モールで打つ検索語の案）、"
                "「いまの表から読めること」（渡した競合の表から言えること。表が空なら「表がまだ空です」）。\n"
                "**競合の店名・商品名・価格・URL・レビュー件数を作らない。**探し方だけを書く。"),
    },
}


def inputs(project_id: str, kind: str) -> dict:
    from . import cost, project as project_m
    k = KINDS[kind]
    p = store.one("SELECT * FROM project WHERE id=?", (project_id,))
    if p is None:
        raise LookupError("案件がありません")
    p = dict(p)
    secs = {r["section_key"]: (r["body"] or "").strip() for r in store.q(
        "SELECT section_key, body FROM project_section WHERE project_id=?", (project_id,))}
    d = {"分類": project_m.product_label(p)}
    for col, lab in (("internal_name", "社内呼称"), ("summary", "概要"), ("launch_date", "発売予定日"),
                     ("occasion", "機会（なぜその日か）")):
        if (p.get(col) or "").strip():
            d[lab] = p[col].strip()
    v = store.one("SELECT * FROM cost_version WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,))
    if v is not None and v["price_ex_tax"] is not None:
        rate = v["tax_rate"] if v["tax_rate"] is not None else cost.TAX_DEFAULT
        d["販売価格（試算）"] = f"{round(v['price_ex_tax'] * (1 + rate / 100)):,}円（税込）"
    for key, lab in k["inputs"]:
        if secs.get(key):
            d[lab] = secs[key][:SECTION_LIMIT]
    rows = store.q("SELECT shop, item, channel, price_yen, spec, design, review_count, review_avg, checked_on "
                   "FROM competitor_item WHERE project_id=? ORDER BY id LIMIT 20", (project_id,))
    if rows:
        d["競合の表（人が調べた値）"] = [{kk: r[kk] for kk in r.keys() if r[kk] not in (None, "")} for r in rows]
    return d


def build_prompt(kind: str, inp: dict) -> str:
    return (KINDS[kind]["ask"] + "\n\n"
            '答えの形: {"sections":[{"title":"…","body":"…"}],"unverified":["…"]}\n\n'
            "カルテ:\n" + json.dumps(inp, ensure_ascii=False, indent=1))


def parse_answer(text: str) -> dict:
    m = re.search(r"\{.*\}", text.strip(), re.S)
    if not m:
        raise ai_score.EnvFailure("返事に JSON がありませんでした")
    try:
        d = json.loads(m.group(0))
    except ValueError:
        raise ai_score.EnvFailure("返事の JSON が読めませんでした") from None
    secs = []
    for s in d.get("sections") or []:
        if not isinstance(s, dict):
            continue
        t, b = str(s.get("title") or "").strip(), s.get("body")
        if isinstance(b, list):
            b = "\n".join(str(x) for x in b)
        b = str(b or "").strip()
        if t and b:
            secs.append({"title": t[:60], "body": b[:3000]})
    if not secs:
        raise ai_score.EnvFailure("中身のある下書きがありませんでした")
    unv = [str(x).strip()[:120] for x in d.get("unverified") or [] if str(x).strip()]
    return {"sections": secs[:6], "unverified": unv[:10]}


def start_run(project_id: str, kind: str, user_id: str, *, runner=None, sync: bool = False,
              force: bool = False) -> dict:
    if kind not in KINDS:
        raise ValueError(f"知らない下書きの種類 {kind!r}")
    p = store.one("SELECT source_of_truth FROM project WHERE id=?", (project_id,))
    if p is None:
        raise LookupError("案件がありません")
    if not force:
        if not ai_score.can_run(user_id):
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
                        "VALUES (?,?,?,?,?,?)", (ai_score.JOB, kind, "待ち", json.dumps([project_id]),
                                                 user_id, store.now_s())).lastrowid
    if sync:
        _work(rid, project_id, kind, runner, record=not force)
    else:
        threading.Thread(target=_work, args=(rid, project_id, kind, runner), daemon=True,
                         name=f"ai-{kind}-{rid}").start()
    return {"started": True, "run_id": rid, "n": 1}


def _work(run_id: int, project_id: str, kind: str, runner=None, record: bool = True) -> None:
    store.ex("UPDATE ai_run SET stage='実行中' WHERE id=?", (run_id,))
    store.conn().commit()
    model, cost = None, 0.0
    try:
        inp = inputs(project_id, kind)
        r = ai_cli.ask(build_prompt(kind, inp), COMMON, runner=runner)
        model, cost = r["model"], float(r.get("cost_usd") or 0.0)
        if not r["ok"]:
            raise ai_score.EnvFailure(r["error"])
        got = parse_answer(r["text"])
        if record:
            try:
                ai_budget.record(ai_score.JOB, model, cost, note=f"{KINDS[kind]['label']} 回{run_id}",
                                 request_id=f"newproduct-ai-run{run_id}")
            except (ai_budget.BudgetUnavailable, ai_budget.OverCap) as e:
                raise ai_score.EnvFailure(f"使った額を記録できませんでした: {e}") from None
        now = store.now_s()
        with store.tx() as c:
            c.execute("UPDATE project_ai_proposal SET state='見送り', decided_by='（自動）', decided_at=?, "
                      "decided_note='新しい案に置き換えた' WHERE project_id=? AND kind=? AND state='提案'",
                      (now, project_id, kind))
            c.execute("INSERT INTO project_ai_proposal (project_id,run_id,kind,body,unverified,inputs,model,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (project_id, run_id, kind, json.dumps({"sections": got["sections"]}, ensure_ascii=False),
                       json.dumps(got["unverified"], ensure_ascii=False),
                       json.dumps({"出所": "NEW PRODUCT の案件カルテ", "項目": list(inp.keys())}, ensure_ascii=False),
                       model, now))
        ai_score._finish(run_id, "完了", 1, 0, model, cost)
    except ai_score.EnvFailure as e:
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "env", str(e))
    except Exception as e:                                   # 想定外も「実行中」のまま残さない
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "bug", f"{type(e).__name__}: {e}")


def as_text(sections: list[dict]) -> str:
    return "\n\n".join(f"【{s['title']}】\n{s['body']}" for s in sections)


def _prop(r) -> dict:
    d = dict(r)
    d["sections"] = json.loads(d.pop("body")).get("sections", [])
    d["text"] = as_text(d["sections"])
    d["unverified"] = json.loads(d["unverified"]) if d.get("unverified") else []
    d["inputs"] = json.loads(d["inputs"]) if d.get("inputs") else {}
    return d


def proposals_of(project_id: str) -> dict:
    out = {}
    for k in KINDS:
        out[k] = [_prop(r) for r in store.q("SELECT * FROM project_ai_proposal WHERE project_id=? AND kind=? "
                                            "ORDER BY id DESC LIMIT 5", (project_id, k))]
    return out


def adopt(proposal_id: int, user_id: str, text: str) -> dict:
    """人が直した文面を、足す先の欄の末尾に足す。**書いてある文は消さない。**"""
    from . import project as project_m
    p = store.one("SELECT * FROM project_ai_proposal WHERE id=?", (proposal_id,))
    if p is None or p["kind"] not in KINDS:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    text = (text or "").strip()
    if not text:
        raise ValueError("足す文面が空です")
    k = KINDS[p["kind"]]
    cur = store.one("SELECT body FROM project_section WHERE project_id=? AND section_key=?",
                    (p["project_id"], k["target"]))
    body = ((cur["body"] if cur else "") or "").rstrip()
    head = f"── AI案（{p['model']}・{p['created_at']}）をもとに {store.today_s()} 追記 ──"
    project_m.save_section(p["project_id"], k["target"], (body + "\n\n" if body else "") + head + "\n" + text, user_id)
    edited = text != as_text(json.loads(p["body"]).get("sections", []))
    note = f"{k['target_label']}に足した（" + ("直して" if edited else "そのまま") + "）"
    with store.tx() as c:
        c.execute("UPDATE project_ai_proposal SET state='採用', decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (user_id, store.now_s(), note, proposal_id))
    return {"ok": True, "edited": edited, "target": k["target"], "project_id": p["project_id"]}


def reject(proposal_id: int, user_id: str, note: str = "") -> dict:
    p = store.one("SELECT * FROM project_ai_proposal WHERE id=?", (proposal_id,))
    if p is None or p["kind"] not in KINDS:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    with store.tx() as c:
        c.execute("UPDATE project_ai_proposal SET state='見送り', decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (user_id, store.now_s(), (note or "").strip() or None, proposal_id))
    return {"ok": True, "project_id": p["project_id"]}
