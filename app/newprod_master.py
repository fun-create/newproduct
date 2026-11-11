#!/usr/bin/env python3
"""
週次レポートの新商品マスタ（キーワード方式）へ、発売した商品の1行を足す（2026-11-10 依頼書 4-4・
2026-11-11 十文字さんの選択・ADR-139）。

**つなぎ。**本来は CIP の共通商品コードで数える（十文字さん 11-11）。CIP にコードで売上を返す口ができたら、
キーワード方式はやめる（HUB tasks/to-cip.md 11-11）。それまでの手作業（Windows の Claude Code に頼んで1行足す）を無くす。

流れ:
1. 発売済みにした案件は「未紐付け」に出る。商品開発部が NEW PRODUCT で 商品ラベル・含む／除外キーワード・月の目標 を決め
   「週次レポートに載せる」を押す（`request`）→ 書き込み待ち
2. アシロボの PC の `sync_newprod_master.ps1`（月曜 8:30・weekly_report の 9:00 の前）が `rows_for_feed` を受け取り、
   sales.csv で**当たる明細の数**と**同じ明細に当たる既存の行**を数え、重なりが無ければ控えを取ってマスタに1行足す
   （同じ商品ラベルが既にあれば足さない）。結果を返す（`apply_results`）。**商品名そのものは送らない**（数とラベルだけ）
3. 追加済みになったら、案件を週次レポートの商品ラベルと結び（登録済み）、計上設定のタスクを完了にする。
   その後も毎週、当たった数を返してもらう（発売から3週たって0件なら「キーワードを確かめて」）

決めないこと: キーワードは**推測で確定しない**（案は出すが、押すのは人）。重なりがあれば足さない（二重計上を防ぐ）。
発売から1年で「有効」を外すのは自動にしない（十文字さんの選択 11-11。人が外す）。
"""
from __future__ import annotations

import datetime as dt
import json
import re

from app import store

WAIT, ADDED, CHECK, FAIL = "書き込み待ち", "追加済み", "要確認", "失敗"
EDITORS = {"devdept", "admin", "president"}
ZERO_AFTER_DAYS = 21


def _clean_kw(s: str, label: str, required: bool) -> str:
    s = (s or "").strip()
    if required and not s:
        raise ValueError(f"{label}を入れてください（sales.csv の商品名に含まれる文言。; で区切ると全部を含むもの）")
    if len(s) > 200 or re.search(r"[\t\r\n]", s):
        raise ValueError(f"{label}は1行・200字までで入れてください")
    for part in [p.strip() for p in s.split(";") if p.strip()]:
        if ("(" in part or ")" in part) and not re.fullmatch(r"\([^()|]+(\|[^()|]+)+\)", part):
            raise ValueError(f"{label}の「{part}」: どれかを含む、は (A|B) の形で書きます")
    return ";".join(p.strip() for p in s.split(";") if p.strip())


def _num(v, label):
    v = str(v or "").replace(",", "").strip()
    if not v:
        return None
    try:
        x = float(v)
    except ValueError:
        raise ValueError(f"{label}は数字で入れてください") from None
    if x < 0:
        raise ValueError(f"{label}は0以上で入れてください")
    return x


def propose(p: dict) -> dict:
    """案。**確定しない。**社内呼称の語を ; でつなぎ、月の目標は年間目標の12分の1。"""
    from app import target
    name = re.sub(r"※.*$", "", p.get("internal_name") or "").strip()
    words = [w for w in re.split(r"[\s　【】（）()［］\[\]・/／]+", name) if len(w) >= 2]
    t = target.get(p["id"]) or {}
    yen = t.get("annual_yen")
    return {"label": name or p["id"], "include": ";".join(words[:3]), "exclude": "",
            "month_sales": round(yen / 12) if yen else None, "month_qty": None,
            "note": "案です。sales.csv の商品名の書き方に合わせて直してください（週次レポートの「新商品」シートの既存の行も参考に）"}


def view(pid: str) -> dict:
    g = store.one("SELECT * FROM newprod_reg WHERE project_id=?", (pid,))
    g = dict(g) if g else {}
    return {k: g.get("m_" + k) for k in ("label", "include", "exclude", "month_sales", "month_qty", "state", "note",
                                         "matches", "requested_by", "requested_at", "checked_at")} | {
        "overlaps": json.loads(g["m_overlaps"]) if g.get("m_overlaps") else []}


