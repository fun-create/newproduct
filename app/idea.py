#!/usr/bin/env python3
"""
アイデア台帳と採点（§4-2・F-1）。

**この部品が直しているのは4つ**（業務要件 第3部 Q1）。

1. **採点を2層にする**（F-1-5）。共通点 0〜70（購買意欲・ニーズ／ターゲット規模／
   競合優位性／**想定粗利額**）＋ テーマ適合点 0〜30。
   共通点だけで全テーマを横並び比較できるようにする。
   現行はテーマ適合が総合点に溶け込んでいるので、横並びにできない。
2. **生産方法(1-5) を減点係数として総合点に掛ける**（F-1-6）。
   現行は回帰係数±0.01以内＝総合点に非寄与で、**作れない案が上位に来る。**
3. **高単価化しやすさを円建てにする**（F-1-7）。現行はウェイト×0.5・寄与6%/2%で、
   利益に一番近い項目が実質無視されている。
4. **ランクは絶対点ではなく百分位**（F-1-9）。オリジナルとうちわで閾値も分布も
   違うのに同じ SS/S/A/B/C を使っている。

**そして、既存データは再採点しない**（F-1-10・第8章 ⑦）。
v1（シートのままの点）と v2（新しい軸）を `rubric.version` で分けて併存させる。
**同じ画面に並べて見せるが、足し算はしない。**

通年性は点数をやめて3値（通年／季節／単発）にし、**枠取りの属性**として持つ
（F-1-8。419件中365件が8点固定で、点数としては機能していなかった）。
"""
from __future__ import annotations

import json
import re
import unicodedata

from . import store

# ── 語彙 ───────────────────────────────────────────────
# F-1-4。**起票経路は必須の新設項目。**ただし移行分は不明なので NULL を許す
ORIGINS = [
    ("internal", "社内アイデア"),
    ("fctr", "FCTR"),
    ("review", "レビュー・顧客の声"),
    ("production", "生産からの要望"),
    ("btob", "法人要望"),
    ("bukkomi", "ぶっこみ"),
    ("newmodel", "新機種調査"),
]
ORIGIN_CODES = [c for c, _ in ORIGINS]
ORIGIN_LABEL = dict(ORIGINS)

# F-1-13。**列で持つ。色で表さない**（N-11）
STAGES = ["起票", "採点済", "候補", "案件化", "保留", "見送り"]

# F-1-8。通年性の代わり。**点数ではない。**年間プランの配置に効かせる属性
DEMAND_CYCLES = ["通年", "季節", "単発"]

RANKS = ["SS", "S", "A", "B", "C"]

# ── テーマ（§4-2）────────────────────────────────────
# **評価テーマだけを入れる。**機会カレンダー（年間イベント・ライフイベントの暦）は
# F-2 の範囲で、まだ取り込んでいない（migrations/006_theme.sql の注記）。
THEMES = [
    ("lifeevent", "ライフイベント（オリジナルグッズ）", 1,
     "オリジナルグッズ評価用シートのテーマ。v1 の相性軸は「ライフイベントとの相性」"),
    ("oshikatsu", "推し活（うちわ）", 2,
     "推し活うちわ評価用シートのテーマ。v1 の相性軸は「推し活との相性」"),
    ("lovot", "LOVOT", 3,
     "LOVOT専用シートのテーマ。v1 は7項目・140点満点の独自系"),
    ("bukkomi", "ぶっこみ（テーマ未定）", 4,
     "ぶっこみ評価用シート。参入条件だけが明文化されており、実データは2件"),
]

# ── v2 の係数表 ────────────────────────────────────────
# **生産方法(1-5) → 減点係数**（F-1-6 ／ 業務要件 Q1-B の提案値）。
FEASIBILITY_FACTORS = {5: 1.0, 4: 0.95, 3: 0.85, 2: 0.7, 1: 0.4}

# **想定粗利額（円・1個あたり）→ 1〜10点**（F-1-7）。
# **2026-09-23 十文字さんの決定で、この境界のまま採点を始める。**
# 原価の正本（seisan の material_cost）は第3段で入る。入ったら見直すが、
# **そのときは新しい版として足す**（`rubric.margin_bands` に版ごとに持たせてある）。
# 881件を古い点のまま置いておくほうが困る、というのが決定の理由。
MARGIN_BANDS = [
    (4000, 10), (3000, 9), (2000, 8), (1600, 7), (1200, 6),
    (800, 5), (600, 4), (400, 3), (200, 2), (0, 1),
]
MARGIN_BANDS_NOTE = (
    "**この刻みで採点を始めます**（2026-09-23 十文字さんの決定）。"
    "1個あたりの想定粗利額を1〜10点に写すための境界で、"
    "根拠となる原価の正本（seisan の材料原価）は**第3段で入ります**。"
    "入った時点で刻みを見直しますが、そのときは**新しい版（v3）として足します。**"
    "いまの点は書き換えません（F-1-10。版は合算しない）。"
)

# F-1-9・業務要件 Q1-C。**テーマ内の上位比率**でランクを決める
PERCENTILE_THRESHOLDS = [("SS", 0.01), ("S", 0.05), ("A", 0.15), ("B", 0.35)]

SIMILAR_METHOD = "title-bigram-dice/1"
SIMILAR_MIN = 0.5
SIMILAR_TOP = 3

