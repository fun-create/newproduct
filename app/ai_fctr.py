#!/usr/bin/env python3
"""
トレンド（FCTR）の自社側70点の AI 案（FR-200・ADR-115・2026-10-19 十文字さんの選択）。

本番の目視チェックで「AIによる自動判定（案として）入力させたい」。自社側70点（FR-138）は
商品相性 25・製造運用 20・発売速度 15・利益性 10 の4軸で、**基準は商品開発部が決める**。
ここでは AI に、根拠つきの「案」を出させるだけ:

- **AI は案を出すだけ**（FR-145）。案は `fctr_ai_proposal` に置き、`fctr_self_score` には入れない。
  人が「採る」（数字を直してもよい）を押したときだけ点になる。見送った案も消さない
- 渡すのは、テーマ（名前・客層・市場性・根拠の一文）と、**同社が作れるもの**（開発タイプ・商品タイプの名前）だけ。
  売上・価格・設備の能力などの数字は渡さないし、作らせない（N-10・FR-148）
- 根拠の無い軸は案にしない。確かめないと言えないことは「未確認」に並べさせる
- 予算は `newproduct-text` に記録し、記録できなければ保存しない（ai_score と同じ）

このファイル自体は外部と通信しない（`claude` を呼ぶのは ai_cli）。
"""
from __future__ import annotations

import json
import re
import threading

from . import ai_budget
from . import ai_cli
from . import ai_score
from . import fctr
from . import store

KIND = "fctr_self"
MAX_PER_RUN = 30
AXES = [(k, label, mx) for k, label, mx in fctr.SELF_AXES]
AXIS_LABEL = {k: label for k, label, _mx in AXES}
AXIS_MAX = {k: mx for k, _label, mx in AXES}

SYSTEM = """あなたは FUN-CREATE株式会社（愛知県西尾市・2006年創業）の新商品開発の補助です。
同社は推し活・ライフイベント向けのカスタムグッズ（アクリルスタンド・うちわ・オリジナルグッズ）を自社で製造し、
自社ECと楽天・Amazon・Yahoo!ショッピング・ギフトモールで販売しています。

役割: 世の中の需要テーマ（トレンド）それぞれについて、「同社がその商品を出すのに向いているか」を4つの軸で採点した「案」を出します。決めるのは人です。

守ること:
- 根拠は、渡されたテーマの情報・同社が作れるものの一覧・一般に知られていることだけから書く
- 売上・価格・原価・設備の能力・リードタイムなどの数字を作らない
- ウェブも社内データも見られない。確かめないと言えないことは unverified に短く並べる
- 根拠が書けない軸は点を付けず null にする
- 軸ごとの根拠は1〜2文・80字以内
- 答えは JSON の配列だけ。前後に文章やコードブロックを付けない"""


def _capabilities() -> dict:
    """同社が作れるもの（マスタの名前だけ）。**数字は渡さない。**"""
    return {"開発タイプ（同社がふだん出している商品の作り方）": [r["label"] for r in store.q("SELECT label FROM flow_type ORDER BY seq")],
            "商品タイプ": [r["label"] for r in store.q("SELECT label FROM product_kind ORDER BY seq")]}


def themes_now() -> list[dict]:
    """いまのトレンドの表のテーマ（客層つき）。同じテーマが2つの客層に出ることがあるので、テーマごとに1つにまとめる。"""
    b = fctr.board(top_n=fctr.TOP_N, faded=True)
    out = {}
    for sg in b.get("segments") or []:
        for t in sg["themes"]:
            x = out.setdefault(t["theme_id"], {"theme_id": t["theme_id"], "label": t["label"], "segments": [],
                                               "market": t["decayed"], "evidence": t.get("evidence"),
                                               "self": t.get("self")})
            x["segments"].append(sg["name"])
            x["market"] = max(x["market"], t["decayed"])
    return list(out.values())


