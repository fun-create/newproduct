#!/usr/bin/env python3
"""
月次レポート（F-10-7・FR-116）。2026-10-02 十文字さん「提案に従う・お任せ」（ADR-054）。

- 前の月の、売上（3店）・商品ABC・新商品・開発の進み・年間プラン・トレンドを **1枚にまとめる**
- **事実の数字だけ。**AI の文章は入れない（根拠の無い文を混ぜないため。入れるときは別に決める）
- 1つの節が作れなくても、ほかは作る。作れなかった節は**理由つきで**レポートに書く（黙って抜かない）
- 送らない。NEW PRODUCT の画面で読む
"""
from __future__ import annotations

import datetime as _dt
import json

from app import store


def _yen(v):
    return "未計測" if v is None else f"{v:,.0f}円"


def _yoy(cur, prev):
    if cur is None or not prev:
        return "—"
    x = (cur - prev) / prev * 100
    return f"{'+' if x > 0 else '−' if x < 0 else '±'}{abs(x):.1f}%"


def prev_month(today: _dt.date | None = None) -> str:
    t = today or store.today()
    first = t.replace(day=1) - _dt.timedelta(days=1)
    return first.strftime("%Y-%m")


def build(month: str) -> tuple[str, list[str]]:
    from app import abc, salesfeed
    problems: list[str] = []
    L = [f"# NEW PRODUCT 月次レポート {month}", "",
         f"作成 {store.now_s()}。金額は税込の商品代（取消・返金を除く）。会社の正式な売上（税抜・請求金額）とは一致しません。", ""]

    # 1. 売上（3店）
    L += ["## 1. 売上（3店・売上フィード）", "", "| サイト | 売上 | 前年同月 | 前年比 |", "|---|---|---|---|"]
    try:
        py = salesfeed._month_add(month, -12)
        for key, (_shop, label, _c) in salesfeed.SHOPS.items():
            tot, _m = salesfeed.monthly(key)
            cur, prev = tot.get(month), (tot.get(py) if py >= salesfeed.SINCE[:7] else None)
            L.append(f"| {label} | {_yen(cur)} | {_yen(prev)} | {_yoy(cur, prev)} |")
        L.append("")
        L.append("Amazon（会社出荷・FBA）は売上フィードに入っていないため、ここには出ません。")
    except Exception as e:                                   # noqa: BLE001
        problems.append(f"売上: {e}")
        L.append(f"（作れませんでした: {e}）")
    L.append("")

    # 2. 商品ABC
    L += ["## 2. 商品ABC（前年同月と比較）", ""]
    for key, (_shop, label, _c) in salesfeed.SHOPS.items():
        try:
            r = abc.analyze(key, month, month, "yoy")
            c = r["current"]
            L.append(f"### {label}（{c['count']} 商品番号・{_yen(c['total'])}）")
            L.append("")
            L.append("| 区分 | 商品数 | 売上 | 構成比 |")
            L.append("|---|---|---|---|")
            for k in "ABC":
                v = c["classes"][k]
                L.append(f"| {k} | {v['count']} | {_yen(v['revenue'])} | {v['share']}% |")
            L.append("")
            tops = [x for x in r["rows"] if x["class"] == "A"][:5]
            if tops:
                L.append("A の上位5: " + "、".join(f"{x['name'] or x['name_label']}（{x['key']}・{_yen(x['revenue'])}）" for x in tops))
                L.append("")
            if r.get("changes"):
                ch = r["changes"]
                L.append("| 比較 | 商品数 | 差額 |")
                L.append("|---|---|---|")
                for k in ("増加", "減少", "消滅", "新規"):
                    L.append(f"| {k} | {ch[k]['count']} | {'+' if ch[k]['diff'] > 0 else ''}{ch[k]['diff']:,.0f}円 |")
                L.append("")
                for k in ("増加", "減少"):
                    xs = ch[k]["rows"][:3]
                    if xs:
                        L.append(f"{k}の大きい3: " + "、".join(f"{x['name'] or x['name_label']}（{'+' if x['diff'] > 0 else ''}{x['diff']:,.0f}円）" for x in xs))
                L.append("")
            elif r.get("why_compare"):
                L += [r["why_compare"], ""]
        except Exception as e:                               # noqa: BLE001
            problems.append(f"商品ABC {label}: {e}")
            L += [f"### {label}", f"（作れませんでした: {e}）", ""]

    # 3. 新商品
    L += ["## 3. 新商品（NEW PRODUCT から出した商品・作成時点）", ""]
    try:
        from app import sales
        n = sales.new_product_summary()
        L.append(f"- 本数（発売から1年以内）: {n['count']} 本" + (f"（{'・'.join(f'{k} {v}' for k, v in n['by_flow'].items())}）" if n["by_flow"] else ""))
        L.append(f"- 売上として数える案件: {n['counted']} 件／全額 {_yen(n['totals']['全額'])}／増分 {_yen(n['totals']['増分'])}"
                 f"／未計測 {n['totals']['未計測']} 件・方式未選択 {n['totals']['方式未選択']} 件")
    except Exception as e:                                   # noqa: BLE001
        problems.append(f"新商品: {e}")
        L.append(f"（作れませんでした: {e}）")
    L.append("")

    # 4. 開発の進み
    L += ["## 4. 開発の進み", ""]
    try:
        from app import task
        st = store.q("SELECT stage, COUNT(*) n FROM project GROUP BY stage ORDER BY COUNT(*) DESC")
        L.append("- ステージ別: " + "・".join(f"{r['stage']} {r['n']}" for r in st))
        lo, hi = month + "-01", _add_month(month) + "-01"
        gr = store.q("SELECT gate, result, COUNT(*) n FROM gate_review WHERE approved_at >= ? AND approved_at < ? "
                     "GROUP BY gate, result ORDER BY gate", (lo, hi))
        L.append("- この月の関門の判定: " + ("・".join(f"{r['gate']} {r['result']} {r['n']}" for r in gr) if gr else "なし"))
        up = store.q("SELECT id, internal_name, launch_date FROM project WHERE launch_date >= ? AND launch_date < ? "
                     "AND stage NOT IN ('中止','発売済','追跡中','評価完了') ORDER BY launch_date",
                     (store.today_s(), (store.today() + _dt.timedelta(days=60)).isoformat()))
        L.append("- 60日以内に発売予定: " + ("、".join(f"{r['launch_date']} {r['internal_name'] or r['id']}" for r in up) if up else "なし"))
        od = store.val("SELECT COUNT(*) FROM task WHERE due_on < ? AND status NOT IN ('完了','対象外')", (store.today_s(),), 0)
        L.append(f"- 期限切れのタスク（作成時点）: {od} 件")
        pl = task.post_launch_pending()
        L.append(f"- 発売後チェックの未処理: {pl['value'] or pl['state']}")
    except Exception as e:                                   # noqa: BLE001
        problems.append(f"開発の進み: {e}")
        L.append(f"（作れませんでした: {e}）")
    L.append("")

    # 5. 年間プラン
    L += ["## 5. 年間プラン", ""]
    try:
        from app import plan
        sc = plan.slot_consumption()
        L.append(f"- 今月の枠の消化（作成時点の月）: {sc.get('value') or sc.get('state')}" + (f"　{sc['why']}" if sc.get("why") else ""))
        vs = store.q("SELECT fiscal_year, label, state FROM plan_version ORDER BY fiscal_year DESC, id DESC LIMIT 3")
        L.append("- 版: " + "・".join(f"{r['fiscal_year']}年度 {r['label']}（{r['state']}）" for r in vs))
    except Exception as e:                                   # noqa: BLE001
        problems.append(f"年間プラン: {e}")
        L.append(f"（作れませんでした: {e}）")
    L.append("")

    # 6. トレンド
    L += ["## 6. トレンド上位（FCTR・最新週）", ""]
    try:
        from app import fctr
        b = fctr.board()
        if b["why"]:
            L.append(f"（{b['why']}）")
        for s in b["segments"]:
            L.append(f"- {s['name']}: " + "、".join(f"{t['label']}（{t['decayed']}）" for t in s["themes"] if t["top"]))
        L.append(f"- 週: {b['latest_week'] or '—'}")
    except Exception as e:                                   # noqa: BLE001
        problems.append(f"トレンド: {e}")
        L.append(f"（作れませんでした: {e}）")
    L.append("")

    L += ["## 出どころ", "",
          "- 売上・商品ABC: 経営管理の売上フィード（各店の API・毎晩）。分類は生産管理の紐付け表",
          "- 新商品・開発・プラン: NEW PRODUCT",
          "- トレンド: Auto GROWTH の FCTR（市場性30点のみ）"]
    if problems:
        L += ["", "## 作れなかった節", ""] + [f"- {p}" for p in problems]
    return "\n".join(L) + "\n", problems


def _add_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:7]) + 1
    return f"{y + (mo - 1) // 12:04d}-{(mo - 1) % 12 + 1:02d}"


def save(month: str, by: str) -> dict:
    body, problems = build(month)
    with store.tx() as c:
        c.execute("INSERT INTO monthly_report (month,body_md,generated_at,generated_by,problems) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(month) DO UPDATE SET body_md=excluded.body_md,generated_at=excluded.generated_at,"
                  "generated_by=excluded.generated_by,problems=excluded.problems",
                  (month, body, store.now_s(), by, json.dumps(problems, ensure_ascii=False) if problems else None))
    return {"ok": True, "month": month, "problems": problems}


def listing() -> list[dict]:
    return store.rows(store.q("SELECT month, generated_at, generated_by, problems FROM monthly_report ORDER BY month DESC"))


def get(month: str) -> dict | None:
    r = store.one("SELECT * FROM monthly_report WHERE month=?", (month,))
    return dict(r) if r else None