# ── rubric の版（F-1-10）──────────────────────────────
# v1 は**実測で復元したウェイト**（総合点列から逆算・最大誤差0.5点）。
# 移行したスコアがこのウェイトで再現できることを tests で検査する。
V1_AXES_SHEET = [
    ("demand",      "購買意欲・ニーズ",  "common", 1, 10),
    ("market_size", "ターゲット規模",    "common", 1, 10),
    ("advantage",   "競合優位性",        "common", 1, 10),
    ("premium",     "高単価化しやすさ",  "common", 1, 10),
    ("year_round",  "通年性",            "common", 1, 10),
    ("theme_fit",   None,                "theme",  1, 10),
]

V1_LOVOT_AXES = [
    ("lovot_love",       "①うちの子愛（感情価値）", 3.0),
    ("offkai",           "②オフ会映え（社交価値）", 2.5),
    ("lovot_rule",       "③LOVOT規定適合性",        2.0),
    ("repeat",           "④リピート性（季節・消耗）", 2.0),
    ("bundle",           "⑤セット売りしやすさ",     1.5),
    ("manufacturability", "⑥FUN-CREATE製造適性",    1.5),
    ("price_freedom",    "⑦価格設定の自由度",       1.5),
]

V1_BUKKOMI_AXES = [
    ("demand",      "購買意欲・ニーズ"),
    ("market_size", "ターゲット規模"),
    ("effort",      "生産工数"),
]

V2_VERSION = "v2"

V2_AXES = [
    ("demand",      "購買意欲・ニーズ", "common", 2.5, 1, 10,
     "寄与シェア31%/27%。v1 でも一番効いていた軸なので残す"),
    ("market_size", "ターゲット規模",   "common", 2.0, 1, 10, None),
    ("advantage",   "競合優位性",       "common", 1.0, 1, 10, None),
    ("margin",      "想定粗利額（円→点）", "common", 1.5, 1, 10,
     "F-1-7。円で入れた想定粗利額を margin_bands で点に写す。"
     "v1 の「高単価化しやすさ×0.5（寄与6%/2%）」の置き換え"),
    ("theme_fit",   "テーマ適合",       "theme",  3.0, 1, 10,
     "F-1-5 ②。ライフイベント相性／推し活相性／LOVOT相性はここに収まる"),
    ("feasibility", "生産方法(1-5)",    "factor", None, 1, 5,
     "F-1-6。**点ではなく減点係数。**総合点に掛ける"),
    ("demand_cycle", "需要発生（通年／季節／単発）", "attribute", None, None, None,
     "F-1-8。**点にしない。**年間プランの枠取りに使う属性"),
]


def _v1_rubric(version, label, theme_id, weights, thresholds, note):
    """v1 の版を1つ組み立てる。**ウェイトも閾値もシートごとに別**。"""
    axes = []
    for i, (code, lab, layer, lo, hi) in enumerate(V1_AXES_SHEET):
        if code == "theme_fit":
            lab = ("ライフイベントとの相性" if theme_id == "lifeevent"
                   else "推し活との相性")
        axes.append((code, lab, layer, weights[code], lo, hi, None))
    return {
        "version": version, "label": label, "generation": 1,
        "theme_id": theme_id, "common_max": None, "theme_max": None,
        "total_max": 100.0, "rank_method": "absolute",
        "thresholds": thresholds, "feasibility_factors": None,
        "margin_bands": None, "note": note, "axes": axes,
    }