def candidates() -> list[str]:
    """既定の対象: 自社側の点がまだ1つも無く、出しかけの案も無いテーマ。"""
    pend = {r[0] for r in store.q("SELECT theme_id FROM fctr_ai_proposal WHERE state='提案'")}
    out = []
    for t in themes_now():
        ss = t["self"] or {}
        if t["theme_id"] in pend:
            continue
        if all(ss.get(k) is None for k, _l, _m in AXES):
            out.append(t["theme_id"])
    return out


def _item(t: dict) -> dict:
    d = {"id": t["theme_id"], "テーマ": t["label"], "客層": "・".join(t["segments"]),
         "市場性（30点中・競合の動きから）": t["market"]}
    if t.get("evidence"):
        d["市場性の根拠"] = t["evidence"]
    return d


def build_prompt(themes: list[dict]) -> str:
    axes = "\n".join(f"- {k}（{label}・0〜{mx}点）: {AXIS_HELP[k]}" for k, label, mx in AXES)
    return ("次の需要テーマそれぞれに、同社がその商品を出すのに向いているかを4つの軸で採点した案を付けてください。\n\n"
            "軸:\n" + axes + "\n\n"
            "同社が作れるもの:\n" + json.dumps(_capabilities(), ensure_ascii=False, indent=1) + "\n\n"
            "答えの形（配列・テーマの数だけ）:\n"
            '[{"id":"…","fit":18,"ops":12,"speed":9,"profit":6,'
            '"reasons":{"fit":"…","ops":"…","speed":"…","profit":"…"},"unverified":["…"]}]\n\n'
            "テーマ:\n" + json.dumps([_item(t) for t in themes], ensure_ascii=False, indent=1))


AXIS_HELP = {
    "fit": "同社の作れるもの（上の一覧）で、このテーマの商品を出せるか。一覧のどれかでそのまま出せるなら高く、新しい種類の商品が要るなら低く",
    "ops": "いまの作り方・流れのままで作れるか。新しい設備や外注が要りそうなら低く",
    "speed": "既にある商品の形の流用で早く出せるか。デザインや仕様を一から作るなら低く",
    "profit": "名入れ・セット・ギフト包装などで単価を上げやすいか。値下げ競争になりやすい商品なら低く",
}


def parse_answer(text: str, ids: list[str]) -> dict:
    m = re.search(r"\[.*\]", (text or "").strip(), re.S)
    if not m:
        raise ai_score.EnvFailure("返事に JSON の配列がありませんでした")
    try:
        arr = json.loads(m.group(0))
    except ValueError:
        raise ai_score.EnvFailure("返事の JSON が読めませんでした") from None
    out = {}
    for x in arr if isinstance(arr, list) else []:
        if isinstance(x, dict) and x.get("id") in ids:
            out[x["id"]] = x
    for i in ids:
        out.setdefault(i, {"error": "返事にこのテーマがありませんでした"})
    return out


def check(res: dict) -> tuple[dict, dict, list] | str:
    """1件分を確かめる。**根拠の無い軸は案にしない・上限の外は断る**（推測で直さない）。"""
    if "error" in res:
        return res["error"]
    axes, reasons, unv = {}, {}, []
    for k, label, mx in AXES:
        v = res.get(k)
        if v is None:
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            return f"{label}が数ではありません"
        if not 0 <= n <= mx:
            return f"{label}が 0〜{mx} の外です（{v}）"
        why = str((res.get("reasons") or {}).get(k) or "").strip()
        if not why:
            continue
        axes[k], reasons[k] = (int(n) if n == int(n) else n), why[:200]
    if not axes:
        return "根拠のある軸が1つもありませんでした"
    for x in res.get("unverified") or []:
        s = str(x).strip()
        if s:
            unv.append(s[:120])
    for k, label, _mx in AXES:
        if k not in axes:
            unv.append(f"{label}は根拠が書けず、点を付けていません")
    return axes, reasons, unv[:10]


