#!/usr/bin/env python3
"""
AI採点（F-1-11）。**呼べる形まで。実際には呼ばない。**

現行はこうなっている（要件定義 2-3 の1件目）:

    未採点行をドラッグでコピー → claude.ai を開く → 指示書を貼る →
    行を貼る → 配列数式が返る → G列に貼る → 範囲を選んで値のみ貼り直す
    （※この操作をしないと、並べ替えで数式がずれて壊れます）

この7ステップを廃止して、**採点結果に rubric版・実行日時・モデル名を残す**のが
F-1-11 の中身。ここでは**差し替え可能な口**と、その記録の作法だけを実装する。

**外部APIを呼ばない。**このファイルは `urllib` も `http` も `socket` も import
しない（tests がそれを検査する）。理由は全体設計書 第11章 ⑩ ——
`newproduct-*` のAI予算枠（AutoGrowth の `ai_budget.json`）がまだ取れていない。
**枠が無いまま呼べる口を開けると、上限の無い呼び出しになる。**

    既定は off。`setting.ai_scoring_enabled = '0'`。

実際に呼ぶ実装を足すときは、`Scorer` と同じ形のものを作って `run_batch()` に
渡すだけでよい。**server.py からは渡さない。**（渡すのは、予算枠が付いてから）
"""
from __future__ import annotations

from . import idea as idea_m
from . import store

MODEL_UNKNOWN = "未設定"


class Scorer:
    """採点する人（または機械）の形。**これに合わせれば差し替えられる。**

    `model` は記録に残る名前。`score(idea)` は v2 の軸の素点を返す:
        {"demand": 1〜10, "market_size": 1〜10, "advantage": 1〜10,
         "theme_fit": 1〜10}

    **想定粗利額と生産方法は返さない。**円と 1〜5 は人が入れる項目で、
    AI に推測させると「分からない」が数字に化ける（N-10）。
    """

    model = MODEL_UNKNOWN

    def score(self, idea: dict) -> dict:      # pragma: no cover - 形の宣言
        raise NotImplementedError


class Disabled(Scorer):
    """既定。**呼ぶと理由を言って止まる。**黙って 0 を返さない。"""

    model = "（AI採点は無効）"

    def score(self, idea: dict) -> dict:
        raise RuntimeError(reason_off())


def reason_off() -> str:
    return ("AI採点は既定で off です。`newproduct-*` のAI予算枠"
            "（AutoGrowth の ai_budget.json）が未取得のためです"
            "（全体設計書 第11章 ⑩）。枠が付いてから設定で on にしてください。")


def enabled() -> bool:
    return str(idea_m.setting("ai_scoring_enabled", "0")).strip() in ("1", "true", "on")


def status() -> dict:
    """画面に出す状態。**「使えない」を「使っていない」と混ぜない。**"""
    return {
        "enabled": enabled(),
        "reason": None if enabled() else reason_off(),
        "rubric_version": idea_m.V2_VERSION,
        "records": {
            "kept": ["rubric版（idea_score.rubric_version）",
                     "実行日時（idea_score.scored_at）",
                     "モデル名（idea_score.model）"],
            "why": "F-1-11。どの基準で・いつ・何が付けた点なのかが残らないと、"
                   "採点が当たったかどうかを後から検証できない（Q1-D）",
        },
        "ai_scored": store.val(
            "SELECT COUNT(*) FROM idea_score WHERE scored_by='ai'", (), 0),
    }


def run_batch(idea_ids: list[str], user_id: str, scorer: Scorer | None = None,
              force: bool = False) -> dict:
    """未採点のアイデアをまとめて採点する。

    `scorer` を渡さなければ何も呼ばない（`Disabled`）。
    `force` は tests から差し替えた採点器を使うためのもので、
    **画面からは渡さない。**
    """
    if not (force or enabled()):
        return {"enabled": False, "reason": reason_off(), "scored": 0,
                "skipped": len(idea_ids), "results": []}
    sc = scorer or Disabled()
    out, errs = [], []
    for iid in idea_ids:
        r = store.one("SELECT * FROM idea WHERE id=?", (iid,))
        if r is None:
            errs.append({"id": iid, "error": "アイデアがありません"})
            continue
        try:
            axes = sc.score(dict(r))
            res = idea_m.score_v2(iid, axes, user_id, scored_by="ai",
                                  model=sc.model,
                                  source_note="AI採点（F-1-11）")
            out.append({"id": iid, "total": res["total"], "rank": res["rank"],
                        "model": sc.model})
        except (ValueError, LookupError, RuntimeError) as e:
            # **落ちた理由を残す。**黙って飛ばすと「採点済み」に見える
            errs.append({"id": iid, "error": str(e)})
    return {"enabled": True, "model": sc.model, "scored": len(out),
            "skipped": len(errs), "results": out, "errors": errs}
