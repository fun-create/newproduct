#!/usr/bin/env python3
"""
年間プランの枠（§4-3・F-3 ／ FR-82〜FR-86）。

**枠は「予定の器」で、案件ではない。**期首に「5月に推し活を1本」とだけ決め、
中身が決まってから案件へ変換する（FR-86）。案件表に空レコードを先に作ると、
カルテもゲートも空のまま件数だけが増え、ダッシュボードが嘘になる。

**挿入ルールは警告であって禁止ではない**（F-3-3 ／ FR-84）。
保存を止めると現場は表計算に戻る。止めずに、**例外の理由を残させる。**

**数えられないルールを「違反なし」と言わない**（N-10）。
長期連休の月は Calendar（calfc）の会社休業日が正本だが、まだ繋いでいない。
未設定のまま OK を返すと「検査した」ことになってしまうので `unavailable` を返す。

──────────────────────────────────────────────────────────
**工数ポイントと「時間(h)」を混同しないこと。**

ここで 10〜15 に収めるのは `flow_type.effort_point`（フローの重さの係数。
名入れ 5.5・フリーカット 6.0・ページリニューアル 2.5）の月合計で、
**標準タスクの `standard_hours` の合計ではない。**

時間側の月次負荷は `task.dashboard()` が別に出している（F-3-6・FR-45。
タスク実施月に積む）。そちらは `task_template.standard_hours` が
**予備時間を含まない**ため、予備時間の扱いが決まるまで実態より軽く出る。
**この2つを足さないこと。**単位が違う。
"""
from __future__ import annotations

import calendar
import datetime as _dt

from . import project as project_m
from . import store

STATES = ["策定中", "承認済", "失効"]
RULES = ["ratio", "effort", "count", "holiday"]

# ── 商品タイプ（F-3-8）────────────────────────────────
# code, label, seq, ratio_group, counts_as_launch, default_flow, note
PRODUCT_KINDS = [
    ("original", "オリジナルグッズ", 1, "original", 1, None,
     "3:1 の分子側として種を入れてある。**括りは商品開発部が決める。**"
     "出典（2026年度 年間プラン）の表記は「オリジナル（推し活）：うちわ」で、"
     "このアプリの評価テーマ「ライフイベント（オリジナルグッズ）／推し活（うちわ）」"
     "とは括弧の中が逆。`ratio_group` を付け替えれば比率の分母・分子が変わる"),
    ("uchiwa", "うちわ", 2, "uchiwa", 1, None,
     "3:1 の分母側。上の但し書きを参照"),
    ("pagerenew", "ページリニューアル", 3, None, 0, "pagerenew",
     "**発売本数に数えない**（F-10-11）。売上計上も既定で含めない（F-4-9）。"
     "計画の38%を占めるため、これを本数に混ぜると「月3商品」が達成に見える"),
    ("other", "その他", 9, None, 1, None,
     "比率の対象外。LOVOT・ぶっこみなど、3:1 のどちらにも入れない枠"),
]

SETTINGS = [
    ("plan.ratio_original_to_uchiwa", "挿入ルール: オリジナル：うちわ", "text", None,
     "出典は2026年度 年間プランの挿入ルール（3:1）。**括りの定義は `product_kind."
     "ratio_group` が正本。**ここは比の数だけを持つ"),
    ("plan.ratio_tolerance_slots", "比率の許容差", "number", "枠",
     "**枠1本ぶんまでは警告しない。**年間26枠で 3:1 は割り切れず、"
     "端数のたびに警告が出ると誰も読まなくなる"),
    ("plan.effort_min", "月間工数ポイントの下限", "number", "点", None),
    ("plan.effort_max", "月間工数ポイントの上限", "number", "点", None),
    ("plan.monthly_launch_slots", "月あたりの発売枠", "number", "本", None),
    ("plan.task_setup_lead_months", "タスク設定期限（発売の何か月前）", "number", "か月",
     "F-2-3 の逆算。枠を案件に変えたとき、この月数を発売予定日から引いた日を"
     "「タスクを組み終える期限」として返す"),
    ("plan.holiday_months", "長期連休のある月", "text", None,
     "**未設定です。**正本は Calendar（calfc）の会社休業日で、まだ連携していません。"
     "推測で 1・5・8月と置くと、置いたこと自体が根拠に見えてしまうため空にしてあります。"
     "決まるまで連休ルールは「未計測」と出します"),
    ("plan.holiday_month_max_slots", "連休月の枠の上限", "number", "本",
     "**未設定です。**「多くしない」としか決まっておらず、本数が決まっていません"),
    ("plan.fiscal_year_start_month", "年度の開始月", "number", "月",
     "**未設定です。**このアプリは年度の開始月を使わない作りにしてあります"
     "（枠のある月だけを並べる）。空欄のままで支障はありません"),
]