def start_run(theme_ids: list[str] | None, user_id: str, *, runner=None, sync: bool = False,
              force: bool = False) -> dict:
    if not force:
        if not fctr.can_score(user_id):
            raise PermissionError("AI に自社側の点の案を出させられるのは、商品開発部・管理者・社長の業務ロールの人です")
        b, pre = ai_budget.check(ai_score.JOB), ai_cli.preflight()
        if not ai_score.enabled(b, pre):
            return {"started": False, "reason": ai_score.reason_off(b, pre)}
        if store.val("SELECT COUNT(*) FROM ai_run WHERE stage IN ('待ち','実行中')", (), 0):
            return {"started": False, "reason": "いま別の回が走っています。終わってから押してください"}
    ids = list(dict.fromkeys(theme_ids or candidates()))
    if not ids:
        return {"started": False, "reason": "対象のテーマがありません（自社側の点がまだ無く、案も出ていないテーマが0件）"}
    if len(ids) > MAX_PER_RUN:
        return {"started": False, "reason": f"1回に出せるのは {MAX_PER_RUN} 件までです（{len(ids)} 件）"}
    with store.tx() as c:
        rid = c.execute("INSERT INTO ai_run (job,kind,stage,target_json,requested_by,requested_at) VALUES (?,?,?,?,?,?)",
                        (ai_score.JOB, KIND, "待ち", json.dumps(ids), user_id, store.now_s())).lastrowid
    if sync:
        _work(rid, runner, record=not force)
    else:
        threading.Thread(target=_work, args=(rid, runner), daemon=True, name=f"ai-fctr-{rid}").start()
    return {"started": True, "run_id": rid, "n": len(ids)}


def _work(run_id: int, runner=None, record: bool = True) -> None:
    store.ex("UPDATE ai_run SET stage='実行中' WHERE id=?", (run_id,))
    store.conn().commit()
    ids = json.loads(store.val("SELECT target_json FROM ai_run WHERE id=?", (run_id,)))
    model, cost, ok, ng = None, 0.0, 0, 0
    try:
        by = {t["theme_id"]: t for t in themes_now()}
        themes = [by[i] for i in ids if i in by]
        ng += len(ids) - len(themes)
        if not themes:
            raise ai_score.EnvFailure("対象のテーマが、いまのトレンドの表にありません")
        r = ai_cli.ask(build_prompt(themes), SYSTEM, runner=runner)
        model, cost = r.get("model"), float(r.get("cost_usd") or 0.0)
        if not r["ok"]:
            raise ai_score.EnvFailure(r["error"])
        res = parse_answer(r["text"], [t["theme_id"] for t in themes])
        if record:
            try:
                ai_budget.record(ai_score.JOB, model, cost, note=f"自社側70点の案 回{run_id}",
                                 request_id=f"newproduct-ai-run{run_id}")
            except (ai_budget.BudgetUnavailable, ai_budget.OverCap) as e:
                raise ai_score.EnvFailure(f"使った額を記録できませんでした: {e}") from None
        now = store.now_s()
        caps = _capabilities()
        with store.tx() as c:
            for t in themes:
                got = check(res.get(t["theme_id"]) or {"error": "返事がありません"})
                if isinstance(got, str):
                    ng += 1
                    continue
                axes, reasons, unv = got
                c.execute("UPDATE fctr_ai_proposal SET state='見送り', decided_by='（自動）', decided_at=?, "
                          "decided_note='新しい案に置き換えた' WHERE theme_id=? AND state='提案'", (now, t["theme_id"]))
                c.execute("INSERT INTO fctr_ai_proposal (theme_id,run_id,axes,reasons,unverified,inputs,model,created_at) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (t["theme_id"], run_id, json.dumps(axes), json.dumps(reasons, ensure_ascii=False),
                           json.dumps(unv, ensure_ascii=False),
                           json.dumps({"出所": "トレンド（FCTR）の週次結果と、NEW PRODUCT のマスタ（開発タイプ・商品タイプ）",
                                       "項目": _item(t), "同社が作れるもの": caps}, ensure_ascii=False), model, now))
                ok += 1
        ai_score._finish(run_id, "完了", ok, ng, model, cost)
    except ai_score.EnvFailure as e:
        ai_score._finish(run_id, "失敗", ok, ng, model, cost, "env", str(e))
    except Exception as e:                                   # 想定外も「実行中」のまま残さない
        ai_score._finish(run_id, "失敗", ok, ng, model, cost, "bug", f"{type(e).__name__}: {e}")


