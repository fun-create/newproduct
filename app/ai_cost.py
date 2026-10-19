#!/usr/bin/env python3
"""
原価・調達の AI 案（FR-202・ADR-117・2026-10-20 十文字さんの選択）。

本番の目視チェックで「CIPと連携してAIで案として入力して欲しい」。CIP（seisan）から読めるのは
原材料マスタ（名前・分類・単位・単価・仕入先・リードタイム・ロット）と外注先だけで、
**レシピ（商品ごとの使用量）と受注後の確定原価（FR-93）はまだ読めない。**そこで AI にさせるのは:

- カルテの「資材の検討・決定」「生産方法」「サンプル」などの文から、**CIP の原材料マスタの一覧の中で**合うものを選ぶこと
- 選んだものの**単価・仕入先・リードタイム・ロットは CIP の値**をそのまま使う（AI の答えに数字があっても使わない）
- **使用量は作らせない。**人が相見積の候補に足し、試算原価の行で使用量を入れる（N-10・FR-91）
- 一覧に無いコードを返したものは捨てる（名前の似た別物を作らない）

- **AI は案を出すだけ**（FR-145）。人が「候補に足す」を押したときだけ相見積の候補（sourcing_candidate）になる
- 渡すのはカルテの文面と CIP の原材料の一覧（名前・分類・単位）だけ。お客さまの情報は無い。CIP の商品名は読まない
- 予算は `newproduct-text` に記録し、記録できなければ保存しない
"""
from __future__ import annotations

import json
import re
import threading

from . import ai_budget
from . import ai_cli
from . import ai_score
from . import store

KIND = "cost_cip"
MAX_ITEMS = 8
PARTS = ("本体", "付属品", "専用資材", "その他")
SECTIONS = (("D.material", "資材の検討・決定"), ("E.method", "生産方法・生産の流れ"), ("E.sample", "サンプル検討・試作"),
            ("C.concept", "コンセプト"), ("D.cost", "原価算出のメモ"))

SYSTEM = """あなたは FUN-CREATE株式会社（愛知県西尾市）の新商品開発の補助です。
同社は推し活・ライフイベント向けのカスタムグッズを自社で製造しています。

役割: 新商品のカルテの文から、その商品を作るのに使いそうな原材料を、渡された「原材料の一覧（生産管理システム CIP のマスタ）」の中から選びます。決めるのは人です。

守ること:
- 選べるのは一覧にある code だけ。一覧に無いものを作らない。合うものが無ければ選ばず、unverified に「一覧に○○に当たるものが無い」と書く
- 使用量・単価・原価などの数字を書かない（単価は CIP の値を人が見る。使用量は人が決める）
- 選んだ理由は1文・60字以内
- 8つまで
- 答えは JSON のオブジェクトだけ。前後に文章やコードブロックを付けない"""


def karte(project_id: str) -> dict:
    from . import project as project_m
    p = store.one("SELECT * FROM project WHERE id=?", (project_id,))
    if p is None:
        raise LookupError("案件がありません")
    p = dict(p)
    d = {"分類": project_m.product_label(p)}
    if (p.get("internal_name") or "").strip():
        d["社内呼称"] = p["internal_name"].strip()
    fl = store.one("SELECT label FROM flow_type WHERE code=?", (p.get("flow_type"),)) if p.get("flow_type") else None
    if fl:
        d["開発タイプ"] = fl["label"]
    secs = {r["section_key"]: (r["body"] or "").strip() for r in store.q(
        "SELECT section_key, body FROM project_section WHERE project_id=?", (project_id,))}
    for key, lab in SECTIONS:
        if secs.get(key):
            d[lab] = secs[key][:1200]
    return d


def has_material_text(d: dict) -> bool:
    return any(lab in d for _k, lab in SECTIONS[:3])


def build_prompt(k: dict, materials: list[dict]) -> str:
    lines = "\n".join(f"{m['code']}\t{m.get('category') or ''}\t{m.get('name') or ''}\t{m.get('unit') or ''}" for m in materials)
    return ("次の新商品のカルテを読み、使いそうな原材料を一覧から選んでください。\n\n"
            "答えの形:\n"
            '{"items":[{"code":"一覧の code","part":"本体|付属品|専用資材|その他","why":"選んだ理由"}],"unverified":["…"]}\n\n'
            "カルテ:\n" + json.dumps(k, ensure_ascii=False, indent=1) + "\n\n"
            "原材料の一覧（code・分類・名前・単位。タブ区切り）:\n" + lines)