SETTING_DEFAULTS = {
    "plan.ratio_original_to_uchiwa": "3:1",
    "plan.ratio_tolerance_slots": "1",
    "plan.effort_min": "10",
    "plan.effort_max": "15",
    "plan.monthly_launch_slots": "3",
    "plan.task_setup_lead_months": "2",
}


def seed() -> dict:
    for code, label, seq, rg, cal, df, note in PRODUCT_KINDS:
        store.ex("INSERT INTO product_kind (code,label,seq,ratio_group,"
                 "counts_as_launch,default_flow,note) VALUES (?,?,?,?,?,?,?) "
                 "ON CONFLICT(code) DO UPDATE SET label=excluded.label,"
                 "seq=excluded.seq,counts_as_launch=excluded.counts_as_launch,"
                 "default_flow=excluded.default_flow,note=excluded.note",
                 (code, label, seq, rg, cal, df, note))
    # **ratio_group は更新しない。**商品開発部が付け替えたものを種で戻さない
    for key, label, kind, unit, why in SETTINGS:
        store.ex("INSERT INTO setting (key,value,label,kind,unit,why) "
                 "VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET label=excluded.label,"
                 "kind=excluded.kind,unit=excluded.unit,why=excluded.why",
                 (key, SETTING_DEFAULTS.get(key), label, kind, unit, why))
    return {"product_kind": len(PRODUCT_KINDS), "setting": len(SETTINGS)}


def _setting(key: str, default=None):
    r = store.one("SELECT value FROM setting WHERE key=?", (key,))
    if r is None or r["value"] in (None, ""):
        return default
    return r["value"]


def _num(key: str):
    v = _setting(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def kinds() -> list[dict]:
    return store.rows(store.q("SELECT * FROM product_kind ORDER BY seq"))


# ══════════════════════════════════════════════════════════
# 版（F-3-4 ／ FR-85）
# ══════════════════════════════════════════════════════════
def create_version(user_id: str, fiscal_year, label: str = "",
                   note: str = "", based_on: str | None = None) -> dict:
    try:
        fy = int(str(fiscal_year).strip())
    except (TypeError, ValueError):
        raise ValueError("年度を数字で入れてください（例 2026）")
    vid = store.new_id("plan_version")
    lab = (label or "").strip() or f"{fy}年度 年間プラン"
    with store.tx() as c:
        c.execute("INSERT INTO plan_version (id,fiscal_year,label,state,based_on,"
                  "note,created_at,created_by,updated_at,updated_by) "
                  "VALUES (?,?,?,'策定中',?,?,?,?,?,?)",
                  (vid, fy, lab, based_on, (note or "").strip() or None,
                   store.now_s(), user_id, store.now_s(), user_id))
    return {"id": vid, "fiscal_year": fy, "label": lab, "state": "策定中"}


def revise(version_id: str, user_id: str, label: str = "") -> dict:
    """承認済み版を種にして、改訂版（策定中）を作る。**枠ごと写す。**

    承認済みをそのまま編集させない。期中の改訂で、期首に何を約束したかが
    消えてしまう（F-3-4）。
    """
    src = store.one("SELECT * FROM plan_version WHERE id=?", (version_id,))
    if src is None:
        raise ValueError("その版がありません")
    new = create_version(user_id, src["fiscal_year"],
                         label or f"{src['label']}（改訂）", based_on=version_id)
    n = 0
    with store.tx() as c:
        for s in store.q("SELECT * FROM plan_slot WHERE version_id=? "
                         "ORDER BY launch_month, id", (version_id,)):
            sid = store.new_id("plan_slot")
            c.execute(
                "INSERT INTO plan_slot (id,version_id,launch_month,launch_date,"
                "product_kind,flow_type,area,effort_point,effort_src,owner,"
                "idea_id,occasion,note,created_at,created_by,updated_at,updated_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, new["id"], s["launch_month"], s["launch_date"],
                 s["product_kind"], s["flow_type"], s["area"], s["effort_point"],
                 s["effort_src"], s["owner"], s["idea_id"], s["occasion"], s["note"],
                 store.now_s(), user_id, store.now_s(), user_id))
            n += 1
    # **変換済み（project_id）は写さない。**案件は既に在るので、二重に作らせない
    new["slots_copied"] = n
    return new


