#!/usr/bin/env python3
"""
AI採点の案（F-1-11 ／ FR-74・FR-145・FR-147・FR-148 ／ ADR-081・2026-10-09 十文字さんの選択）。

これまでのやり方（要件定義 2-3 の1件目）:

    未採点行をドラッグでコピー → claude.ai を開く → 指示書を貼る →
    行を貼る → 配列数式が返る → G列に貼る → 範囲を選んで値のみ貼り直す

この7ステップを、画面のボタン1つにする。ただし **AI は案を出すだけ**（FR-145）:

- AI が付けるのは v2 の4軸（購買意欲・ターゲット規模・競合優位性・テーマ適合）の 1〜10 と、**軸ごとの根拠**
- **想定粗利額（円）と生産方法(1-5)は付けさせない。**人が入れる項目で、AI に推測させると
  「分からない」が数字に化ける（N-10）
- 案は `idea_ai_proposal` に置く。**点（idea_score）には入れない。**人が「採用」を押したときに
  v2 の点になる（そのまま採用＝scored_by ai・モデル名つき／直して採用＝scored_by human）
- 根拠の無い軸は案にしない。確かめられないことは「未確認」として並べさせる（FR-148）
- 案には、モデル名・実行日時・渡した項目とその出所を残す（FR-147）

**このファイル自体は外部と通信しない。**`urllib` も `http` も `socket` も import しない
（tests が検査する）。呼ぶのは `ClaudeScorer` → `app/ai_cli.py`（`claude` を子プロセスで起動）。
その前に**予算を確かめる**のと、後で**使った額を記録する**のがここの役目。
記録できなければ、その回の案は保存しない（記録の無い使用を作らない）。
"""
from __future__ import annotations

import json
import re
import threading

from . import ai_budget
from . import ai_cli
from . import idea as idea_m
from . import store

MODEL_UNKNOWN = "未設定"
JOB = "newproduct-text"          # 採点は文章側。**`newproduct-` で始めること**
AXES = ("demand", "market_size", "advantage", "theme_fit")
CHUNK = 10                        # 1回の claude に渡す件数
MAX_PER_RUN = 30                  # 1回に走らせる上限（押し間違いで枠を使い切らない）
RUNNERS = ("devdept", "admin", "president")      # 走らせてよい業務ロール（FCTR の自社側と同じ）


class EnvFailure(RuntimeError):
    """環境側の失敗（ログイン切れ・時間切れ・予算）。**その回を打ち切る。**入力の不備とは分ける。"""


class Scorer:
    """採点の案を出すものの形。**これに合わせれば差し替えられる。**

    `score(idea)` は {"axes": {軸: 1〜10}, "reasons": {軸: 根拠}, "unverified": [...]} を返す。
    `score_many(ideas)` は ({idea_id: 上の形 or {"error": …}}, {"model", "cost_usd"}) を返す。
    """

    model = MODEL_UNKNOWN

    def score(self, idea: dict) -> dict:      # pragma: no cover - 形の宣言
        raise NotImplementedError

    def score_many(self, ideas: list[dict]) -> tuple[dict, dict]:
        out = {}
        for i in ideas:
            try:
                out[i["id"]] = self.score(i)
            except (ValueError, RuntimeError) as e:
                out[i["id"]] = {"error": str(e)}
        return out, {"model": self.model, "cost_usd": 0.0}


class Disabled(Scorer):
    """呼ぶと理由を言って止まる。**黙って 0 を返さない。**"""

    model = "（AI採点は無効）"

    def score(self, idea: dict) -> dict:
        raise RuntimeError(reason_off())


# ══════════════════════════════════════════════════════════
# 使えるか
# ══════════════════════════════════════════════════════════
def setting_on() -> bool:
    return str(idea_m.setting("ai_scoring_enabled", "0")).strip() in ("1", "true", "on")


def can_run(user_id: str) -> bool:
    from . import gate
    return bool(set(gate.roles_of(user_id)) & set(RUNNERS))


def reason_off(budget: dict | None = None, pre: dict | None = None) -> str:
    if not setting_on():
        return ("AI採点は設定で off です（ai_scoring_enabled）。"
                "予算枠は 2026-09-24 に付きました（newproduct-* 5.0）。"
                "使うなら設定で on にしてください。")
    pre = pre or ai_cli.preflight()
    if not pre["ok"]:
        return "AI を呼べません: " + pre["ng"][0]["why"]
    b = budget or ai_budget.check(JOB)
    return f"予算を確かめられないか、残りがありません: {b.get('why') or b.get('state')}"