def request(pid: str, f: dict, user_id: str) -> dict:
    from app import gate
    if not set(gate.roles_of(user_id)) & EDITORS:
        raise PermissionError("週次レポートに載せる操作は、商品開発部・管理者・社長の業務ロールの人です")
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    g = store.one("SELECT * FROM newprod_reg WHERE project_id=?", (pid,))
    if g is not None and g["state"] == "登録済み":
        raise ValueError("もう週次レポートの商品ラベルと結んであります")
    if g is not None and g["m_state"] == ADDED:
        raise ValueError("マスタに追加済みです。直すときは新商品マスタを直接直してください")
    label = (f.get("label") or "").strip()
    if not label or len(label) > 80 or re.search(r"[\t\r\n]", label):
        raise ValueError("商品ラベルを1行・80字までで入れてください（週次レポートに出る名前）")
    if store.one("SELECT 1 FROM newprod_reg WHERE m_label=? AND project_id!=?", (label, pid)):
        raise ValueError(f"商品ラベル「{label}」は別の案件で使っています")
    inc = _clean_kw(f.get("include"), "含むキーワード", True)
    exc = _clean_kw(f.get("exclude"), "除外キーワード", False)
    ms, mq = _num(f.get("month_sales"), "月間目標売上"), _num(f.get("month_qty"), "月間目標数量")
    now = store.now_s()
    with store.tx() as c:
        c.execute("INSERT OR IGNORE INTO newprod_reg (project_id,state,created_at) VALUES (?,?,?)", (pid, "登録待ち", now))
        c.execute("UPDATE newprod_reg SET m_label=?, m_include=?, m_exclude=?, m_month_sales=?, m_month_qty=?, "
                  "m_state=?, m_note=NULL, m_overlaps=NULL, m_requested_by=?, m_requested_at=? WHERE project_id=?",
                  (label, inc, exc or None, ms, mq, WAIT, user_id, now, pid))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                  (pid, now, user_id, "週次レポートに載せる",
                   f"商品ラベル「{label}」・含む「{inc}」・除外「{exc or 'なし'}」。次の月曜 8:30 に新商品マスタへ"))
    store.audit(user_id, "newprod_master.request", pid, {"label": label, "include": inc, "exclude": exc})
    return {"ok": True, "state": WAIT}


def rows_for_feed(today: dt.date | None = None) -> dict:
    """スクリプトへ渡すもの。add＝足す行、check＝追加済みで当たりを数え直す行（発売から1年以内）"""
    from app import newprod
    today = today or store.today()
    since = newprod.window_since(today)
    add, check = [], []
    for r in store.q("SELECT g.*, p.launch_date FROM newprod_reg g JOIN project p ON p.id=g.project_id "
                     "WHERE g.m_state IS NOT NULL"):
        row = {"project_id": r["project_id"], "label": r["m_label"], "include": r["m_include"],
               "exclude": r["m_exclude"] or "", "release_date": r["launch_date"]}
        if r["m_state"] == WAIT:
            ms, mq = r["m_month_sales"] or 0, r["m_month_qty"] or 0
            add.append({**row, "monthly_sales": [ms] * 12, "monthly_qty": [mq] * 12})
        elif r["m_state"] == ADDED and (r["launch_date"] or "") > since:
            check.append(row)
    return {"add": add, "check": check}


def apply_results(items: list, source: str = "feed:windows") -> dict:
    """スクリプトの結果。result＝追加済み／要確認／失敗／当たり（追加済みの行の数え直し）"""
    from app import launchlink, newprod
    done = 0
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("project_id") or "")
        g = store.one("SELECT g.*, p.launch_date FROM newprod_reg g JOIN project p ON p.id=g.project_id "
                      "WHERE g.project_id=?", (pid,))
        if g is None or not g["m_state"]:
            continue
        res = str(it.get("result") or "")
        try:
            m = int(it.get("matches")) if it.get("matches") not in (None, "") else None
        except (TypeError, ValueError):
            m = None
        ov = it.get("overlaps") or []
        ov = [ov] if isinstance(ov, str) else ov                 # PowerShell は1件の配列を文字列にすることがある
        ov = [str(x)[:80] for x in ov if str(x).strip()][:20]
        note = str(it.get("note") or "")[:300] or None
        now = store.now_s()
        if res == "当たり" and g["m_state"] == ADDED:
            age = (store.today() - dt.date.fromisoformat(g["launch_date"])).days if g["launch_date"] else 0
            if m == 0 and age >= ZERO_AFTER_DAYS:
                note = f"発売から{age}日たっても sales.csv で0件です。キーワードが商品名の書き方と合っているか確かめてください"
            with store.tx() as c:
                c.execute("UPDATE newprod_reg SET m_matches=?, m_overlaps=?, m_note=?, m_checked_at=? WHERE project_id=?",
                          (m, json.dumps(ov, ensure_ascii=False) if ov else None, note, now, pid))
            done += 1
            continue
        if g["m_state"] != WAIT or res not in (ADDED, CHECK, FAIL):
            continue                                   # 書き込み待ちのものだけ（二重に結ばない）
        with store.tx() as c:
            c.execute("UPDATE newprod_reg SET m_state=?, m_matches=?, m_overlaps=?, m_note=?, m_checked_at=? "
                      "WHERE project_id=?", (res, m, json.dumps(ov, ensure_ascii=False) if ov else None, note, now, pid))
            if res == ADDED:
                c.execute("UPDATE newprod_reg SET state=?, labels=?, registered_at=?, registered_by=? WHERE project_id=?",
                          (newprod.DONE, json.dumps([g["m_label"]], ensure_ascii=False), now, source, pid))
                c.execute("UPDATE task SET status='完了', done_at=? WHERE project_id=? AND title=? "
                          "AND status NOT IN ('完了','対象外')", (now, pid, newprod.TASK_TITLE))
                c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what,detail) VALUES (?,?,?,?,?)",
                          (pid, now, source, "新商品マスタに追加", f"商品ラベル「{g['m_label']}」（sales.csv で {m if m is not None else '—'} 件）"))
        launchlink._save(pid, "weekly", {ADDED: "済", CHECK: "要確認", FAIL: "失敗"}[res],
                         {ADDED: f"週次レポートの新商品マスタに「{g['m_label']}」を足しました",
                          CHECK: f"新商品マスタに足していません: {note or '確認が要ります'}",
                          FAIL: f"新商品マスタに足せませんでした: {note or '理由不明'}"}[res],
                         {"items": [], "matches": m, "overlaps": ov}, source)
        done += 1
    return {"ok": True, "applied": done}