def approve(version_id: str, user_id: str) -> dict:
    """承認する。**年度に承認済みは1つだけ**（FR-85）。前の承認済みは失効へ。"""
    v = store.one("SELECT * FROM plan_version WHERE id=?", (version_id,))
    if v is None:
        raise ValueError("その版がありません")
    if v["state"] == "承認済":
        return {"id": version_id, "state": "承認済", "changed": False}
    prev = store.one("SELECT id FROM plan_version WHERE fiscal_year=? AND "
                     "state='承認済'", (v["fiscal_year"],))
    with store.tx() as c:
        if prev is not None:
            c.execute("UPDATE plan_version SET state='失効',updated_at=?,"
                      "updated_by=? WHERE id=?",
                      (store.now_s(), user_id, prev["id"]))
        c.execute("UPDATE plan_version SET state='承認済',approved_by=?,"
                  "approved_at=?,updated_at=?,updated_by=? WHERE id=?",
                  (user_id, store.now_s(), store.now_s(), user_id, version_id))
    return {"id": version_id, "state": "承認済", "changed": True,
            "superseded": prev["id"] if prev else None}


def versions(fiscal_year=None) -> list[dict]:
    sql = ("SELECT v.*, (SELECT COUNT(*) FROM plan_slot s WHERE s.version_id=v.id) "
           "AS slot_n FROM plan_version v")
    p: list = []
    if fiscal_year not in (None, ""):
        sql += " WHERE v.fiscal_year=?"
        p.append(int(fiscal_year))
    sql += " ORDER BY v.fiscal_year DESC, (v.state='承認済') DESC, v.created_at DESC"
    return store.rows(store.q(sql, p))


def current(fiscal_year=None) -> dict | None:
    """その年度の「正」。承認済みが無ければ、いちばん新しい策定中を返す。"""
    vs = versions(fiscal_year)
    if not vs:
        return None
    for v in vs:
        if v["state"] == "承認済":
            return v
    return vs[0]


# ══════════════════════════════════════════════════════════
# 枠（F-3-1 ／ FR-82）
# ══════════════════════════════════════════════════════════
def _month_ok(s: str) -> str:
    s = (s or "").strip()
    try:
        _dt.date.fromisoformat(s + "-01")
    except ValueError:
        raise ValueError(f"発売月は YYYY-MM で入れてください（受け取った値: {s!r}）")
    return s


def create_slot(user_id: str, **f) -> dict:
    vid = (f.get("version_id") or "").strip()
    v = store.one("SELECT * FROM plan_version WHERE id=?", (vid,))
    if v is None:
        raise ValueError("版を選んでください")
    if v["state"] != "策定中":
        raise ValueError(f"{v['state']}の版は編集できません。改訂版を作ってください（F-3-4）")
    date = (f.get("launch_date") or "").strip() or None
    if date:
        _dt.date.fromisoformat(date)     # 形が違えば ValueError で弾く
        month = date[:7]
    else:
        month = _month_ok(f.get("launch_month", ""))
    kind = (f.get("product_kind") or "").strip() or None
    if kind and store.one("SELECT 1 FROM product_kind WHERE code=?", (kind,)) is None:
        raise ValueError(f"知らない商品タイプ {kind!r}")
    ft = (f.get("flow_type") or "").strip() or None
    if not ft and kind:
        ft = store.val("SELECT default_flow FROM product_kind WHERE code=?", (kind,))
    if ft and store.one("SELECT 1 FROM flow_type WHERE code=?", (ft,)) is None:
        raise ValueError(f"知らない開発タイプ {ft!r}")
    ep, src = f.get("effort_point"), "manual"
    if ep in (None, ""):
        # **フローの係数から取る。NULL なら NULL のまま**（⑤資材リニューアル）
        ep = store.val("SELECT effort_point FROM flow_type WHERE code=?", (ft,)) if ft else None
        src = "flow_type" if ep is not None else None
    sid = store.new_id("plan_slot")
    with store.tx() as c:
        c.execute(
            "INSERT INTO plan_slot (id,version_id,launch_month,launch_date,"
            "product_kind,flow_type,area,effort_point,effort_src,owner,idea_id,"
            "occasion,note,created_at,created_by,updated_at,updated_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, vid, month, date, kind, ft, (f.get("area") or "").strip() or None,
             float(ep) if ep not in (None, "") else None, src,
             (f.get("owner") or "").strip() or None,
             (f.get("idea_id") or "").strip() or None,
             (f.get("occasion") or "").strip() or None,
             (f.get("note") or "").strip() or None,
             store.now_s(), user_id, store.now_s(), user_id))
    return {"id": sid, "launch_month": month}