def enabled(budget: dict | None = None, pre: dict | None = None) -> bool:
    """**設定が on・claude が呼べる・枠を確かめられて残りがある**の3つがそろったときだけ真。"""
    if not setting_on():
        return False
    if not (pre or ai_cli.preflight())["ok"]:
        return False
    b = budget or ai_budget.check(JOB)
    return bool(b.get("usable"))


def candidates() -> list[str]:
    """既定の対象: **一度も採点されていない（起票のまま）**で、出しかけの案が無いもの。

    移行した 874 件は v1 の点を持つ（再採点しない・F-1-10）。全部を AI に回すと枠を使い切るので、
    既定には入れない。個別のアイデアからは1件ずつ出せる。
    """
    return [r[0] for r in store.q(
        "SELECT i.id FROM idea i WHERE i.stage='起票' AND i.theme_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM idea_score s WHERE s.idea_id=i.id) "
        "AND NOT EXISTS (SELECT 1 FROM idea_ai_proposal p WHERE p.idea_id=i.id AND p.state='提案') "
        "ORDER BY i.created_at, i.id")]


def status(user_id: str | None = None) -> dict:
    """画面に出す状態。**「使えない」を「使っていない」と混ぜない。**"""
    pre = ai_cli.preflight()
    b = ai_budget.check(JOB)
    on = enabled(b, pre)
    real = ai_cli.cost_is_real()
    return {
        "enabled": on,
        "reason": None if on else reason_off(b, pre),
        "budget": b,
        "setting_on": setting_on(),
        "preflight": pre,
        "scorer_implemented": True,
        "can_run": bool(user_id) and can_run(user_id),
        "runners_label": "商品開発部・管理者・社長の業務ロールの人",
        "cost_label": "使った額" if real else "使った額の目安",
        "cost_note": "" if real else "定額プランのログインなので、この額は請求されません（API で同じことをしたらいくらかの目安です）。予算の枠にはこの目安で数えます",
        "rubric_version": idea_m.V2_VERSION,
        "records": {
            "kept": ["rubric版", "実行日時", "モデル名", "渡した項目"],
            "why": "どの基準で・いつ・何が付けた点なのかが残らないと、採点が当たったかどうかを後から検証できない",
        },
        "ai_scored": store.val("SELECT COUNT(*) FROM idea_score WHERE scored_by='ai'", (), 0),
        "pending": store.val("SELECT COUNT(*) FROM idea_ai_proposal WHERE state='提案'", (), 0),
        "candidates": len(candidates()),
        "max_per_run": MAX_PER_RUN,
        "running": store.val("SELECT COUNT(*) FROM ai_run WHERE stage IN ('待ち','実行中')", (), 0),
    }


# ══════════════════════════════════════════════════════════
# claude に聞く
# ══════════════════════════════════════════════════════════
SYSTEM = """あなたは FUN-CREATE株式会社（愛知県西尾市・2006年創業）の新商品開発の採点補助です。
同社はアクリルスタンド・うちわ・オリジナルグッズなど、推し活やライフイベント向けのカスタムグッズを
自社EC（グッズ本店・うちわ本店ほか）と楽天・Amazon・Yahoo!ショッピング・ギフトモールで販売し、自社で製造しています。

役割: 渡された新商品アイデアに、決められた4つの軸で 1〜10 の点の「案」を付けます。決めるのは人です。

守ること:
- 根拠は、渡された文面と一般に知られていることだけから書く。売上・検索数・市場規模などの数字を作らない
- ウェブも社内データも見られない。確かめないと言えないことは unverified に短く並べる（例:「競合の価格帯は未確認」）
- 根拠が書けない軸は点を付けず null にする
- 軸ごとの根拠は1〜2文・80字以内
- 答えは JSON の配列だけ。前後に文章やコードブロックを付けない"""


def _theme_fit_label(theme_id: str) -> str:
    return {"lifeevent": "ライフイベント（記念日・行事・贈り物）との相性",
            "oshikatsu": "推し活（うちわ・応援・推しの布教）との相性",
            "lovot": "LOVOT オーナー向けとしての相性（うちの子愛・オフ会映え・規定への適合）",
            "bukkomi": "テーマ未定のため、同社の既存事業（推し活・ライフイベント）との相性"}.get(theme_id, "テーマとの相性")


INPUT_FIELDS = (("title", "商品案名"), ("summary", "概要"), ("target_scene", "想定ターゲット・シーン"),
                ("demand_cycle", "需要発生（通年／季節／単発）"), ("origin", "起票経路"), ("note", "メモ"))


