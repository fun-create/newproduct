#!/usr/bin/env python3
"""
LP依頼書（F-11・FR-118）。2026-10-02 十文字さん「案件カルテの項目から作る」。

- **カルテにあることだけ**を並べる。無いものは「（未記入）」と書き、冒頭に「埋まっていない項目」を出す
  （空欄のまま渡すと、受け手が「書くことが無い」のか「まだ決まっていない」のか分からない）
- 生成はするが**送らない**。文面を見て、人が貼り付けて渡す
- お客さまの情報は扱わない（カルテに無い）
"""
from __future__ import annotations

from app import store

PENDING = "（未記入）"
# (見出し, 節キー) — カルテの節をそのまま使う
SECTIONS = [
    ("コンセプト", "C.concept"), ("ターゲット・使用シーン", "C.target"), ("ニーズ", "C.needs"),
    ("差別化", "C.diff"), ("競合", "C.competitor"), ("商品名の検討", "F.name"),
    ("販路別の掲載", "F.channel"), ("LP依頼の補足", "F.lp"),
]


def build(pid: str) -> dict:
    from app import compat, cost, project as project_m
    p = store.one("SELECT p.*, f.label AS flow_label FROM project p LEFT JOIN flow_type f ON f.code=p.flow_type "
                  "WHERE p.id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    p = dict(p)
    secs = {r["section_key"]: (r["body"] or "").strip() for r in store.q(
        "SELECT section_key, body FROM project_section WHERE project_id=?", (pid,))}
    missing, lines = [], []

    def val(label, v):
        v = (str(v).strip() if v is not None else "")
        if not v:
            missing.append(label)
            return PENDING
        return v

    lines.append(f"■ LP依頼書（NEW PRODUCT 案件 {pid}・{store.today_s()} 作成）")
    lines.append("")
    lines.append("【基本】")
    lines.append(f"・社内呼称: {val('社内呼称', p['internal_name'])}")
    lines.append(f"・分類: {project_m.product_label(p)}")
    lines.append(f"・開発タイプ: {val('開発タイプ', p['flow_label'])}")
    lines.append(f"・発売予定日: {val('発売予定日', p['launch_date'])}")
    lines.append(f"・機会（なぜその日か）: {val('機会', p['occasion'])}")
    lines.append(f"・担当: {val('担当', p['owner'])}")
    lines.append(f"・概要・仕様: {val('概要・仕様', p['summary'])}")
    for title, key in SECTIONS:
        lines.append("")
        lines.append(f"【{title}】")
        body = secs.get(key, "")
        if key == "C.diff" and body:
            lines += ["・" + ln.strip() for ln in body.splitlines() if ln.strip()]
        else:
            lines.append(val(title, body))
    # 価格（試算原価の最新の版に販売価格があれば、それを正とする）
    lines.append("")
    lines.append("【販売価格】")
    v = store.one("SELECT * FROM cost_version WHERE project_id=? ORDER BY version DESC LIMIT 1", (pid,))
    if v is not None and v["price_ex_tax"] is not None:
        rate = v["tax_rate"] if v["tax_rate"] is not None else cost.TAX_DEFAULT
        lines.append(f"・{v['price_ex_tax']:,.0f}円（税抜）／{round(v['price_ex_tax'] * (1 + rate / 100)):,}円（税込）"
                     f"　※試算原価 v{v['version']} の販売価格")
    else:
        lines.append(val("販売価格", secs.get("D.price", "")))
    # バリエーション
    vs = store.q("SELECT label, spec, state, launch_date FROM project_variant WHERE project_id=? "
                 "AND state != '見送り' ORDER BY label", (pid,))
    lines.append("")
    lines.append("【バリエーション】")
    lines += [f"・{r['label']}" + (f"（{r['spec']}）" if r["spec"] else "") + (f" 発売 {r['launch_date']}" if r["launch_date"] else "")
              for r in vs] or ["なし"]
    # 対応確認
    cs = compat.overview(pid, "")["rows"]
    lines.append("")
    lines.append("【対応確認】")
    if cs:
        lines += [f"・{r['item']}: {r['result']}" + (f"（{r['note']}）" if r["note"] else "") for r in cs]
    else:
        lines.append(val("対応確認", ""))
    # seisan
    codes = [r["product_code"] for r in store.q(
        "SELECT product_code FROM seisan_registration WHERE project_id=? AND state='登録済'", (pid,))]
    lines.append("")
    lines.append("【seisan 商品コード】")
    lines.append("、".join(codes) if codes else "未登録（発売可 G5 の前に登録）")
    head = (["※ 埋まっていない項目: " + "、".join(missing), ""] if missing else [])
    text = "\n".join([lines[0], ""] + head + lines[2:])
    return {"project_id": pid, "text": text, "missing": missing}