_EDITABLE = ("launch_date", "product_kind", "flow_type", "area", "effort_point",
             "owner", "idea_id", "occasion", "note")


def update_slot(slot_id: str, user_id: str, **f) -> dict:
    s = store.one("SELECT s.*, v.state FROM plan_slot s "
                  "JOIN plan_version v ON v.id=s.version_id WHERE s.id=?", (slot_id,))
    if s is None:
        raise ValueError("その枠がありません")
    if s["state"] != "策定中":
        raise ValueError(f"{s['state']}の版は編集できません。改訂版を作ってください（F-3-4）")
    if s["project_id"]:
        raise ValueError("案件に変換済みの枠です。案件の側で直してください")
    sets, p = [], []
    for k in _EDITABLE:
        if k not in f:
            continue
        v = f[k]
        v = None if (v is None or str(v).strip() == "") else str(v).strip()
        if k == "effort_point":
            sets.append("effort_src=?")
            p.append("manual" if v is not None else None)
            v = float(v) if v is not None else None
        sets.append(f"{k}=?")
        p.append(v)
    if "launch_date" in f and f["launch_date"]:
        _dt.date.fromisoformat(str(f["launch_date"]).strip())
        sets.append("launch_month=?")
        p.append(str(f["launch_date"]).strip()[:7])
    if not sets:
        return {"id": slot_id, "changed": 0}
    sets += ["updated_at=?", "updated_by=?"]
    p += [store.now_s(), user_id, slot_id]
    with store.tx() as c:
        c.execute(f"UPDATE plan_slot SET {','.join(sets)} WHERE id=?", p)
    return {"id": slot_id, "changed": len(sets) - 2}


def delete_slot(slot_id: str) -> dict:
    s = store.one("SELECT s.*, v.state FROM plan_slot s "
                  "JOIN plan_version v ON v.id=s.version_id WHERE s.id=?", (slot_id,))
    if s is None:
        raise ValueError("その枠がありません")
    if s["state"] != "策定中":
        raise ValueError(f"{s['state']}の版は編集できません")
    if s["project_id"]:
        raise ValueError("案件に変換済みの枠は消せません。変換の跡が消えるためです")
    with store.tx() as c:
        c.execute("DELETE FROM plan_slot WHERE id=?", (slot_id,))
    return {"id": slot_id, "deleted": True}


def slots(version_id: str) -> list[dict]:
    return store.rows(store.q(
        "SELECT s.*, k.label AS kind_label, k.ratio_group, k.counts_as_launch, "
        "f.label AS flow_label, i.title AS idea_title, p.stage AS project_stage "
        "FROM plan_slot s "
        "LEFT JOIN product_kind k ON k.code=s.product_kind "
        "LEFT JOIN flow_type f ON f.code=s.flow_type "
        "LEFT JOIN idea i ON i.id=s.idea_id "
        "LEFT JOIN project p ON p.id=s.project_id "
        "WHERE s.version_id=? ORDER BY s.launch_month, s.launch_date, s.id",
        (version_id,)))