def parse_answer(text: str) -> dict:
    m = re.search(r"\{.*\}", (text or "").strip(), re.S)
    if not m:
        raise ai_score.EnvFailure("返事に JSON がありませんでした")
    try:
        j = json.loads(m.group(0))
    except ValueError:
        raise ai_score.EnvFailure("返事の JSON が読めませんでした") from None
    return {"items": j.get("items") if isinstance(j.get("items"), list) else [],
            "unverified": [str(x).strip()[:120] for x in (j.get("unverified") or []) if str(x).strip()][:8]}


def pick(ans: dict, materials: list[dict]) -> tuple[list[dict], list[str]]:
    """答えを CIP の一覧と突き合わせる。**一覧に無い code は捨てる**。数字は CIP の値だけを持つ。"""
    by = {str(m["code"]): m for m in materials}
    out, dropped, seen = [], [], set()
    for x in ans.get("items") or []:
        if not isinstance(x, dict):
            continue
        code = str(x.get("code") or "").strip()
        if code not in by:
            if code:
                dropped.append(code)
            continue
        if code in seen:
            continue
        seen.add(code)
        m = by[code]
        part = str(x.get("part") or "").strip()
        out.append({"code": code, "name": m.get("name"), "category": m.get("category"), "unit": m.get("unit"),
                    "unit_price": m.get("unit_price"), "supplier": m.get("supplier"), "lead_days": m.get("lead_days"),
                    "lot_size": m.get("lot_size"), "part": part if part in PARTS else "その他",
                    "why": str(x.get("why") or "").strip()[:120] or None, "state": "提案", "candidate_id": None})
        if len(out) >= MAX_ITEMS:
            break
    return out, dropped


def start_run(project_id: str, user_id: str, *, runner=None, materials_fn=None, sync: bool = False,
              force: bool = False) -> dict:
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
    if not has_material_text(karte(project_id)):
        return {"started": False, "reason": "カルテの「資材の検討・決定」「生産方法・生産の流れ」「サンプル検討・試作」がどれも空です。どれかを書いてから出させてください（文が無いと選べません）"}
    with store.tx() as c:
        rid = c.execute("INSERT INTO ai_run (job,kind,stage,target_json,requested_by,requested_at) VALUES (?,?,?,?,?,?)",
                        (ai_score.JOB, KIND, "待ち", json.dumps([project_id]), user_id, store.now_s())).lastrowid
    if sync:
        _work(rid, project_id, runner, materials_fn, record=not force)
    else:
        threading.Thread(target=_work, args=(rid, project_id, runner, materials_fn), daemon=True, name=f"ai-cost-{rid}").start()
    return {"started": True, "run_id": rid, "n": 1}


def _materials() -> list[dict]:
    from . import seisan
    return seisan.materials()["items"]


def _work(run_id: int, project_id: str, runner=None, materials_fn=None, record: bool = True) -> None:
    store.ex("UPDATE ai_run SET stage='実行中' WHERE id=?", (run_id,))
    store.conn().commit()
    model, cost = None, 0.0
    try:
        from . import seisan
        try:
            mats = (materials_fn or _materials)()
        except (seisan.NotConfigured, seisan.Refused) as e:
            raise ai_score.EnvFailure(f"CIP の原材料の一覧を読めませんでした: {e}") from None
        mats = [m for m in mats if m.get("code") and m.get("name")]
        if not mats:
            raise ai_score.EnvFailure("CIP の原材料の一覧が空でした")
        as_of = store.now_s()
        k = karte(project_id)
        r = ai_cli.ask(build_prompt(k, mats), SYSTEM, runner=runner)
        model, cost = r.get("model"), float(r.get("cost_usd") or 0.0)
        if not r["ok"]:
            raise ai_score.EnvFailure(r["error"])
        ans = parse_answer(r["text"])
        items, dropped = pick(ans, mats)
        if record:
            try:
                ai_budget.record(ai_score.JOB, model, cost, note=f"原価の AI 案（CIP の原材料から） 回{run_id}",
                                 request_id=f"newproduct-ai-run{run_id}")
            except (ai_budget.BudgetUnavailable, ai_budget.OverCap) as e:
                raise ai_score.EnvFailure(f"使った額を記録できませんでした: {e}") from None
        unv = list(ans["unverified"])
        if dropped:
            unv.append("一覧に無いコードを返したので捨てた: " + "、".join(dropped[:5]))
        unv.append("使用量は人が決める（AI は出していない）")
        if not items:
            raise ai_score.EnvFailure("一覧の中から選べたものがありませんでした" + (f"（{unv[0]}）" if ans["unverified"] else ""))
        now = store.now_s()
        with store.tx() as c:
            c.execute("UPDATE cost_ai_proposal SET state='見送り', decided_by='（自動）', decided_at=?, "
                      "decided_note='新しい案に置き換えた' WHERE project_id=? AND state='提案'", (now, project_id))
            c.execute("INSERT INTO cost_ai_proposal (project_id,run_id,items,unverified,inputs,model,as_of,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (project_id, run_id, json.dumps(items, ensure_ascii=False), json.dumps(unv[:10], ensure_ascii=False),
                       json.dumps({"出所": "NEW PRODUCT の案件カルテと CIP の原材料マスタ", "カルテの項目": list(k.keys()),
                                   "原材料の件数": len(mats)}, ensure_ascii=False), model, as_of, now))
        ai_score._finish(run_id, "完了", len(items), 0, model, cost)
    except ai_score.EnvFailure as e:
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "env", str(e))
    except Exception as e:                                   # 想定外も「実行中」のまま残さない
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "bug", f"{type(e).__name__}: {e}")