RUBRICS = [
    _v1_rubric(
        "v1-original", "v1 オリジナルグッズ（ライフイベント）", "lifeevent",
        {"demand": 3.5, "market_size": 2.5, "advantage": 1.5,
         "premium": 0.5, "year_round": 1.0, "theme_fit": 1.0},
        {"SS": 81, "S": 75, "A": 70, "B": 65,
         "_note": "実データの実測レンジ SS 81〜82 ／ S 75〜80 ／ A 70〜74 ／ "
                  "B 65〜70 ／ C 33〜65。**A/B と B/C の境界が重複している**"
                  "（65点・70点の行が両方のランクに存在）。"
                  "移行では**シートに書かれたランクをそのまま**入れている"},
        "総合点列から逆算したウェイト（最大誤差0.5点）。"
        "**デザイン自由度・生産方法は総合点に入っていない**（回帰係数±0.01以内）"),
    _v1_rubric(
        "v1-uchiwa", "v1 推し活うちわ", "oshikatsu",
        {"demand": 2.5, "market_size": 1.5, "advantage": 1.5,
         "premium": 0.5, "year_round": 1.0, "theme_fit": 3.0},
        {"SS": 82, "S": 78, "A": 70, "B": 66,
         "_note": "実データの実測レンジ SS 82〜90 ／ S 78〜79 ／ A 70〜73 ／ "
                  "B 66 ／ C 26〜56。**C の最高56 と B の66 の間に10点の空白**が"
                  "あり、連続分布でない（バッチ一括採点の痕跡）"},
        "オリジナルとウェイトが違う（購買意欲 ×2.5・規模 ×1.5・相性 ×3.0）のに、"
        "**同じ SS/S/A/B/C の記号を使っている。**これが横並び比較できない原因"),
    {
        "version": "v1-lovot", "label": "v1 LOVOT専用", "generation": 1,
        "theme_id": "lovot", "common_max": None, "theme_max": None,
        "total_max": 140.0, "rank_method": "absolute",
        "thresholds": {"SS": 110, "S": 95, "A": 80, "B": 65,
                       "_note": "評価基準シートに明記（SS 110〜140 ／ S 95〜109 ／ "
                                "A 80〜94 ／ B 65〜79 ／ C 〜64）。"
                                "**総合点の計算式セルは #ERROR! のまま壊れている**が、"
                                "7項目のウェイトで42行すべて誤差0で再現できた"},
        "feasibility_factors": None, "margin_bands": None,
        "note": "3つ目の採点系。7項目・140点満点。"
                "「原価率30%以下」「粗利」という視点が他2シートには無い形で入っている",
        "axes": [(c, l, "theme", w, 1, 10, None) for c, l, w in V1_LOVOT_AXES],
    },
    {
        "version": "v1-bukkomi", "label": "v1 ぶっこみ", "generation": 1,
        "theme_id": "bukkomi", "common_max": None, "theme_max": None,
        "total_max": None, "rank_method": "absolute",
        "thresholds": {"_note": "**閾値は実測できない。**実データ2件がどちらも未採点"},
        "feasibility_factors": None, "margin_bands": None,
        "note": "唯一明文化された参入条件を持つシート（①誰かの熱量が高い時 "
                "②機会が明確な時 ③最低限の評価基準）。"
                "**ウェイトは NULL。**採点済みの行が1件も無く、実測できない",
        "axes": [(c, l, "common", None, 1, 10,
                  "ウェイト未実測（採点済みの行が無い）") for c, l in V1_BUKKOMI_AXES],
    },
    {
        "version": V2_VERSION, "label": "v2 2層採点（共通点70＋テーマ適合30）",
        "generation": 2, "theme_id": None,
        "common_max": 70.0, "theme_max": 30.0, "total_max": 100.0,
        "rank_method": "percentile",
        "thresholds": {k: v for k, v in PERCENTILE_THRESHOLDS},
        "feasibility_factors": {str(k): v for k, v in FEASIBILITY_FACTORS.items()},
        "margin_bands": {"bands": [[lo, pt] for lo, pt in MARGIN_BANDS],
                         "note": MARGIN_BANDS_NOTE},
        "note": "**v1 と合算しない**（F-1-10・第8章 ⑦）。既存の移行分は再採点しない。"
                "総合点 = (共通点 + テーマ適合点) × 生産方法の減点係数。"
                "ランクはテーマ内の百分位（F-1-9）",
        "axes": V2_AXES,
    },
]

RUBRIC_VERSIONS = [r["version"] for r in RUBRICS]


# ══════════════════════════════════════════════════════════
# 種まき。**何度流しても同じ結果**（起動のたびに流す）
# ══════════════════════════════════════════════════════════
def seed_themes() -> int:
    for code, label, sort, note in THEMES:
        store.ex("INSERT INTO theme (id,label,kind,sort,note) "
                 "VALUES (?,?,'評価テーマ',?,?) "
                 "ON CONFLICT(id) DO UPDATE SET label=excluded.label,"
                 "kind=excluded.kind,sort=excluded.sort,note=excluded.note",
                 (code, label, sort, note))
    return len(THEMES)


def seed_rubrics() -> int:
    for r in RUBRICS:
        store.ex(
            "INSERT INTO rubric (version,label,generation,theme_id,common_max,"
            "theme_max,total_max,rank_method,thresholds,feasibility_factors,"
            "margin_bands,active,note,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?) "
            "ON CONFLICT(version) DO UPDATE SET label=excluded.label,"
            "generation=excluded.generation,theme_id=excluded.theme_id,"
            "common_max=excluded.common_max,theme_max=excluded.theme_max,"
            "total_max=excluded.total_max,rank_method=excluded.rank_method,"
            "thresholds=excluded.thresholds,"
            "feasibility_factors=excluded.feasibility_factors,"
            "margin_bands=excluded.margin_bands,note=excluded.note",
            (r["version"], r["label"], r["generation"], r["theme_id"],
             r["common_max"], r["theme_max"], r["total_max"], r["rank_method"],
             json.dumps(r["thresholds"], ensure_ascii=False),
             json.dumps(r["feasibility_factors"], ensure_ascii=False)
             if r["feasibility_factors"] else None,
             json.dumps(r["margin_bands"], ensure_ascii=False)
             if r["margin_bands"] else None,
             r["note"], store.now_s()))
        for i, ax in enumerate(r["axes"]):
            code, label, layer, weight, lo, hi, note = ax
            store.ex(
                "INSERT INTO rubric_axis (rubric_version,code,label,layer,weight,"
                "scale_min,scale_max,seq,note) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(rubric_version,code) DO UPDATE SET label=excluded.label,"
                "layer=excluded.layer,weight=excluded.weight,"
                "scale_min=excluded.scale_min,scale_max=excluded.scale_max,"
                "seq=excluded.seq,note=excluded.note",
                (r["version"], code, label, layer, weight, lo, hi, i, note))
    return len(RUBRICS)