# ══════════════════════════════════════════════════════════
# 挿入ルールの検査（F-3-2/3-3 ／ FR-83・FR-84）
#
# **返すのは判定だけ。保存は止めない。**level は4つ:
#   ok / warn / unavailable（数えられない） の3つに、
#   `acked`（例外として理由つきで承知済み）が付く。
#   **acked でも warn のまま出す。**消える作りにすると理由が書かれなくなる。
# ══════════════════════════════════════════════════════════
def _acks(version_id: str) -> dict:
    return {(r["rule"], r["scope"]): dict(r) for r in
            store.q("SELECT * FROM plan_exception WHERE version_id=?", (version_id,))}


def _r(rule, scope, level, message, **extra) -> dict:
    d = {"rule": rule, "scope": scope, "level": level, "message": message,
         "acked": None}
    d.update(extra)
    return d


def _rule_ratio(rows: list[dict]) -> list[dict]:
    spec = str(_setting("plan.ratio_original_to_uchiwa") or "")
    tol = _num("plan.ratio_tolerance_slots")
    parts = spec.split(":")
    if len(parts) != 2 or not all(x.strip().replace(".", "", 1).isdigit() for x in parts):
        return [_r("ratio", "FY", "unavailable",
                   f"比率の設定が読めません（{spec!r}）。「3:1」の形で入れてください")]
    a, b = float(parts[0]), float(parts[1])
    if a + b == 0:
        return [_r("ratio", "FY", "unavailable", "比率の設定が 0:0 です")]
    n_o = sum(1 for r in rows if r.get("ratio_group") == "original")
    n_u = sum(1 for r in rows if r.get("ratio_group") == "uchiwa")
    n_x = sum(1 for r in rows if not r.get("ratio_group"))
    tot = n_o + n_u
    if tot == 0:
        return [_r("ratio", "FY", "unavailable",
                   "比率の対象になる枠が1本もありません"
                   f"（対象外 {n_x} 本）。商品タイプを入れると数えられます",
                   original=0, uchiwa=0, excluded=n_x)]
    target_u = tot * b / (a + b)
    gap = abs(n_u - target_u)
    if tol is None:
        return [_r("ratio", "FY", "unavailable",
                   "許容差（plan.ratio_tolerance_slots）が未設定です",
                   original=n_o, uchiwa=n_u, excluded=n_x)]
    lvl = "ok" if gap <= tol else "warn"
    msg = (f"オリジナル {n_o} 本 : うちわ {n_u} 本"
           f"（目標 {spec} なら うちわ {target_u:.1f} 本）。"
           f"差 {gap:.1f} 本／許容 {tol:.0f} 本")
    if n_x:
        msg += f"。比率の対象外が {n_x} 本あります（数に入れていません）"
    return [_r("ratio", "FY", lvl, msg, original=n_o, uchiwa=n_u,
               excluded=n_x, target_uchiwa=round(target_u, 1), gap=round(gap, 1))]


def _counts_as_launch(r: dict) -> bool:
    """商品タイプ未設定（LEFT JOIN で NULL）は**数に入れる。**

    入れないと、タイプを選び忘れた枠が本数から静かに消え、
    「月3本」が足りているように見える。`0` が明示されたときだけ除く。
    """
    v = r.get("counts_as_launch")
    return True if v is None else bool(v)


def _by_month(rows: list[dict]) -> dict:
    d: dict[str, list[dict]] = {}
    for r in rows:
        d.setdefault(r["launch_month"], []).append(r)
    return dict(sorted(d.items()))


def _rule_effort(rows: list[dict]) -> list[dict]:
    lo, hi = _num("plan.effort_min"), _num("plan.effort_max")
    out = []
    for m, rs in _by_month(rows).items():
        known = [r for r in rs if r.get("effort_point") is not None]
        unknown = len(rs) - len(known)
        total = sum(float(r["effort_point"]) for r in known)
        if lo is None or hi is None:
            out.append(_r("effort", m, "unavailable",
                          "月間工数ポイントの上下限が未設定です",
                          total=round(total, 1), unknown=unknown))
            continue
        lvl = "ok" if lo <= total <= hi else "warn"
        msg = f"{total:.1f} 点（目安 {lo:.0f}〜{hi:.0f}）"
        if unknown:
            # **0 として足さない。**足すと「軽い月」に見える
            lvl = "warn" if lvl == "ok" else lvl
            msg += (f"。ただし工数ポイント未確定が {unknown} 本あり、"
                    "合計に入れていません。実際はこれより重くなります")
        out.append(_r("effort", m, lvl, msg, total=round(total, 1),
                      unknown=unknown, min=lo, max=hi))
    return out


