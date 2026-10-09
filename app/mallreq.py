#!/usr/bin/env python3
"""
4モールの出品依頼データ（F-11-2・FR-119・2026-10-09 十文字さんの選択・ADR-075）。

LP依頼書（ADR-053）と同じ作法:
- **カルテにあることだけ**を並べる。無いものは「（未記入）」と書き、冒頭に「埋まっていない項目」を出す
- 生成はするが**送らない**。人が見て、出品の担当へ渡す（モールへの登録は人が行う・CLAUDE.md 原則2）
- お客さまの情報は扱わない。CIP の商品名は読まない（N-6-2）。商品名は「商品名の検討」（F.name）の案を使う

モールごとに要る項目（各モールの出品で一般に必要になるもの）を並べ、埋まっていないものを示す。
**文字数の上限などモールの規定は、ここに書き写さない**（規定は変わる。写すと古くなっても気づけない）。
文字数は数えて出すので、登録の前に各モールの最新の規定で確かめる。
店の商品番号は、CIP の紐付け表（店の商品番号 → 共通商品コード）に登録済みのものだけを出す。
"""
from __future__ import annotations

from app import store

PENDING = "（未記入）"
MALLS = {
    "rakuten": {"label": "楽天市場", "store": "楽天",
                "extra": ["商品管理番号（店の商品番号）", "楽天のジャンル", "送料の区分"]},
    "amazon": {"label": "Amazon", "store": "amazon",
               "extra": ["JAN（または製品コード免除の有無）", "ブランド名", "出荷方法（自社発送／FBA）"]},
    "yahoo": {"label": "Yahoo!ショッピング", "store": "Yahoo",
              "extra": ["商品コード（店の商品番号）", "プロダクトカテゴリ", "送料の区分"]},
    "giftmall": {"label": "ギフトモール", "store": "ギフトモール",
                 "extra": ["ギフト対応（ラッピング・のし・メッセージカード）", "カテゴリ"]},
}


def build(pid: str) -> dict:
    from app import cost, project as project_m
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    p = dict(p)
    secs = {r["section_key"]: (r["body"] or "").strip() for r in store.q(
        "SELECT section_key, body FROM project_section WHERE project_id=?", (pid,))}
    missing: list[str] = []

    def val(label, v):
        v = (str(v).strip() if v is not None else "")
        if not v:
            if label not in missing:
                missing.append(label)
            return PENDING
        return v

    # 共通
    v = store.one("SELECT * FROM cost_version WHERE project_id=? ORDER BY version DESC LIMIT 1", (pid,))
    if v is not None and v["price_ex_tax"] is not None:
        rate = v["tax_rate"] if v["tax_rate"] is not None else cost.TAX_DEFAULT
        price = f"{round(v['price_ex_tax'] * (1 + rate / 100)):,}円（税込）　※試算原価 v{v['version']} の販売価格"
    else:
        price = val("販売価格", secs.get("D.price", ""))
    names = [ln.strip() for ln in secs.get("F.name", "").splitlines() if ln.strip()]
    diff = ["・" + ln.strip() for ln in secs.get("C.diff", "").splitlines() if ln.strip()]
    vs = store.q("SELECT label, spec FROM project_variant WHERE project_id=? AND state != '見送り' ORDER BY label", (pid,))
    codes = [r["product_code"] for r in store.q(
        "SELECT product_code FROM seisan_registration WHERE project_id=? AND state='登録済' AND product_code IS NOT NULL", (pid,))]
    store_codes = _store_codes(codes)

    common = [
        f"・社内呼称: {val('社内呼称', p['internal_name'])}",
        f"・商品名（案）: " + ("／".join(names) if names else val("商品名（案）", "")),
        f"・分類: {project_m.product_label(p)}",
        f"・発売予定日: {val('発売予定日', p['launch_date'])}",
        f"・販売価格: {price}",
        f"・概要・仕様: {val('概要・仕様', p['summary'])}",
        f"・ターゲット・使用シーン: {val('ターゲット・使用シーン', secs.get('C.target'))}",
        f"・コンセプト: {val('コンセプト', secs.get('C.concept'))}",
        "・差別化: " + ("\n" + "\n".join(diff) if diff else val("差別化", "")),
        "・バリエーション: " + ("、".join(r["label"] + (f"（{r['spec']}）" if r["spec"] else "") for r in vs) or "なし"),
        f"・CIP 商品コード: " + ("、".join(codes) if codes else val("CIP 商品コード", "")),
        f"・販路別の掲載（カルテ）: {val('販路別の掲載', secs.get('F.channel'))}",
    ]
    out = []
    for key, m in MALLS.items():
        sc = store_codes.get(m["store"]) or []
        lines = [f"■ {m['label']} 出品依頼（NEW PRODUCT 案件 {pid}・{store.today_s()} 作成）", "", "【共通】"] + common
        lines += ["", f"【{m['label']} で要る項目】"]
        lines.append("・店の商品番号: " + ("、".join(sc) if sc else "CIP の紐付け表に未登録"))
        lines += [f"・{x}: {PENDING}" for x in m["extra"]]
        lines += ["・画像: " + PENDING + "（枚数・順番・使う写真）", "・検索キーワード: " + PENDING,
                  "", "※ 文字数の上限などは、登録の前に各モールの最新の規定で確かめてください。"
                  + (f"商品名（案）の文字数: " + "／".join(f"{len(n)}字" for n in names) if names else "")]
        out.append({"mall": key, "label": m["label"], "text": "\n".join(lines),
                    "store_codes": sc, "extra": m["extra"]})
    head = ("※ カルテで埋まっていない項目: " + "、".join(missing)) if missing else None
    if head:
        for o in out:
            o["text"] = o["text"].replace("【共通】", head + "\n\n【共通】", 1)
    return {"project_id": pid, "malls": out, "missing": missing}


def _store_codes(codes: list[str]) -> dict:
    """共通商品コード → 店ごとの店の商品番号（CIP の紐付け表）。読めなければ空（「未登録」と出る）。"""
    if not codes:
        return {}
    try:
        from app import seisan
        items = seisan.store_codes()["items"]
    except Exception:
        return {}
    out: dict = {}
    for it in items:
        if it.get("product_code") in codes and it.get("store_code"):
            out.setdefault(it.get("store"), [])
            if it["store_code"] not in out[it["store"]]:
                out[it["store"]].append(it["store_code"])
    return out