# **未設定を 0 で埋めない**（N-10）。value は NULL のまま置き、理由を持たせる
SETTINGS = [
    ("monthly_launch_target", "月間の発売目標本数", "number", "本",
     "**未確定です。**年間36本か52本かが未決で、F-14（販売計画シミュレーション）"
     "で決める設計になっています。決まるまで「コンセプト在庫月数」は計算しません"),
    ("concept_stock_floor", "コンセプト在庫月数の下限", "number", "か月", None),
    ("ai_scoring_enabled", "AI採点を使う", "bool", None,
     "**既定は off。**`newproduct-*` のAI予算枠（AutoGrowth の ai_budget.json）が"
     "未取得のため（全体設計書 第11章 ⑩）"),
]

SETTING_DEFAULTS = {"concept_stock_floor": "6", "ai_scoring_enabled": "0"}


def seed_settings() -> int:
    for key, label, kind, unit, why in SETTINGS:
        store.ex("INSERT INTO setting (key,value,label,kind,unit,why) "
                 "VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET label=excluded.label,"
                 "kind=excluded.kind,unit=excluded.unit,why=excluded.why",
                 (key, SETTING_DEFAULTS.get(key), label, kind, unit, why))
    return len(SETTINGS)


def setting(key: str, default=None):
    r = store.one("SELECT value FROM setting WHERE key=?", (key,))
    if r is None or r["value"] in (None, ""):
        return default
    return r["value"]


def settings() -> list[dict]:
    return store.rows(store.q("SELECT * FROM setting ORDER BY key"))


# ══════════════════════════════════════════════════════════
# 類似案（F-1-12）
#
# **完全な名寄せは要らない。**「似た案が3件あります」を出すだけ。
# **外部APIを使わない。**正規化＋文字2-gram の Dice 係数で、
# 同じ入力なら必ず同じ結果になる形にする（tests で決定性を検査する）。
# ══════════════════════════════════════════════════════════
_DROP = re.compile(r"[\s　・､、。,.\-_／/（）()\[\]【】「」『』＋+&＆:：;；!！?？\"'*]+")