def _rule_count(rows: list[dict]) -> list[dict]:
    want = _num("plan.monthly_launch_slots")
    out = []
    for m, rs in _by_month(rows).items():
        n = sum(1 for r in rs if _counts_as_launch(r))
        skipped = len(rs) - n
        if want is None:
            out.append(_r("count", m, "unavailable",
                          "月あたりの発売枠（plan.monthly_launch_slots）が未設定です",
                          n=n, skipped=skipped))
            continue
        lvl = "ok" if n == int(want) else "warn"
        msg = f"{n} 本（目標 {int(want)} 本）"
        if skipped:
            msg += (f"。ページリニューアル等 {skipped} 本は本数に数えていません"
                    "（F-10-11）")
        out.append(_r("count", m, lvl, msg, n=n, want=int(want), skipped=skipped))
    return out


def _rule_holiday(rows: list[dict]) -> list[dict]:
    months = str(_setting("plan.holiday_months") or "").strip()
    cap = _num("plan.holiday_month_max_slots")
    if not months or cap is None:
        return [_r("holiday", "FY", "unavailable",
                   "長期連休のある月が未設定です。正本は Calendar（calfc）の"
                   "会社休業日で、まだ連携していません。**推測で埋めていません**ので、"
                   "この月は人が見てください")]
    try:
        hm = {int(x) for x in months.replace("　", " ").replace(",", " ").split()}
    except ValueError:
        return [_r("holiday", "FY", "unavailable",
                   f"長期連休の月が読めません（{months!r}）。「1 5 8」の形で入れてください")]
    out = []
    for m, rs in _by_month(rows).items():
        if int(m[5:7]) not in hm:
            continue
        n = sum(1 for r in rs if _counts_as_launch(r))
        lvl = "ok" if n <= cap else "warn"
        out.append(_r("holiday", m, lvl,
                      f"長期連休のある月に {n} 本（上限 {cap:.0f} 本）", n=n, cap=cap))
    return out


def check(version_id: str) -> dict:
    """挿入ルールを機械で検査する（FR-83）。**保存は止めない**（FR-84）。"""
    rows = slots(version_id)
    res = (_rule_ratio(rows) + _rule_effort(rows)
           + _rule_count(rows) + _rule_holiday(rows))
    acks = _acks(version_id)
    for r in res:
        a = acks.get((r["rule"], r["scope"]))
        if a:
            r["acked"] = {"reason": a["reason"], "by": a["acked_by"],
                          "at": a["acked_at"]}
    n = {"ok": 0, "warn": 0, "unavailable": 0}
    for r in res:
        n[r["level"]] = n.get(r["level"], 0) + 1
    return {
        "results": res, "counts": n,
        "warn_unacked": sum(1 for r in res if r["level"] == "warn" and not r["acked"]),
        "note": "**警告は保存を止めません**（F-3-3）。例外は理由を付けて承知できますが、"
                "承知しても警告は消えません。消せる作りにすると理由が書かれなくなります。",
        "effort_unit_note":
            "月間10〜15 は枠の**工数ポイント**（フロー係数）の合計です。"
            "標準タスクの**時間(h)**ではありません。時間側の月次負荷はタスク画面に"
            "別に出しています（予備時間の扱いが未決のため、そちらは実態より軽く出ます）。",
    }


def ack(version_id: str, rule: str, scope: str, reason: str, user_id: str) -> dict:
    if rule not in RULES:
        raise ValueError(f"知らないルール {rule!r}")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("例外にする理由を書いてください（F-3-3）")
    if store.one("SELECT 1 FROM plan_version WHERE id=?", (version_id,)) is None:
        raise ValueError("その版がありません")
    with store.tx() as c:
        c.execute("INSERT INTO plan_exception (version_id,rule,scope,reason,"
                  "acked_by,acked_at) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT(version_id,rule,scope) DO UPDATE SET "
                  "reason=excluded.reason,acked_by=excluded.acked_by,"
                  "acked_at=excluded.acked_at",
                  (version_id, rule, scope, reason, user_id, store.now_s()))
    return {"ok": True, "rule": rule, "scope": scope}