def _inputs(idea: dict, theme_label: str) -> dict:
    """渡す項目。**URL・人の名前・数字の推測材料は渡さない。**空欄は渡さない（「空」と書いて渡すと推測で埋める）。"""
    d = {lab: str(idea.get(k)).strip() for k, lab in INPUT_FIELDS
         if idea.get(k) is not None and str(idea.get(k)).strip()}
    d["テーマ"] = theme_label
    return d


def build_prompt(ideas: list[dict]) -> str:
    themes = {r["id"]: r["label"] for r in store.q("SELECT id,label FROM theme")}
    items = []
    for i in ideas:
        items.append({"id": i["id"], "theme_fit の意味": _theme_fit_label(i.get("theme_id")),
                      **_inputs(i, themes.get(i.get("theme_id"), i.get("theme_id") or ""))})
    return (
        "次のアイデアそれぞれに、4つの軸で 1〜10 の点の案を付けてください。\n\n"
        "軸:\n"
        "- demand（購買意欲・ニーズ）: 欲しいと思う人の気持ちの強さ。1=ほぼ無い 5=あれば買う人がいる 10=探してでも買う\n"
        "- market_size（ターゲット規模）: 買い手になりうる人の広さ。1=ごく一部 5=特定の層で広い 10=誰でも\n"
        "- advantage（競合優位性）: 同社が作る理由（自社製造・カスタム・小ロット）が効くか。1=どこでも同じ物 10=同社でしか出せない\n"
        "- theme_fit（テーマ適合）: 各アイデアの「theme_fit の意味」に書いたものとの相性\n\n"
        "答えの形（配列・アイデアの数だけ）:\n"
        '[{"id":"…","demand":7,"market_size":5,"advantage":6,"theme_fit":8,'
        '"reasons":{"demand":"…","market_size":"…","advantage":"…","theme_fit":"…"},'
        '"unverified":["…"]}]\n\n'
        "アイデア:\n" + json.dumps(items, ensure_ascii=False, indent=1))


def parse_answer(text: str, ids: list[str]) -> dict:
    """返事を読む。**形が崩れていたら、その件は案にしない**（推測で直さない）。"""
    t = text.strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        raise EnvFailure("返事に JSON の配列がありませんでした")
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        raise EnvFailure("返事の JSON が読めませんでした") from None
    out = {}
    for x in arr if isinstance(arr, list) else []:
        if not isinstance(x, dict) or x.get("id") not in ids:
            continue
        out[x["id"]] = {"axes": {a: x.get(a) for a in AXES},
                        "reasons": x.get("reasons") or {},
                        "unverified": x.get("unverified") or []}
    for i in ids:
        out.setdefault(i, {"error": "返事にこのアイデアがありませんでした"})
    return out


class ClaudeScorer(Scorer):
    """`claude` に CHUNK 件ずつ聞く。**ツールは渡さない**（ai_cli）。"""

    def __init__(self, runner=None):
        self.runner = runner
        self.model = ai_cli.DEFAULT_MODEL

    def score_many(self, ideas):
        r = ai_cli.ask(build_prompt(ideas), SYSTEM, runner=self.runner)
        if not r["ok"]:
            raise EnvFailure(r["error"])
        self.model = r["model"]
        return parse_answer(r["text"], [i["id"] for i in ideas]), {"model": r["model"], "cost_usd": r["cost_usd"]}


def _check(res: dict) -> tuple[dict, dict, list] | str:
    """1件分を確かめる。**根拠の無い軸は案にしない**（FR-148）。"""
    if "error" in res:
        return res["error"]
    axes, reasons, unv = {}, {}, []
    for a in AXES:
        v = (res.get("axes") or {}).get(a)
        if v is None:
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            return f"{a} が数ではありません"
        if not 1 <= n <= 10:
            return f"{a} が 1〜10 の外です（{n}）"
        why = str((res.get("reasons") or {}).get(a) or "").strip()
        if not why:
            continue                                   # 根拠の無い点は捨てる
        axes[a], reasons[a] = n, why[:200]
    if not axes:
        return "根拠のある軸が1つもありませんでした"
    for x in res.get("unverified") or []:
        s = str(x).strip()
        if s:
            unv.append(s[:120])
    for a in AXES:
        if a not in axes:
            unv.append(f"{a} は根拠が書けず、点を付けていません")
    return axes, reasons, unv[:10]


