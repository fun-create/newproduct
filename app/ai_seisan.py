#!/usr/bin/env python3
"""
CIP 登録票の AI 入力アシスト（FR-228・ADR-125・2026-10-23 十文字さん選択）。

十文字さんの言葉: 「CIP側の商品構成を学習し、AIに商品マスタや原材料マスタの入力アシストまたは、候補を提案するような仕組みにしてほしい」。
「学習」はモデルを訓練することではなく、**毎回、CIP のいまの構成を AI に渡して選ばせる**こと（CIP が変われば案も変わる）。

- 渡すのは、カルテの文（社内呼称・開発タイプ・コンセプト・資材・作り方など）と、CIP の選択肢（販売タイプ・大中小分類）と、
  CIP の商品の構成（共通商品コード・販売タイプ・分類・形状・サイズ・色・レシピの有無）。**CIP の商品名は読まない**
  （名入れのお客さまの入力値が混ざる列のため・FR-24。口も名前を返さない）。お客さまの情報は無い
- AI が出すのは登録票の候補（販売タイプ・大中小分類・形状・サイズ・色・レシピ複製元）と理由だけ。**共通商品コードと商品名は出させない**
  （コードは採番の案・名前は人）
- **CIP に無い値は案にしない**（分類の組み合わせは CIP にあるものだけ・複製元は CIP にあってレシピのある商品だけ）。外れたものは未確認に回す
- AI は案を出すだけ（FR-145）。「登録票に入れる」で下書きの欄に入り、保存も登録も人
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

KIND = "seisan_master"
SECTIONS = (("C.concept", "コンセプト"), ("C.target", "ターゲット・使用シーン"), ("D.material", "資材の検討・決定"),
            ("E.method", "生産方法・生産の流れ"), ("E.sample", "サンプル検討・試作"), ("F.name", "商品名の検討"))
FIELDS = ("sales_type", "cat1", "cat2", "cat3", "shape", "size", "color", "copy_recipe_from")

SYSTEM = """あなたは FUN-CREATE株式会社（愛知県西尾市）の新商品開発の補助です。
同社は推し活・ライフイベント向けのカスタムグッズを自社で製造し、生産管理システム CIP に商品マスタを登録しています。

役割: 新商品のカルテを読み、CIP の商品マスタの登録票の候補を出します。決めて登録するのは人です。