def _prop(r) -> dict:
    d = dict(r)
    for k in ("axes", "reasons", "unverified", "inputs"):
        d[k] = json.loads(d[k]) if d.get(k) else ([] if k == "unverified" else {})
    d["axes_view"] = [{"key": k, "label": label, "max": mx, "value": d["axes"].get(k), "reason": d["reasons"].get(k)}
                      for k, label, mx in AXES]
    return d


def pending_by_theme() -> dict:
    return {r["theme_id"]: _prop(r) for r in store.q("SELECT * FROM fctr_ai_proposal WHERE state='提案' ORDER BY id")}


def adopt(proposal_id: int, user_id: str, axes: dict | None = None, note: str = "") -> dict:
    """案を自社側の点にする。**空の軸は人が入れてから**（AI が根拠を書けなかった軸を 0 にしない）。"""
    p = store.one("SELECT * FROM fctr_ai_proposal WHERE id=?", (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    orig = json.loads(p["axes"])
    final = dict(orig)
    for k, _label, _mx in AXES:
        v = (axes or {}).get(k)
        if v not in (None, ""):
            final[k] = v
    missing = [AXIS_LABEL[k] for k, _l, _m in AXES if final.get(k) in (None, "")]
    if missing:
        raise ValueError("点の無い軸があります（AI が根拠を書けなかった軸）: " + "、".join(missing) + "。数字を入れてから採ってください")
    changed = any(str(final[k]) != str(orig.get(k)) for k, _l, _m in AXES)
    note_s = (f"AI案（{p['model']}・{p['created_at']}）を" + ("直して採用" if changed else "そのまま採用")
              + (f"。{note.strip()}" if note and note.strip() else ""))
    fctr.save_self(p["theme_id"], {**{k: final[k] for k, _l, _m in AXES}, "note": note_s}, user_id)
    with store.tx() as c:
        c.execute("UPDATE fctr_ai_proposal SET state='採用', decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (user_id, store.now_s(), note_s, proposal_id))
    return {"ok": True, "changed": changed, "theme_id": p["theme_id"]}


def reject(proposal_id: int, user_id: str, note: str = "") -> dict:
    if not fctr.can_score(user_id):
        raise PermissionError("自社側の点を扱えるのは、商品開発部・管理者・社長の業務ロールの人です")
    p = store.one("SELECT * FROM fctr_ai_proposal WHERE id=?", (proposal_id,))
    if p is None:
        raise LookupError("その案はありません")
    if p["state"] != "提案":
        raise ValueError(f"この案はもう「{p['state']}」です")
    with store.tx() as c:
        c.execute("UPDATE fctr_ai_proposal SET state='見送り', decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (user_id, store.now_s(), (note or "").strip() or None, proposal_id))
    return {"ok": True, "theme_id": p["theme_id"]}


def status(user_id: str | None = None) -> dict:
    pre = ai_cli.preflight()
    b = ai_budget.check(ai_score.JOB)
    on = ai_score.enabled(b, pre)
    last = store.one("SELECT * FROM ai_run WHERE kind=? ORDER BY id DESC LIMIT 1", (KIND,))
    return {"enabled": on, "reason": None if on else ai_score.reason_off(b, pre),
            "can_run": bool(user_id) and fctr.can_score(user_id), "candidates": len(candidates()),
            "pending": store.val("SELECT COUNT(*) FROM fctr_ai_proposal WHERE state='提案'", (), 0),
            "last_run": ai_score.run_view(last["id"]) if last else None}