# ══════════════════════════════════════════════════════════
# 走らせる
# ══════════════════════════════════════════════════════════
def start_run(idea_ids: list[str] | None, user_id: str, *, scorer: Scorer | None = None,
              sync: bool = False, force: bool = False) -> dict:
    """案を出す回を起こす。既定は**別のスレッドで走らせて**、すぐ返す（画面は回の番号で様子を見る）。

    `force` と `scorer` は tests から差し替えるためのもの。**画面からは渡さない。**
    """
    if not force:
        if not can_run(user_id):
            raise PermissionError("AI に採点の案を出させられるのは、商品開発部・管理者・社長の業務ロールの人です")
        b, pre = ai_budget.check(JOB), ai_cli.preflight()
        if not enabled(b, pre):
            return {"started": False, "reason": reason_off(b, pre)}
        if store.val("SELECT COUNT(*) FROM ai_run WHERE stage IN ('待ち','実行中')", (), 0):
            return {"started": False, "reason": "いま別の回が走っています。終わってから押してください"}
    ids = list(dict.fromkeys(idea_ids or candidates()))
    if not ids:
        return {"started": False, "reason": "対象のアイデアがありません（起票のままで、案の出ていないものが0件）"}
    if len(ids) > MAX_PER_RUN:
        return {"started": False, "reason": f"1回に出せるのは {MAX_PER_RUN} 件までです（{len(ids)} 件を選んでいます）"}
    with store.tx() as c:
        rid = c.execute("INSERT INTO ai_run (job,kind,stage,target_json,requested_by,requested_at) "
                        "VALUES (?,?,?,?,?,?)", (JOB, "idea_score", "待ち", json.dumps(ids),
                                                 user_id, store.now_s())).lastrowid
    sc = scorer or ClaudeScorer()
    if sync:
        _work(rid, sc, record=not force)
    else:
        threading.Thread(target=_work, args=(rid, sc), daemon=True, name=f"ai-run-{rid}").start()
    return {"started": True, "run_id": rid, "n": len(ids)}


def _work(run_id: int, scorer: Scorer, record: bool = True) -> None:
    store.ex("UPDATE ai_run SET stage='実行中' WHERE id=?", (run_id,))
    store.conn().commit()
    run = store.one("SELECT * FROM ai_run WHERE id=?", (run_id,))
    ids = json.loads(run["target_json"])
    themes = {r["id"]: r["label"] for r in store.q("SELECT id,label FROM theme")}
    ok = ng = 0
    cost = 0.0
    model = None
    try:
        for k in range(0, len(ids), CHUNK):
            chunk = []
            for iid in ids[k:k + CHUNK]:
                r = store.one("SELECT * FROM idea WHERE id=?", (iid,))
                if r is None or not r["theme_id"]:
                    ng += 1
                    continue
                chunk.append(dict(r))
            if not chunk:
                continue
            res, meta = scorer.score_many(chunk)
            model = meta.get("model") or scorer.model
            c_usd = float(meta.get("cost_usd") or 0.0)
            if record:
                # **記録できなければ、この回の案を保存しない**（記録の無い使用を作らない）
                try:
                    ai_budget.record(JOB, model, c_usd, note=f"AI採点の案 回{run_id}",
                                     request_id=f"newproduct-ai-run{run_id}-c{k}")
                except (ai_budget.BudgetUnavailable, ai_budget.OverCap) as e:
                    raise EnvFailure(f"使った額を記録できませんでした: {e}") from None
            cost += c_usd
            now = store.now_s()
            with store.tx() as c:
                for i in chunk:
                    got = _check(res.get(i["id"]) or {"error": "返事がありません"})
                    if isinstance(got, str):
                        ng += 1
                        continue
                    axes, reasons, unv = got
                    c.execute("UPDATE idea_ai_proposal SET state='見送り', decided_by='（自動）', decided_at=?, "
                              "decided_note='新しい案に置き換えた' WHERE idea_id=? AND state='提案'", (now, i["id"]))
                    c.execute("INSERT INTO idea_ai_proposal (idea_id,run_id,rubric_version,axes,reasons,unverified,"
                              "inputs,model,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                              (i["id"], run_id, idea_m.V2_VERSION, json.dumps(axes), json.dumps(reasons, ensure_ascii=False),
                               json.dumps(unv, ensure_ascii=False),
                               json.dumps({"出所": "NEW PRODUCT のアイデア台帳",
                                           "項目": _inputs(i, themes.get(i["theme_id"], i["theme_id"]))},
                                          ensure_ascii=False), model, now))
                    ok += 1
                c.execute("UPDATE ai_run SET n_ok=?, n_ng=?, model=?, cost_usd=? WHERE id=?",
                          (ok, ng, model, round(cost, 4), run_id))
        _finish(run_id, "完了", ok, ng, model, cost)
    except EnvFailure as e:
        _finish(run_id, "失敗", ok, ng, model, cost, "env", str(e))
    except Exception as e:                                   # 想定外も「実行中」のまま残さない
        _finish(run_id, "失敗", ok, ng, model, cost, "bug", f"{type(e).__name__}: {e}")