def normalize(s: str) -> str:
    """**決定的に**正規化する。NFKC → 小文字 → 区切り文字を落とす。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.casefold()
    return _DROP.sub("", s)


def bigrams(s: str) -> set[str]:
    n = normalize(s)
    if len(n) <= 1:
        return {n} if n else set()
    return {n[i:i + 2] for i in range(len(n) - 1)}


def dice(a: str, b: str) -> float:
    """Dice 係数。**同じ入力なら同じ値。**乱数も辞書順依存も持たせない。"""
    x, y = bigrams(a), bigrams(b)
    if not x or not y:
        return 1.0 if normalize(a) == normalize(b) and normalize(a) else 0.0
    if normalize(a) == normalize(b):
        return 1.0
    return 2 * len(x & y) / (len(x) + len(y))


def similar_to_title(title: str, exclude_id: str | None = None,
                     top: int = SIMILAR_TOP, minimum: float = SIMILAR_MIN
                     ) -> list[dict]:
    """似た案を上位 `top` 件。**並びは (点数降順, id昇順) で決定的。**"""
    if not normalize(title):
        return []
    out = []
    for r in store.q("SELECT id,title,stage,theme_id FROM idea"):
        if exclude_id and r["id"] == exclude_id:
            continue
        s = dice(title, r["title"])
        if s >= minimum:
            out.append({"id": r["id"], "title": r["title"], "stage": r["stage"],
                        "theme_id": r["theme_id"], "score": round(s, 4)})
    out.sort(key=lambda x: (-x["score"], x["id"]))
    return out[:top]


def cache_similar(idea_id: str, rows: list[dict]):
    store.ex("DELETE FROM idea_similar WHERE idea_id=?", (idea_id,))
    for r in rows:
        store.ex("INSERT OR REPLACE INTO idea_similar "
                 "(idea_id,other_id,score,method,computed_at) VALUES (?,?,?,?,?)",
                 (idea_id, r["id"], r["score"], SIMILAR_METHOD, store.now_s()))
    store.conn().commit()


def similar_of(idea_id: str) -> list[dict]:
    """キャッシュがあればそれ、無ければ計算してキャッシュする。"""
    rs = store.q("SELECT s.other_id AS id, s.score, i.title, i.stage, i.theme_id "
                 "FROM idea_similar s JOIN idea i ON i.id=s.other_id "
                 "WHERE s.idea_id=? ORDER BY s.score DESC, s.other_id", (idea_id,))
    if rs:
        return store.rows(rs)
    r = store.one("SELECT title FROM idea WHERE id=?", (idea_id,))
    if r is None:
        return []
    rows = similar_to_title(r["title"], exclude_id=idea_id)
    cache_similar(idea_id, rows)
    return rows


# ══════════════════════════════════════════════════════════
# 書く
# ══════════════════════════════════════════════════════════
def _int_or_none(v, lo=None, hi=None, what=""):
    if v in (None, "", "—"):
        return None
    try:
        n = int(str(v).replace(",", "").strip())
    except ValueError:
        raise ValueError(f"{what}は数で入れてください（{v!r}）")
    if lo is not None and n < lo or hi is not None and n > hi:
        raise ValueError(f"{what}は {lo}〜{hi} で入れてください（{n}）")
    return n


def create(user_id: str, **f) -> dict:
    """起票（F-1-3）。**要るのは4つだけ。**

    商品案名・概要・想定ターゲット・**起票経路**。
    デザイン自由度も生産方法も参考URLもエリアも、ここでは訊かない。
    750件＋755件のストックはこの軽さが生んでいる。
    """
    title = (f.get("title") or "").strip()
    if not title:
        raise ValueError("商品案名を入れてください")
    origin = (f.get("origin") or "").strip() or None
    if origin is not None and origin not in ORIGIN_CODES:
        raise ValueError(f"知らない起票経路 {origin!r}")
    if origin is None:
        raise ValueError("起票経路を選んでください（F-1-4。あとから区別できなくなります）")
    theme_id = (f.get("theme_id") or "").strip() or None
    if theme_id and store.one("SELECT 1 FROM theme WHERE id=?", (theme_id,)) is None:
        raise ValueError(f"知らないテーマ {theme_id!r}")
    iid = store.new_id("idea")
    with store.tx() as c:
        c.execute(
            "INSERT INTO idea (id,title,summary,target_scene,origin,theme_id,"
            "stage,created_at,created_by,updated_at,updated_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (iid, title, (f.get("summary") or "").strip() or None,
             (f.get("target_scene") or "").strip() or None, origin, theme_id,
             "起票", store.now_s(), user_id, store.now_s(), user_id))
    rows = similar_to_title(title, exclude_id=iid)
    cache_similar(iid, rows)
    return {"id": iid, "similar": rows}


EDITABLE = {
    "summary": (str, None), "target_scene": (str, None), "note": (str, None),
    "ref_url1": (str, None), "ref_url2": (str, None),
    "area1": (str, None), "area2": (str, None),
    "design_freedom": (int, (1, 5)),
    "production_feasibility": (int, (1, 5)),
    "expected_margin_yen": (int, (0, 10_000_000)),
}


def update_fields(idea_id: str, user_id: str, **f) -> dict:
    """採点・案件化のときに埋まる項目。**起票フォームには出さない。**"""
    if store.one("SELECT 1 FROM idea WHERE id=?", (idea_id,)) is None:
        raise LookupError("アイデアがありません")
    sets, params = [], []
    for k, (kind, rng) in EDITABLE.items():
        if k not in f:
            continue
        v = f.get(k)
        if kind is int:
            v = _int_or_none(v, rng[0], rng[1], k)
        else:
            v = (v or "").strip() or None
        sets.append(f"{k}=?"); params.append(v)
    if "demand_cycle" in f:
        v = (f.get("demand_cycle") or "").strip() or None
        if v is not None and v not in DEMAND_CYCLES:
            raise ValueError(f"需要発生は {'／'.join(DEMAND_CYCLES)} から選んでください")
        sets.append("demand_cycle=?"); params.append(v)
    if "theme_id" in f:
        v = (f.get("theme_id") or "").strip() or None
        if v and store.one("SELECT 1 FROM theme WHERE id=?", (v,)) is None:
            raise ValueError(f"知らないテーマ {v!r}")
        sets.append("theme_id=?"); params.append(v)
    if "stage" in f:
        v = (f.get("stage") or "").strip()
        if v not in STAGES:
            raise ValueError(f"知らないステージ {v!r}")
        sets.append("stage=?"); params.append(v)
    if "origin" in f:
        v = (f.get("origin") or "").strip() or None
        if v is not None and v not in ORIGIN_CODES:
            raise ValueError(f"知らない起票経路 {v!r}")
        sets.append("origin=?"); params.append(v)
        # 移行分に人が起票経路を入れたら、「移行時不明」の断り書きは外す
        sets.append("origin_note=?"); params.append(None if v else "移行時不明")
    if not sets:
        return {"updated": 0}
    sets += ["updated_at=?", "updated_by=?"]
    params += [store.now_s(), user_id, idea_id]
    with store.tx() as c:
        c.execute("UPDATE idea SET " + ",".join(sets) + " WHERE id=?", params)
    return {"updated": 1}


# ── 採点 v2 ────────────────────────────────────────────
def margin_points(yen: int | None) -> int | None:
    """円 → 1〜10点（F-1-7）。**未入力は None。0 で埋めない。**"""
    if yen is None:
        return None
    for lo, pt in MARGIN_BANDS:
        if yen >= lo:
            return pt
    return 1


def feasibility_factor(level: int | None) -> float | None:
    """生産方法(1-5) → 減点係数（F-1-6）。**未入力は None。**"""
    if level is None:
        return None
    return FEASIBILITY_FACTORS.get(int(level))


def score_v2(idea_id: str, axes: dict, user_id: str,
             scored_by: str = "human", model: str | None = None,
             source_note: str | None = None) -> dict:
    """v2 で採点する。**足りない入力を推測で埋めない。**

    総合点 = (共通点 0〜70 ＋ テーマ適合点 0〜30) × 生産方法の減点係数
    """
    r = store.one("SELECT * FROM idea WHERE id=?", (idea_id,))
    if r is None:
        raise LookupError("アイデアがありません")
    idea = dict(r)
    if not idea.get("theme_id"):
        raise ValueError("テーマを選んでください（テーマ適合点はテーマごとに定義が変わります）")

    got = {}
    for code in ("demand", "market_size", "advantage", "theme_fit"):
        v = axes.get(code)
        n = _int_or_none(v, 1, 10, code)
        if n is None:
            raise ValueError(f"{code} が未入力です。**0 では埋めません**（N-10）")
        got[code] = n

    mp = margin_points(idea.get("expected_margin_yen"))
    if mp is None:
        raise ValueError(
            "想定粗利額（円）が未入力です。v1 の「高単価化しやすさ」は"
            "円建てに置き換えました（F-1-7）。先にアイデアの項目へ入れてください")
    got["margin"] = mp

    fac = feasibility_factor(idea.get("production_feasibility"))
    if fac is None:
        raise ValueError(
            "生産方法(1-5) が未入力です。v2 では**総合点に掛ける減点係数**なので、"
            "入っていないと総合点が出せません（F-1-6）。"
            "1.0 で通すと「作れない案」が上位に来ます")

    common = (2.5 * got["demand"] + 2.0 * got["market_size"]
              + 1.0 * got["advantage"] + 1.5 * got["margin"])
    theme_fit = 3.0 * got["theme_fit"]
    raw = common + theme_fit
    total = round(raw * fac, 2)

    store.ex(
        "INSERT INTO idea_score (idea_id,rubric_version,axes,common_score,"
        "theme_fit,feasibility_factor,raw_total,total,rank_basis,scored_by,"
        "model,scored_at,source_note) "
        "VALUES (?,?,?,?,?,?,?,?, 'percentile', ?,?,?,?) "
        "ON CONFLICT(idea_id,rubric_version) DO UPDATE SET axes=excluded.axes,"
        "common_score=excluded.common_score,theme_fit=excluded.theme_fit,"
        "feasibility_factor=excluded.feasibility_factor,"
        "raw_total=excluded.raw_total,total=excluded.total,"
        "scored_by=excluded.scored_by,model=excluded.model,"
        "scored_at=excluded.scored_at,source_note=excluded.source_note",
        (idea_id, V2_VERSION, json.dumps(got, ensure_ascii=False),
         round(common, 2), round(theme_fit, 2), fac, round(raw, 2), total,
         scored_by, model, store.now_s(), source_note))
    if idea["stage"] == "起票":
        store.ex("UPDATE idea SET stage='採点済',updated_at=?,updated_by=? "
                 "WHERE id=?", (store.now_s(), user_id, idea_id))
    store.conn().commit()
    recompute_ranks(V2_VERSION)
    return score_of(idea_id, V2_VERSION)


# ── ランク（F-1-9）──────────────────────────────────────
def _rank_from_percentile(p: float) -> str:
    for name, limit in PERCENTILE_THRESHOLDS:
        if p < limit:
            return name
    return "C"


def _percentiles(values: list[float]) -> dict[int, float]:
    """**同点は同じ百分位。**「自分より厳密に高い件数 ÷ 母数」。

    首位は 0.0。同点が並んだら全員が同じ値になる（並び順に依存しない）。
    """
    n = len(values)
    out = {}
    srt = sorted(values, reverse=True)
    for i, v in enumerate(values):
        greater = 0
        for w in srt:
            if w > v:
                greater += 1
            else:
                break
        out[i] = greater / n if n else 0.0
    return out


def recompute_ranks(version: str = V2_VERSION) -> int:
    """百分位とランクを引き直す（F-1-9）。

    **母数はテーマ内。**テーマ適合点の定義がテーマごとに違うので、
    テーマをまたいで総合点を並べても揃わない（それが v1 の失敗）。
    共通点だけは**テーマをまたいで**並べる（`common_percentile`）。
    それが2層にした理由そのもの（F-1-5）。
    """
    rs = store.q(
        "SELECT s.id, s.idea_id, s.total, s.common_score, i.theme_id "
        "FROM idea_score s JOIN idea i ON i.id=s.idea_id "
        "WHERE s.rubric_version=?", (version,))
    rows = store.rows(rs)
    if not rows:
        return 0
    by_theme: dict[str, list[dict]] = {}
    for r in rows:
        by_theme.setdefault(r["theme_id"] or "—", []).append(r)
    pct = {}
    for _, group in by_theme.items():
        vals = [r["total"] or 0.0 for r in group]
        p = _percentiles(vals)
        for i, r in enumerate(group):
            pct[r["id"]] = p[i]
    commons = [r["common_score"] or 0.0 for r in rows]
    cp = _percentiles(commons)
    for i, r in enumerate(rows):
        store.ex("UPDATE idea_score SET percentile=?,common_percentile=?,rank=?,"
                 "rank_basis='percentile' WHERE id=?",
                 (round(pct[r["id"]], 4), round(cp[i], 4),
                  _rank_from_percentile(pct[r["id"]]), r["id"]))
    store.conn().commit()
    return len(rows)


# ══════════════════════════════════════════════════════════
# 読む
# ══════════════════════════════════════════════════════════
def rubrics() -> list[dict]:
    out = []
    for r in store.q("SELECT * FROM rubric ORDER BY generation, version"):
        d = dict(r)
        for k in ("thresholds", "feasibility_factors", "margin_bands"):
            d[k] = json.loads(d[k]) if d[k] else None
        d["axes"] = store.rows(store.q(
            "SELECT * FROM rubric_axis WHERE rubric_version=? ORDER BY seq",
            (d["version"],)))
        out.append(d)
    return out


def score_of(idea_id: str, version: str) -> dict | None:
    r = store.one("SELECT * FROM idea_score WHERE idea_id=? AND rubric_version=?",
                  (idea_id, version))
    if r is None:
        return None
    d = dict(r)
    d["axes"] = json.loads(d["axes"]) if d["axes"] else {}
    return d


def scores_of(idea_id: str) -> list[dict]:
    """**版ごとに1件ずつ並べて返す。混ぜない。**"""
    out = []
    for r in store.q("SELECT s.*, r.label AS rubric_label, r.generation, "
                     "r.total_max, r.rank_method FROM idea_score s "
                     "JOIN rubric r ON r.version=s.rubric_version "
                     "WHERE s.idea_id=? ORDER BY r.generation, s.rubric_version",
                     (idea_id,)):
        d = dict(r)
        d["axes"] = json.loads(d["axes"]) if d["axes"] else {}
        out.append(d)
    return out


def _idea_filters(args: dict):
    where, params = ["1=1"], []
    if args.get("stage"):
        where.append("i.stage=?"); params.append(args["stage"])
    if args.get("theme"):
        where.append("i.theme_id=?"); params.append(args["theme"])
    if args.get("origin") == "_unknown":
        where.append("i.origin IS NULL")
    elif args.get("origin"):
        where.append("i.origin=?"); params.append(args["origin"])
    if args.get("rubric"):
        where.append("EXISTS (SELECT 1 FROM idea_score s WHERE s.idea_id=i.id "
                     "AND s.rubric_version=?)")
        params.append(args["rubric"])
    if args.get("rank"):
        v = args.get("rubric")
        if v:
            where.append("EXISTS (SELECT 1 FROM idea_score s WHERE s.idea_id=i.id "
                         "AND s.rubric_version=? AND s.rank=?)")
            params += [v, args["rank"]]
        else:
            where.append("EXISTS (SELECT 1 FROM idea_score s WHERE s.idea_id=i.id "
                         "AND s.rank=?)")
            params.append(args["rank"])
    if args.get("q"):
        where.append("i.title LIKE ?"); params.append("%" + args["q"] + "%")
    return " AND ".join(where), params


LIMIT_DEFAULT = 200


def listing(args: dict | None = None) -> dict:
    """アイデア一覧。**rubric版を跨いで1つの順位表にしない**（F-1-10）。

    絞り込みで版を選ぶと、その版の点とランクだけを列に出す。
    版を選ばないときは「採点の版」列に**持っている版の名前を並べる**。
    """
    args = args or {}
    w, params = _idea_filters(args)
    version = args.get("rubric") or None
    limit = min(int(args.get("limit") or LIMIT_DEFAULT), 1000)
    total_n = store.val("SELECT COUNT(*) FROM idea i WHERE " + w, params, 0)

    order = ("ORDER BY (SELECT s.total FROM idea_score s WHERE s.idea_id=i.id "
             "AND s.rubric_version=?) IS NULL, "
             "(SELECT s.total FROM idea_score s WHERE s.idea_id=i.id "
             "AND s.rubric_version=?) DESC, i.id"
             if version else "ORDER BY i.created_at DESC, i.id")
    p2 = ([version, version] if version else []) + [limit]
    rs = store.q("SELECT i.*, t.label AS theme_label FROM idea i "
                 "LEFT JOIN theme t ON t.id=i.theme_id WHERE " + w + " " + order +
                 " LIMIT ?", params + p2)
    rows = []
    for r in rs:
        d = dict(r)
        sc = score_of(d["id"], version) if version else None
        versions = [x["rubric_version"] for x in store.q(
            "SELECT rubric_version FROM idea_score WHERE idea_id=? "
            "ORDER BY rubric_version", (d["id"],))]
        rows.append({
            "id": d["id"], "title": d["title"], "stage": d["stage"],
            "theme_id": d["theme_id"], "theme_label": d["theme_label"] or "—",
            # **移行分は「不明」。空欄にも 0 にもしない**（N-10）
            "origin": d["origin"],
            "origin_label": (ORIGIN_LABEL.get(d["origin"]) if d["origin"]
                             else (d["origin_note"] or "不明")),
            "demand_cycle": d["demand_cycle"],
            "production_feasibility": d["production_feasibility"],
            "expected_margin_yen": d["expected_margin_yen"],
            "source_sheet": d["source_sheet"],
            "score_versions": versions,
            "score": ({"total": sc["total"], "rank": sc["rank"],
                       "percentile": sc["percentile"],
                       "common_score": sc["common_score"],
                       "theme_fit": sc["theme_fit"],
                       "feasibility_factor": sc["feasibility_factor"]}
                      if sc else None),
        })
    return {
        "rows": rows, "total": total_n, "shown": len(rows), "limit": limit,
        "rubric": version,
        "filters": {
            "stage": STAGES,
            "rank": RANKS,
            "rubric": [{"version": r["version"], "label": r["label"],
                        "generation": r["generation"]} for r in
                       store.q("SELECT version,label,generation FROM rubric "
                               "ORDER BY generation, version")],
            "origin": ([{"code": c, "label": lab} for c, lab in ORIGINS]
                       + [{"code": "_unknown", "label": "不明（移行分）"}]),
            "theme": store.rows(store.q(
                "SELECT id AS code, label FROM theme WHERE kind='評価テーマ' "
                "ORDER BY sort")),
        },
        "notes": [
            "**採点の版を混ぜて1つの順位表にしていません**（F-1-10）。"
            "版を選ぶと、その版の点とランクだけを出します。",
            "移行した 1件 1件の `起票経路` は**不明**です。"
            "元のシートに起票経路の列がありません。推測では埋めていません（N-10）。",
        ],
    }


def detail(idea_id: str) -> dict | None:
    r = store.one("SELECT i.*, t.label AS theme_label FROM idea i "
                  "LEFT JOIN theme t ON t.id=i.theme_id WHERE i.id=?", (idea_id,))
    if r is None:
        return None
    d = dict(r)
    d["origin_label"] = (ORIGIN_LABEL.get(d["origin"]) if d["origin"]
                         else (d["origin_note"] or "不明"))
    d["scores"] = scores_of(idea_id)
    d["similar"] = similar_of(idea_id)
    d["margin_points"] = margin_points(d["expected_margin_yen"])
    d["feasibility_factor"] = feasibility_factor(d["production_feasibility"])
    # v2 の母数（テーマ内）。**少ない母数の百分位は動きやすいので画面に出す**
    d["v2_population"] = store.val(
        "SELECT COUNT(*) FROM idea_score s JOIN idea x ON x.id=s.idea_id "
        "WHERE s.rubric_version=? AND x.theme_id IS ?",
        (V2_VERSION, d["theme_id"]), 0)
    d["v2_ready"], d["v2_blockers"] = _v2_ready(d)
    d["projects"] = store.rows(store.q(
        "SELECT id, stage, launch_date FROM project WHERE idea_id=? "
        "ORDER BY created_at", (idea_id,)))
    return d


def _v2_ready(d: dict) -> tuple[bool, list[str]]:
    """**何が足りなくて v2 で採点できないかを先に言う。**"""
    miss = []
    if not d.get("theme_id"):
        miss.append("テーマ（テーマ適合点の定義がテーマごとに変わります）")
    if d.get("expected_margin_yen") is None:
        miss.append("想定粗利額（円）— v1 の「高単価化しやすさ」の置き換え（F-1-7）")
    if d.get("production_feasibility") is None:
        miss.append("生産方法(1-5) — 総合点に掛ける減点係数（F-1-6）")
    return (not miss), miss


# ── コンセプト在庫月数（F-1-14）───────────────────────────
def concept_stock() -> dict:
    """**ゲートG3（コンセプト承認）を通過し、まだ発売していない件数 ÷ 月間目標本数。**

    月間目標本数が未確定なら **「未計測」のまま理由を出す**。
    0 で割らない。**仮の目標で埋めない**（N-10）。
    """
    rows = store.q(
        "SELECT p.id FROM project p WHERE p.stage NOT IN "
        "('発売済','追跡中','評価完了','中止') AND "
        "(SELECT g.result FROM gate_review g WHERE g.project_id=p.id "
        " AND g.gate='G3' ORDER BY g.id DESC LIMIT 1) = '通過'")
    n = len(rows)
    target = setting("monthly_launch_target")
    floor = setting("concept_stock_floor", "6")
    base = {
        "label": "コンセプト在庫月数",
        "stock_n": n,
        "monthly_target": None if target is None else float(target),
        "floor": float(floor) if floor else None,
        "definition": "ゲートG3（コンセプト承認）通過・未発売の件数 ÷ 月間発売目標本数",
    }
    if target is None or float(target) <= 0:
        return {**base, "value": None, "state": "未計測",
                "why": "**月間の発売目標本数が未確定です。**年間36本か52本かが"
                       "決まっておらず、F-14（販売計画シミュレーション）で決める"
                       "設計になっています。設定に入れると計算します。"
                       f"（いまのG3通過・未発売は {n} 件）"}
    v = round(n / float(target), 1)
    short = base["floor"] is not None and v < base["floor"]
    return {**base, "value": v,
            # **色に意味を持たせない**（N-11）。状態は語で出す
            "state": "不足" if short else "充足",
            "why": (f"下限 {base['floor']} か月を下回っています。"
                    if short else None)}


def counts() -> dict:
    """`/api/health` に出す件数。**0 と未取込を区別できるように実測で出す。**"""
    out = {t: store.val(f"SELECT COUNT(*) FROM {t}", (), 0)
           for t in ("idea", "rubric", "rubric_axis", "idea_score", "theme",
                     "theme_score", "theme_signal", "idea_similar", "setting")}
    out["idea_by_sheet"] = {
        (r["source_sheet"] or "アプリで起票"): r["n"] for r in store.q(
            "SELECT source_sheet, COUNT(*) AS n FROM idea "
            "GROUP BY source_sheet ORDER BY source_sheet")}
    out["idea_without_origin"] = store.val(
        "SELECT COUNT(*) FROM idea WHERE origin IS NULL", (), 0)
    out["idea_score_by_version"] = {
        r["rubric_version"]: r["n"] for r in store.q(
            "SELECT rubric_version, COUNT(*) AS n FROM idea_score "
            "GROUP BY rubric_version ORDER BY rubric_version")}
    return out