# ══════════════════════════════════════════════════════════
# 枠 → 案件（F-3-5 ／ FR-86）
# ══════════════════════════════════════════════════════════
def _month_first(month: str) -> _dt.date:
    return _dt.date.fromisoformat(month + "-01")


def _minus_months(d: _dt.date, n: int) -> _dt.date:
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    return _dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def task_setup_due(slot: dict) -> dict:
    """タスク設定期限を割り戻す（FR-86）。

    **日が決まっていない枠は、その月の1日で逆算する。**月末で計算すると
    期限が最大30日うしろへずれる。早いほうに倒すのが安全側。
    """
    n = _num("plan.task_setup_lead_months")
    if n is None:
        return {"due": None, "days_left": None,
                "reason": "plan.task_setup_lead_months が未設定です"}
    base = (_dt.date.fromisoformat(slot["launch_date"]) if slot.get("launch_date")
            else _month_first(slot["launch_month"]))
    due = _minus_months(base, int(n))
    return {"due": due.isoformat(), "days_left": (due - store.today()).days,
            "lead_months": int(n), "based_on": slot.get("launch_date") or
            (slot["launch_month"] + "-01（月の1日で逆算。日が未定のため）")}


def convert(slot_id: str, user_id: str, **over) -> dict:
    """枠を案件へ。**1操作**（FR-86）。枠は消さず、変換の跡を残す。"""
    s = store.one("SELECT s.*, v.state, v.label AS version_label FROM plan_slot s "
                  "JOIN plan_version v ON v.id=s.version_id WHERE s.id=?", (slot_id,))
    if s is None:
        raise ValueError("その枠がありません")
    if s["project_id"]:
        raise ValueError(f"この枠は既に案件 {s['project_id']} になっています")
    s = dict(s)
    kind = store.one("SELECT * FROM product_kind WHERE code=?",
                     (s["product_kind"],)) if s["product_kind"] else None
    fields = {
        "flow_type": s["flow_type"],
        "area": s["area"],
        # **日が未定なら案件にも入れない。**仮の日付を置くと期限がそれで動き出す
        "launch_date": s["launch_date"],
        "occasion": s["occasion"],
        "idea_id": s["idea_id"],
        "owner": s["owner"],
        "effort_point": s["effort_point"],
        "stage": "年間プラン採択",
        "internal_name": (over.get("internal_name") or "").strip() or None,
        "summary": (over.get("summary") or "").strip() or None,
    }
    # ページリニューアルは既定で売上に含めない（F-4-9・F-10-11）。**黙って混ぜない**
    for k in ("revenue_counted", "revenue_basis", "cat1", "cat2", "cat3", "size", "code"):
        if over.get(k) not in (None, ""):
            fields[k] = over[k]
    r = project_m.create(user_id, **fields)
    due = task_setup_due(s)
    with store.tx() as c:
        c.execute("UPDATE plan_slot SET project_id=?,converted_at=?,converted_by=?,"
                  "updated_at=?,updated_by=? WHERE id=?",
                  (r["id"], store.now_s(), user_id, store.now_s(), user_id, slot_id))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,"
                  "what,detail) VALUES (?,?,?,?,?)",
                  (r["id"], store.now_s(), user_id, "年間プランの枠から変換",
                   f"{s['version_label']} / {s['launch_month']} / 枠 {slot_id}"
                   f" ／ タスク設定期限 {due.get('due') or '未算出'}"))
    msg = ("案件を作りました。"
           + (f"**タスクを組み終える期限は {due['due']}**"
              f"（発売の{due.get('lead_months')}か月前・残り {due['days_left']} 日）。"
              if due.get("due") else f"タスク設定期限は出せません（{due.get('reason')}）。"))
    if kind is not None and not kind["counts_as_launch"]:
        msg += f"　この枠（{kind['label']}）は**発売本数に数えません**（F-10-11）。"
    if not r.get("template_defined"):
        msg += "　この開発タイプは標準タスクが未定義のため、タスク一覧は空です。"
    return {"slot_id": slot_id, "project_id": r["id"],
            "tasks_created": r["tasks_created"],
            "template_defined": r["template_defined"],
            "task_setup_due": due, "message": msg}