def _finish(run_id, stage, ok, ng, model, cost, kind=None, err=None):
    with store.tx() as c:
        c.execute("UPDATE ai_run SET stage=?, finished_at=?, n_ok=?, n_ng=?, model=?, cost_usd=?, cost_real=?, "
                  "error_kind=?, error=? WHERE id=?",
                  (stage, store.now_s(), ok, ng, model, round(cost, 4), 1 if ai_cli.cost_is_real() else 0,
                   kind, err, run_id))


def abandon_running() -> int:
    """起動時に呼ぶ。**走ったまま残った回を「中断」に倒す**（再起動でスレッドごと止まるため）。"""
    with store.tx() as c:
        return c.execute("UPDATE ai_run SET stage='中断', finished_at=?, error_kind='restart', "
                         "error='サーバが再起動したため中断しました。もう一度押せば流し直せます' "
                         "WHERE stage IN ('待ち','実行中')", (store.now_s(),)).rowcount or 0


def run_view(run_id: int) -> dict:
    r = store.one("SELECT * FROM ai_run WHERE id=?", (run_id,))
    if r is None:
        raise LookupError("その回はありません")
    d = dict(r)
    d["n"] = len(json.loads(d.pop("target_json")))
    return d


def runs(limit: int = 5) -> list[dict]:
    out = []
    for r in store.q("SELECT id FROM ai_run ORDER BY id DESC LIMIT ?", (limit,)):
        out.append(run_view(r[0]))
    return out


# ══════════════════════════════════════════════════════════
# 案を見る・採用する・見送る
# ══════════════════════════════════════════════════════════
AXIS_LABEL = {"demand": "購買意欲・ニーズ", "market_size": "ターゲット規模",
              "advantage": "競合優位性", "theme_fit": "テーマ適合"}


def _prop(r) -> dict:
    d = dict(r)
    for k in ("axes", "reasons", "unverified", "inputs"):
        d[k] = json.loads(d[k]) if d.get(k) else ({} if k != "unverified" else [])
    d["axes_view"] = [{"code": a, "label": AXIS_LABEL[a], "value": d["axes"].get(a),
                       "reason": d["reasons"].get(a)} for a in AXES]
    return d


def proposals_of(idea_id: str) -> list[dict]:
    return [_prop(r) for r in store.q("SELECT * FROM idea_ai_proposal WHERE idea_id=? ORDER BY id DESC LIMIT 5",
                                      (idea_id,))]


def pending(limit: int = 50) -> list[dict]:
    rs = store.q("SELECT p.*, i.title AS idea_title FROM idea_ai_proposal p JOIN idea i ON i.id=p.idea_id "
                 "WHERE p.state='提案' ORDER BY p.id DESC LIMIT ?", (limit,))
    return [_prop(r) for r in rs]


def adopt(proposal_id: int, user_id: str, axes: dict | None = None, note: str = "") -> dict:
    """案を点にする。**直した軸があれば人の採点**として残す（AI の点と見分けられるように）。"""
    p = store.one("SELECT * FROM idea_ai_proposal WHERE id=?", (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    final = dict(json.loads(p["axes"]))
    for a in AXES:
        v = (axes or {}).get(a)
        if v not in (None, ""):
            final[a] = int(v)
    missing = [AXIS_LABEL[a] for a in AXES if final.get(a) is None]
    if missing:
        raise ValueError("点の無い軸があります（AI が根拠を書けなかった軸）: " + "、".join(missing)
                         + "。数字を入れてから採用してください")
    changed = final != json.loads(p["axes"])
    note_s = (f"AI案（{p['model']}・{p['created_at']}）を" + ("直して採用" if changed else "そのまま採用")
              + (f"。{note.strip()}" if note and note.strip() else ""))
    res = idea_m.score_v2(p["idea_id"], final, user_id,
                          scored_by="human" if changed else "ai", model=p["model"], source_note=note_s)
    with store.tx() as c:
        c.execute("UPDATE idea_ai_proposal SET state='採用', decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (user_id, store.now_s(), note_s, proposal_id))
    return {"score": res, "changed": changed, "idea_id": p["idea_id"]}


def reject(proposal_id: int, user_id: str, note: str = "") -> dict:
    p = store.one("SELECT * FROM idea_ai_proposal WHERE id=?", (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    with store.tx() as c:
        c.execute("UPDATE idea_ai_proposal SET state='見送り', decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (user_id, store.now_s(), (note or "").strip() or None, proposal_id))
    return {"ok": True, "idea_id": p["idea_id"]}