def _prop(r) -> dict:
    d = dict(r)
    d["items"] = json.loads(d["items"]) if d.get("items") else []
    d["unverified"] = json.loads(d["unverified"]) if d.get("unverified") else []
    d["inputs"] = json.loads(d["inputs"]) if d.get("inputs") else {}
    return d


def latest(project_id: str) -> dict | None:
    r = store.one("SELECT * FROM cost_ai_proposal WHERE project_id=? AND state='提案' ORDER BY id DESC LIMIT 1", (project_id,))
    return _prop(r) if r else None


def _close_if_done(c, pid_: int, items: list[dict], user_id: str) -> None:
    if all(x["state"] != "提案" for x in items):
        c.execute("UPDATE cost_ai_proposal SET state='済み', decided_by=?, decided_at=? WHERE id=?", (user_id, store.now_s(), pid_))


def add_item(proposal_id: int, code: str, user_id: str) -> dict:
    """案の1つを相見積の候補に足す（**数字は CIP の値**。使用量は入れない）。"""
    from . import cost
    p = store.one("SELECT * FROM cost_ai_proposal WHERE id=?", (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    items = json.loads(p["items"])
    it = next((x for x in items if x["code"] == str(code)), None)
    if it is None:
        raise LookupError("その原材料は案にありません")
    if it["state"] != "提案":
        raise ValueError(f"この原材料はもう「{it['state']}」です")
    r = cost.save_candidate(p["project_id"], {
        "kind": "資材", "part": it["part"], "supplier": it.get("supplier") or "（CIP に仕入先の登録なし）",
        "shape_size": f"{it.get('name')}（{it.get('unit') or '単位なし'}）", "unit_price": it.get("unit_price"),
        "min_lot": it.get("lot_size") or None, "lead_days": it.get("lead_days"),
        "note": f"CIP の原材料 {it['code']}（{p['as_of']} 時点のマスタの値）。AI の案（{p['model']}）から足した。使用量は試算原価の行で入れる"
                + (f"。選んだ理由（AI）: {it['why']}" if it.get("why") else "")}, user_id)
    it["state"], it["candidate_id"] = "足した", r["id"]
    with store.tx() as c:
        c.execute("UPDATE cost_ai_proposal SET items=? WHERE id=?", (json.dumps(items, ensure_ascii=False), proposal_id))
        _close_if_done(c, proposal_id, items, user_id)
    return {"ok": True, "candidate_id": r["id"]}


def skip_item(proposal_id: int, code: str, user_id: str) -> dict:
    if not ai_score.can_run(user_id):
        raise PermissionError("見送れるのは、商品開発部・管理者・社長の業務ロールの人です")
    p = store.one("SELECT * FROM cost_ai_proposal WHERE id=?", (proposal_id,))
    if p is None or p["state"] != "提案":
        raise ValueError("その案はもう決まっています")
    items = json.loads(p["items"])
    it = next((x for x in items if x["code"] == str(code)), None)
    if it is None or it["state"] != "提案":
        raise ValueError("その原材料はもう決まっています")
    it["state"] = "見送り"
    with store.tx() as c:
        c.execute("UPDATE cost_ai_proposal SET items=? WHERE id=?", (json.dumps(items, ensure_ascii=False), proposal_id))
        _close_if_done(c, proposal_id, items, user_id)
    return {"ok": True}