def slot_consumption() -> dict:
    """今月の枠の消化（FR-63）。**承認済み版の、今月の枠のうち案件化した数。**

    **策定中の版では数えない。**期首に約束したもの（＝承認済み）に対する消化が
    問いであって、下書きを分母にすると、枠を足すたびに達成率が下がる。

    **「枠が0本」と「版が無い」を区別する**（N-10）。
    版が無いのは未計測、0本は「今月は枠を置いていない」という事実。
    """
    base = {"label": "今月の枠の消化", "link": "#/plan",
            "definition": "承認済み版の今月の枠のうち、開発案件に変換した数"}
    month = store.today().isoformat()[:7]
    v = store.one("SELECT id, label, fiscal_year FROM plan_version "
                  "WHERE state='承認済' ORDER BY fiscal_year DESC LIMIT 1")
    if v is None:
        n_draft = store.val("SELECT COUNT(*) FROM plan_version "
                            "WHERE state='策定中'", (), 0)
        return {**base, "value": None, "state": "未計測", "month": month,
                "why": "**承認済みの年間プランがまだありません。**"
                       + (f"策定中の版が {n_draft} 件あります。承認すると数え始めます。"
                          if n_draft else "プラン画面で版を作り、承認すると数え始めます。")}
    rows = store.q("SELECT s.project_id, k.counts_as_launch FROM plan_slot s "
                   "LEFT JOIN product_kind k ON k.code=s.product_kind "
                   "WHERE s.version_id=? AND s.launch_month=?", (v["id"], month))
    n = len(rows)
    done = sum(1 for r in rows if r["project_id"])
    if n == 0:
        return {**base, "value": None, "state": "枠なし", "month": month,
                "version": v["label"],
                # **0件と未計測を混ぜない。**「置いていない」は事実であって欠測ではない
                "why": f"{v['label']} に {month} の枠が1本もありません。"
                       "未計測ではなく、**この月に枠を置いていない**という意味です。"}
    return {**base, "value": f"{done} / {n} 本", "month": month,
            "version": v["label"], "slots": n, "converted": done,
            "launch_slots": sum(1 for r in rows if _counts_as_launch(dict(r))),
            # **色に意味を持たせない**（N-11）。状態は語で出す
            "state": "消化済" if done >= n else "未消化あり",
            "why": (None if done >= n else
                    f"{n - done} 本がまだ開発案件になっていません。"
                    "プラン画面の「案件にする」で変換すると、"
                    "タスクを組み終える期限（発売の2か月前）が出ます。")}


# ══════════════════════════════════════════════════════════
# 画面が使う形
# ══════════════════════════════════════════════════════════
def detail(version_id: str) -> dict | None:
    v = store.one("SELECT * FROM plan_version WHERE id=?", (version_id,))
    if v is None:
        return None
    rows = slots(version_id)
    months: dict[str, dict] = {}
    for m, rs in _by_month(rows).items():
        known = [r for r in rs if r["effort_point"] is not None]
        months[m] = {
            "month": m, "slots": rs, "n": len(rs),
            "launch_n": sum(1 for r in rs if _counts_as_launch(r)),
            "effort": round(sum(float(r["effort_point"]) for r in known), 1),
            "effort_unknown": len(rs) - len(known),
            "converted_n": sum(1 for r in rs if r["project_id"]),
        }
    return {"version": dict(v), "months": list(months.values()),
            "slot_n": len(rows), "rules": check(version_id),
            "editable": v["state"] == "策定中"}


def overview(fiscal_year=None) -> dict:
    vs = versions(fiscal_year)
    cur = current(fiscal_year)
    return {
        "versions": vs,
        "current": detail(cur["id"]) if cur else None,
        "kinds": kinds(),
        "empty_note": ("年間プランの版がまだありません。"
                       "年度を選んで「版を作る」から始めます。"
                       "**計画が0行の状態から立ち上がる設計**です（D-3・FR-134）。")
        if not vs else None,
    }