守ること:
- 販売タイプと大分類・中分類・小分類は、渡された「CIP の選択肢」の値だけを使う。分類の組み合わせは「CIP の商品の構成」に実際にあるものを選ぶ
- レシピ複製元は「CIP の商品の構成」にあるコードで、レシピが「あり」のものから、作り方・形・大きさがいちばん近いものを1つ選ぶ。合うものが無ければ空にして unverified に理由を書く
- 形状・サイズ・色は、CIP の既存の書き方（構成の一覧にある表記）に合わせ、短く（20字以内）。分からなければ空にする
- 共通商品コード・商品名・価格・数量などは書かない
- 各欄の理由は1文・60字以内
- 答えは JSON のオブジェクトだけ。前後に文章やコードブロックを付けない"""


def karte(project_id: str) -> dict:
    from . import project as project_m
    p = store.one("SELECT * FROM project WHERE id=?", (project_id,))
    if p is None:
        raise LookupError("案件がありません")
    p = dict(p)
    d = {"分類（案件）": project_m.product_label(p)}
    if (p.get("internal_name") or "").strip():
        d["社内呼称"] = p["internal_name"].strip()
    fl = store.one("SELECT label FROM flow_type WHERE code=?", (p.get("flow_type"),)) if p.get("flow_type") else None
    if fl:
        d["開発タイプ"] = fl["label"]
    if p.get("size"):
        d["サイズ（案件）"] = p["size"]
    vs = [v["label"] for v in store.q("SELECT label FROM project_variant WHERE project_id=? ORDER BY label", (project_id,))]
    if vs:
        d["バリエーション"] = vs[:20]
    secs = {r["section_key"]: (r["body"] or "").strip() for r in store.q(
        "SELECT section_key, body FROM project_section WHERE project_id=?", (project_id,))}
    for key, lab in SECTIONS:
        if secs.get(key):
            d[lab] = secs[key][:1000]
    return d


def has_text(d: dict) -> bool:
    return any(lab in d for _k, lab in SECTIONS)


def build_prompt(k: dict, voc: dict, products: list[dict]) -> str:
    lines = "\n".join("\t".join(str(x.get(f) or "") for f in ("code", "sales_type", "cat1", "cat2", "cat3", "shape", "size", "color"))
                      + "\t" + ("あり" if x.get("has_recipe") else "なし") for x in products)
    return ("次の新商品のカルテを読み、CIP の商品マスタの登録票の候補を出してください。\n\n"
            "答えの形:\n"
            '{"fields":{"sales_type":"","cat1":"","cat2":"","cat3":"","shape":"","size":"","color":"","copy_recipe_from":""},'
            '"reasons":{"欄の名前":"理由"},"unverified":["…"]}\n\n'
            "カルテ:\n" + json.dumps(k, ensure_ascii=False, indent=1) + "\n\n"
            "CIP の選択肢:\n" + json.dumps({"販売タイプ": voc.get("sales_types"), "大分類": voc.get("cat1"),
                                          "中分類": voc.get("cat2"), "小分類": voc.get("cat3")}, ensure_ascii=False) + "\n\n"
            "CIP の商品の構成（コード・販売タイプ・大分類・中分類・小分類・形状・サイズ・色・レシピ。タブ区切り。商品名は無い）:\n" + lines)


def parse_answer(text: str) -> dict:
    m = re.search(r"\{.*\}", (text or "").strip(), re.S)
    if not m:
        raise ai_score.EnvFailure("返事に JSON がありませんでした")
    try:
        j = json.loads(m.group(0))
    except ValueError:
        raise ai_score.EnvFailure("返事の JSON が読めませんでした") from None
    f = j.get("fields") if isinstance(j.get("fields"), dict) else {}
    r = j.get("reasons") if isinstance(j.get("reasons"), dict) else {}
    return {"fields": {k: str(f.get(k) or "").strip() for k in FIELDS},
            "reasons": {str(k): str(v).strip()[:120] for k, v in r.items() if str(v).strip()},
            "unverified": [str(x).strip()[:120] for x in (j.get("unverified") or []) if str(x).strip()][:8]}


def check(ans: dict, voc: dict, products: list[dict]) -> tuple[dict, list[str]]:
    """CIP に無い値を落とす。落としたものは未確認に。"""
    f, unv = dict(ans["fields"]), []
    if f["sales_type"] and f["sales_type"] not in (voc.get("sales_types") or []):
        unv.append(f"販売タイプ「{f['sales_type']}」は CIP の選択肢に無いので外した"); f["sales_type"] = ""
    combos = {(p.get("cat1") or "", p.get("cat2") or "", p.get("cat3") or "") for p in products}
    if f["cat1"] and f["cat1"] not in (voc.get("cat1") or []):
        unv.append(f"大分類「{f['cat1']}」は CIP に無いので外した"); f["cat1"] = f["cat2"] = f["cat3"] = ""
    if f["cat1"] and f["cat2"] and not any(c[0] == f["cat1"] and c[1] == f["cat2"] for c in combos):
        unv.append(f"「{f['cat1']} / {f['cat2']}」の組み合わせは CIP の商品に無いので、中分類・小分類を外した"); f["cat2"] = f["cat3"] = ""
    if f["cat3"] and (f["cat1"], f["cat2"], f["cat3"]) not in combos:
        unv.append(f"小分類「{f['cat3']}」は、その大分類・中分類の商品に無いので外した"); f["cat3"] = ""
    by = {str(p.get("code")): p for p in products}
    src = f["copy_recipe_from"]
    if src and (src not in by or not by[src].get("has_recipe")):
        unv.append(f"レシピ複製元「{src}」は CIP にレシピのある商品として無いので外した"); f["copy_recipe_from"] = ""
    for k in ("shape", "size", "color"):
        f[k] = f[k][:30]
    return f, unv


def _products() -> tuple[dict, list[dict]]:
    """CIP の選択肢と、商品の構成（分類ごとに読む。**名前は来ない**）。"""
    from . import seisan
    voc = seisan.vocab()
    if voc.get("source") != "seisan":
        raise seisan.NotConfigured(voc.get("why") or "CIP の選択肢を読めません")
    out, seen = [], set()
    for c1 in voc.get("cat1") or []:
        for it in seisan.similar(c1):
            if it.get("code") and it["code"] not in seen:
                seen.add(it["code"])
                out.append(it)
    return voc, out


def start_run(project_id: str, user_id: str, *, runner=None, products_fn=None, sync: bool = False, force: bool = False) -> dict:
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
    if not has_text(karte(project_id)):
        return {"started": False, "reason": "カルテのコンセプト・資材・作り方などの文がどれも空です。どれかを書いてから出させてください（文が無いと選べません）"}
    with store.tx() as c:
        rid = c.execute("INSERT INTO ai_run (job,kind,stage,target_json,requested_by,requested_at) VALUES (?,?,?,?,?,?)",
                        (ai_score.JOB, KIND, "待ち", json.dumps([project_id]), user_id, store.now_s())).lastrowid
    if sync:
        _work(rid, project_id, runner, products_fn, record=not force)
    else:
        threading.Thread(target=_work, args=(rid, project_id, runner, products_fn), daemon=True, name=f"ai-seisan-{rid}").start()
    return {"started": True, "run_id": rid, "n": 1}


def _work(run_id: int, project_id: str, runner=None, products_fn=None, record: bool = True) -> None:
    store.ex("UPDATE ai_run SET stage='実行中' WHERE id=?", (run_id,))
    store.conn().commit()
    model, cost = None, 0.0
    try:
        from . import seisan
        try:
            voc, prods = (products_fn or _products)()
        except (seisan.NotConfigured, seisan.Refused) as e:
            raise ai_score.EnvFailure(f"CIP の商品の構成を読めませんでした: {e}") from None
        if not prods:
            raise ai_score.EnvFailure("CIP の商品の構成が空でした")
        as_of = store.now_s()
        k = karte(project_id)
        r = ai_cli.ask(build_prompt(k, voc, prods), SYSTEM, runner=runner)
        model, cost = r.get("model"), float(r.get("cost_usd") or 0.0)
        if not r["ok"]:
            raise ai_score.EnvFailure(r["error"])
        ans = parse_answer(r["text"])
        fields, dropped = check(ans, voc, prods)
        if record:
            try:
                ai_budget.record(ai_score.JOB, model, cost, note=f"CIP 登録票の AI 案 回{run_id}",
                                 request_id=f"newproduct-ai-run{run_id}")
            except (ai_budget.BudgetUnavailable, ai_budget.OverCap) as e:
                raise ai_score.EnvFailure(f"使った額を記録できませんでした: {e}") from None
        if not any(fields.values()):
            raise ai_score.EnvFailure("CIP にある値で出せた候補がありませんでした" + (f"（{dropped[0]}）" if dropped else ""))
        unv = list(ans["unverified"]) + dropped + ["共通商品コードと商品名は AI に出させていない（コードは採番の案・名前は人）"]
        now = store.now_s()
        with store.tx() as c:
            c.execute("UPDATE seisan_ai_proposal SET state='見送り', decided_by='（自動）', decided_at=? "
                      "WHERE project_id=? AND state='提案'", (now, project_id))
            c.execute("INSERT INTO seisan_ai_proposal (project_id,run_id,fields,reasons,unverified,inputs,model,as_of,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (project_id, run_id, json.dumps(fields, ensure_ascii=False), json.dumps(ans["reasons"], ensure_ascii=False),
                       json.dumps(unv[:10], ensure_ascii=False),
                       json.dumps({"出所": "NEW PRODUCT の案件カルテと CIP の選択肢・商品の構成（商品名なし）",
                                   "カルテの項目": list(k.keys()), "CIP の商品の件数": len(prods)}, ensure_ascii=False),
                       model, as_of, now))
        ai_score._finish(run_id, "完了", 1, 0, model, cost)
    except ai_score.EnvFailure as e:
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "env", str(e))
    except Exception as e:                                   # 想定外も「実行中」のまま残さない
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "bug", f"{type(e).__name__}: {e}")


def latest(project_id: str) -> dict | None:
    r = store.one("SELECT * FROM seisan_ai_proposal WHERE project_id=? AND state='提案' ORDER BY id DESC LIMIT 1", (project_id,))
    if r is None:
        return None
    d = dict(r)
    for k in ("fields", "reasons", "unverified", "inputs"):
        d[k] = json.loads(d[k]) if d.get(k) else ({} if k != "unverified" else [])
    return d


def decide(proposal_id: int, state: str, user_id: str) -> dict:
    """「登録票に入れた」（使った）か「見送る」を記録する。下書きに入れるのは画面（保存は人）。"""
    if state not in ("使った", "見送り"):
        raise ValueError("使った／見送り のどちらか")
    if not ai_score.can_run(user_id):
        raise PermissionError("決められるのは、商品開発部・管理者・社長の業務ロールの人です")
    p = store.one("SELECT state FROM seisan_ai_proposal WHERE id=?", (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        return {"ok": True, "changed": False}
    with store.tx() as c:
        c.execute("UPDATE seisan_ai_proposal SET state=?, decided_by=?, decided_at=? WHERE id=?",
                  (state, user_id, store.now_s(), proposal_id))
    return {"ok": True, "changed": True}
