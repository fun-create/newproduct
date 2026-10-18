#!/usr/bin/env python3
"""
新機種調査（FR-140・F-9-5・ADR-102・2026-10-18 十文字さんの選択「NEW PRODUCT が AI にウェブ調査させる」）。

元の運用: 毎月、Google シート「スキンシール本体モデル展開管理」の「手順」にある長文を ChatGPT に投げ、
10シートの展開の表（本体モデル × スキンシール展開）へ手で転記していた。

ここでやること:
- **展開の表の正本をこのアプリに移す**（`device_model`。取り込みは tools/import_device_models.py・画面で直せる）
- 毎月（`tools/monthly_report.py` から）と画面のボタンで、AI にウェブを調べさせる。指示文は「手順」の文をそのまま使い、
  **展開の表にある機種を「既知」として渡し、新しい機種だけを候補にさせる**
- 候補は `device_candidate` に入り、人が 案件にする（④ニューモデル追加の案件を作る）／展開済み（表に足す）／見送り を押す
- 検索語・開いた URL は `ai_web`（kind=device）に全部残す。出典が AI の見たものでない行は「見ていない」と出す（ADR-088）

守ること: AI は候補を出すだけ（決めるのは人）。発売日は AI が書いたままで「確かめるのは人」。数字を作らない。
"""
from __future__ import annotations

import csv
import json
import re
import threading
from pathlib import Path

from . import ai_budget
from . import ai_cli
from . import ai_score
from . import ai_web
from . import store

BASE = Path(__file__).resolve().parent.parent
SEED_DIR = BASE / "seed"
SHEET_PREFIX = "09_スキンシール本体モデル展開管理__"
RUN_KIND = "web_device"
EDITORS = ("devdept", "admin", "president")
STATES = ("提案", "案件にした", "展開済み", "見送り")
JP = ("発売済み", "発売予定", "日本未発売", "不明")
CONF = ("公式", "報道", "噂")

# 「手順」の対象シリーズ12（順もそのまま）。sheet は展開の表のシート名（12 → 10 シート）
SERIES = [
    ("iqos", "アイコス", "アイコス", "IQOS / ILUMA / ILUMA i / ONE / 限定モデルを含む"),
    ("ploom", "プルームテック", "プルーム", "Ploom ブランド全体（TECH / X / AURA / CUBE など JT の加熱式たばこデバイス全体）"),
    ("lil", "リルハイブリッド", "アイコス", "lil HYBRID（KT&G・日本では IQOS の販路）"),
    ("glo", "グロー", "グロー", "glo ブランド全体（hyper / pro など）"),
    ("airpods", "エアポッズ", "エアポッズ", "AirPods / Pro / Max"),
    ("playstation", "プレイステーション", "プレステ", "PS5 / PS5 Digital / Pro / 特定地域向け / 限定本体"),
    ("switch", "ニンテンドースイッチ", "スイッチ", "Switch / Switch OLED / Switch 2 / 同梱版"),
    ("iphone", "アイフォン", "アイフォン", "iPhone / e / Plus / Pro / Pro Max / 地域限定仕様"),
    ("ipad", "アイパッド", "アイパッド", "iPad / mini / Air / Pro"),
    ("macbook", "マックブック", "マックブック", "MacBook Air / Pro"),
    ("kingblade", "キングブレード", "ペンライト", "KING BLADE（ルイファン）"),
    ("mixpenla", "ミックスペンラ", "ペンライト", "MIX PENLa（ターンオン）"),
]

# 12シリーズを1回で調べるので、ほかのウェブ調査より量を多く渡す（2026-10-18 十文字さんの選択「1回のままで上限を上げる」）。
# 初回は検索4回・ページ6件で上限に届き、プレステと Ploom の公式ページを開けなかった（$0.36）
MAX_SEARCH, MAX_OPEN, MAX_USD = 8, 12, 1.2
SYSTEM = (ai_web.SYSTEM.replace("新商品開発の調査補助です。", "新商品開発の調査補助です（スキンシールの本体モデル展開の調査）。")
          .replace("検索は4回まで、開くページは6つまで。", f"検索は{MAX_SEARCH}回まで、開くページは{MAX_OPEN}まで。"
                   "メーカーの公式（ニュース・プレスリリース）を先に開く。"))

ASK = """以下の対象シリーズについて、「回答時点を基準として直近3か月以内」に
① 新たに発売された機器があるかどうか
② 発売されている場合は、その機種名・発売日・概要
を必ず漏れなく整理してください。

【調査・整理ルール（最重要）】
- 日本国内での発売を基準とすること（日本未発売の場合は必ずその旨を明記）
- 限定版、カラーバリエーション違い、仕様変更、同梱版、名称違い、リネーム品、後継機、派生モデルをすべて含めること
- 各シリーズごとに「該当なし」の場合も必ず明記すること
- 公式発表・公式サイト・プレスリリース・信頼できるニュース情報を横断的に確認し、網羅性を最優先すること
- 情報が不完全なものは「公式／報道／噂」の信頼度を必ず明記すること

【ブランド・シリーズ解釈の強制ルール】
- 対象シリーズ名が「ブランド名」または「旧称・代表名」の場合、そのブランド配下に存在するすべての派生シリーズ・後継モデル・別称モデルを必ず包含対象とすること
- シリーズ名と製品名が異なる場合でも、メーカー公式により同一ブランド・同一製品系譜に属するものは必ず拾うこと

【該当なし判定の条件】
- 「該当なし」と記載する場合は、旧名称・後継名称・派生シリーズ・別ブランド統合名をすべて調査したうえで該当が無いことを前提とする

【将来情報】
- あわせて、今後半年以内に 発売予定 / 発表済み（発売日未定）とされている機種があれば、発売予定時期と情報の確実性（公式／報道／噂）を必ず整理すること

【このアプリの決まり】
- 「既知の機種」に挙げた機種（表記ゆれ・カラー違いを含む）は候補に入れない。新しい機種だけを rows に入れる
- 各行の url は、検索結果に出たか、実際に開いたページのものだけ
- 答えの形: {"rows":[{"series":"シリーズのコード","model":"機種名","release_date":"YYYY-MM-DD または YYYY-MM または 不明",
  "jp_released":"発売済み|発売予定|日本未発売|不明","confidence":"公式|報道|噂","summary":"概要（1〜2文）","url":"出典"}],
  "none":["該当なしだったシリーズのコード"],"unverified":["確かめきれなかったこと"]}"""


def can_edit(user_id: str) -> bool:
    from . import gate
    return bool(set(gate.roles_of(user_id)) & set(EDITORS))


def ensure_series() -> None:
    with store.tx() as c:
        for i, (code, label, sheet, hint) in enumerate(SERIES):
            c.execute("INSERT INTO device_series (code,label,sheet,hint,sort) VALUES (?,?,?,?,?) "
                      "ON CONFLICT(code) DO UPDATE SET label=excluded.label, sheet=excluded.sheet, hint=excluded.hint, sort=excluded.sort",
                      (code, label, sheet, hint, i))


# ── 展開の表の取り込み（元シートの列は揃っていない）──────────────────────────
_URL = re.compile(r"(https?://\S+|[a-z0-9.-]+\.(?:jp|com|net|shop)\b\S*)", re.I)
# 元シートに公式ショップのログイン情報が書かれたセルがある（2026-10-18 取り込みで発見）。**取り込まない・写さない。**
_SECRET = re.compile(r"(パスワード|password|ログイン情報|^ID[／/]|^PW[／/])", re.I)
_DATE = re.compile(r"(20\d\d\s*年|20\d\d[-/.]\d{1,2}|\d{4}年式)")


def _parse_sheet(path: Path) -> list[dict]:
    rows = list(csv.reader(path.open(encoding="utf-8", newline=""), delimiter="\t"))
    if not rows:
        return []
    head = [h.strip() for h in rows[0]]
    idx = {h: i for i, h in enumerate(head) if h}
    out = []
    for n, r in enumerate(rows[1:], start=1):
        cells = [c.strip() for c in r]
        if not any(cells):
            continue

        def col(name):
            i = idx.get(name)
            return cells[i] if i is not None and i < len(cells) else ""
        model = col("本体モデル") or (cells[0] if "本体モデル" not in idx else "")
        if not model:
            continue
        rec = {"model": model, "model_no": col("モデル番号") or None, "deploy": col("スキンシール展開") or None,
               "release": col("発売日") or None, "ended": col("掲載終了") or None, "url": col("出典URL") or None,
               "note": None, "source_row": n}
        extras = []
        used = {idx.get(k) for k in ("本体モデル", "モデル番号", "スキンシール展開", "発売日", "掲載終了", "出典URL") if k in idx}
        if "本体モデル" not in idx:
            used.add(0)
        for i, c in enumerate(cells):
            if not c or i in used or _SECRET.search(c):
                continue
            if not rec["url"] and _URL.search(c) and " " not in c.strip():
                rec["url"] = c
            elif not rec["release"] and _DATE.search(c) and "終了" not in c:
                rec["release"] = c
            else:
                extras.append(c)
        if "カテゴリ" in idx and col("カテゴリ"):
            extras.insert(0, col("カテゴリ"))
        if "販売ページ上の型・互換先" in idx and col("販売ページ上の型・互換先"):
            extras.append("販売ページ: " + col("販売ページ上の型・互換先"))
        if "備考" in idx and col("備考"):
            extras.append(col("備考"))
        rec["note"] = "／".join(extras)[:300] or None
        out.append(rec)
    return out


def import_models(seed_dir: Path | None = None) -> dict:
    """10シートを取り込む。**画面で直した行（edited_at）は上書きしない。**"""
    ensure_series()
    d = seed_dir or SEED_DIR
    n = {"sheets": 0, "inserted": 0, "updated": 0, "kept_edited": 0, "missing": []}
    sheets = sorted({s for _, _, s, _ in SERIES})
    with store.tx() as c:
        for sheet in sheets:
            p = d / f"{SHEET_PREFIX}{sheet}.tsv"
            if not p.is_file():
                n["missing"].append(sheet)
                continue
            n["sheets"] += 1
            for rec in _parse_sheet(p):
                cur = c.execute("SELECT id, edited_at FROM device_model WHERE sheet=? AND model=?", (sheet, rec["model"])).fetchone()
                if cur and cur["edited_at"]:
                    n["kept_edited"] += 1
                    continue
                if cur:
                    c.execute("UPDATE device_model SET model_no=?,deploy=?,release=?,ended=?,url=?,note=?,source_row=? WHERE id=?",
                              (rec["model_no"], rec["deploy"], rec["release"], rec["ended"], rec["url"], rec["note"], rec["source_row"], cur["id"]))
                    n["updated"] += 1
                else:
                    c.execute("INSERT INTO device_model (sheet,model,model_no,deploy,release,ended,url,note,source_row) VALUES (?,?,?,?,?,?,?,?,?)",
                              (sheet, rec["model"], rec["model_no"], rec["deploy"], rec["release"], rec["ended"], rec["url"], rec["note"], rec["source_row"]))
                    n["inserted"] += 1
    return n


def save_model(f: dict, user_id: str) -> dict:
    """展開の表の1行を足す・直す（画面）。"""
    if not can_edit(user_id):
        raise PermissionError("展開の表を直せるのは、商品開発部・管理者・社長の業務ロールの人です")
    sheet = (f.get("sheet") or "").strip()
    if sheet not in {s for _, _, s, _ in SERIES}:
        raise ValueError("シート（シリーズ）を選んでください")
    model = (f.get("model") or "").strip()[:120]
    if not model:
        raise ValueError("本体モデルを入れてください")
    vals = {k: ((f.get(k) or "").strip()[:300] or None) for k in ("model_no", "deploy", "release", "ended", "url", "note")}
    now = store.now_s()
    with store.tx() as c:
        mid = f.get("id")
        if mid:
            if not c.execute("SELECT 1 FROM device_model WHERE id=?", (int(mid),)).fetchone():
                raise LookupError("その行がありません")
            c.execute(f"UPDATE device_model SET sheet=?,model=?,{','.join(k + '=?' for k in vals)},edited_by=?,edited_at=? WHERE id=?",
                      (sheet, model, *vals.values(), user_id, now, int(mid)))
        else:
            if c.execute("SELECT 1 FROM device_model WHERE sheet=? AND model=?", (sheet, model)).fetchone():
                raise ValueError(f"「{model}」は {sheet} の表に既にあります")
            mid = c.execute(f"INSERT INTO device_model (sheet,model,{','.join(vals)},edited_by,edited_at) VALUES (?,?,{','.join('?' * len(vals))},?,?)",
                            (sheet, model, *vals.values(), user_id, now)).lastrowid
    return {"ok": True, "id": int(mid)}


# ── AI に調べさせる ──────────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"[\s\-_／/（）()・]", "", (s or "").lower())


def known_models() -> dict:
    """シート → 既知の機種名（AI に「候補に入れない」と渡す）。"""
    out: dict[str, list[str]] = {}
    for r in store.q("SELECT sheet, model FROM device_model ORDER BY sheet, id"):
        out.setdefault(r["sheet"], []).append(r["model"])
    return out


def inputs() -> dict:
    ensure_series()
    km = known_models()
    series = []
    for r in store.q("SELECT * FROM device_series WHERE active=1 ORDER BY sort"):
        series.append({"code": r["code"], "name": r["label"], "含むもの": r["hint"], "既知の機種": km.get(r["sheet"], [])})
    return {"対象シリーズ": series}


def build_prompt(inp: dict) -> str:
    return ASK + "\n\n調べる対象:\n" + json.dumps(inp, ensure_ascii=False, indent=1)


def parse_answer(text: str, trace: dict) -> dict:
    m = re.search(r"\{.*\}", text.strip(), re.S)
    if not m:
        raise ai_score.EnvFailure("返事に JSON がありませんでした")
    try:
        d = json.loads(m.group(0))
    except ValueError:
        raise ai_score.EnvFailure("返事の JSON が読めませんでした") from None
    codes = {c for c, _, _, _ in SERIES}
    km = {sheet: {_norm(x) for x in xs} for sheet, xs in known_models().items()}
    sheet_of = {c: s for c, _, s, _ in SERIES}
    rows, dropped = [], 0
    for x in d.get("rows") or []:
        if not isinstance(x, dict):
            continue
        code, model = str(x.get("series") or "").strip(), str(x.get("model") or "").strip()[:120]
        if code not in codes or not model:
            dropped += 1
            continue
        if _norm(model) in km.get(sheet_of[code], set()):
            dropped += 1                                   # 既知の機種は候補にしない（表記ゆれも）
            continue
        url = str(x.get("url") or "").strip()[:500]
        jp = str(x.get("jp_released") or "").strip()
        conf = str(x.get("confidence") or "").strip()
        rows.append({"series_code": code, "model": model, "release_date": str(x.get("release_date") or "").strip()[:40] or None,
                     "jp_released": jp if jp in JP else "不明", "confidence": conf if conf in CONF else None,
                     "summary": str(x.get("summary") or "").strip()[:400] or None, "url": url or None,
                     "seen": ai_web._seen(url, trace) if url else "見ていない"})
    none = [str(c) for c in d.get("none") or [] if str(c) in codes]
    unv = [str(u).strip()[:150] for u in d.get("unverified") or [] if str(u).strip()][:10]
    return {"rows": rows[:40], "none": none, "unverified": unv, "dropped": dropped}


def start_run(user_id: str, *, runner=None, sync: bool = False, force: bool = False) -> dict:
    if not force:
        if not ai_score.can_run(user_id):
            raise PermissionError("AI に調べさせられるのは、商品開発部・管理者・社長の業務ロールの人です")
        b, pre = ai_budget.check(ai_score.JOB), ai_cli.preflight()
        if not ai_score.enabled(b, pre):
            return {"started": False, "reason": ai_score.reason_off(b, pre)}
        if store.val("SELECT COUNT(*) FROM ai_run WHERE stage IN ('待ち','実行中')", (), 0):
            return {"started": False, "reason": "いま別の回が走っています。終わってから押してください"}
    with store.tx() as c:
        rid = c.execute("INSERT INTO ai_run (job,kind,stage,target_json,requested_by,requested_at) VALUES (?,?,?,?,?,?)",
                        (ai_score.JOB, RUN_KIND, "待ち", json.dumps(["device"]), user_id, store.now_s())).lastrowid
    if sync:
        _work(rid, runner, record=not force)
    else:
        threading.Thread(target=_work, args=(rid, runner), daemon=True, name=f"ai-device-{rid}").start()
    return {"started": True, "run_id": rid, "n": 1}


def _work(run_id: int, runner=None, record: bool = True) -> None:
    store.ex("UPDATE ai_run SET stage='実行中' WHERE id=?", (run_id,))
    store.conn().commit()
    model, cost = None, 0.0
    try:
        inp = inputs()
        r = ai_cli.ask(build_prompt(inp), SYSTEM, runner=runner, web=True, max_usd=MAX_USD)
        model, cost = r["model"], float(r.get("cost_usd") or 0.0)
        if not r["ok"]:
            if record and cost > 0:
                try:
                    ai_budget.record(ai_score.JOB, model, cost, note=f"新機種調査（途中で止まった） 回{run_id}",
                                     request_id=f"newproduct-ai-run{run_id}")
                except (ai_budget.BudgetUnavailable, ai_budget.OverCap):
                    pass
            raise ai_score.EnvFailure(r["error"])
        trace = r.get("trace") or {"calls": [], "opened": [], "seen": []}
        got = parse_answer(r["text"], trace)
        if record:
            try:
                ai_budget.record(ai_score.JOB, model, cost, note=f"新機種調査 回{run_id}", request_id=f"newproduct-ai-run{run_id}")
            except (ai_budget.BudgetUnavailable, ai_budget.OverCap) as e:
                raise ai_score.EnvFailure(f"使った額を記録できませんでした: {e}") from None
        now = store.now_s()
        with store.tx() as c:
            wid = c.execute("INSERT INTO ai_web (target_type,target_id,run_id,kind,body,unverified,inputs,trace,model,created_at,state) "
                            "VALUES ('device','all',?,'device',?,?,?,?,?,?,'採用')",
                            (run_id, json.dumps({"rows": got["rows"], "none": got["none"], "dropped": got["dropped"]}, ensure_ascii=False),
                             json.dumps(got["unverified"], ensure_ascii=False),
                             json.dumps({"出所": "NEW PRODUCT の展開の表", "項目": ["対象シリーズ", "既知の機種"]}, ensure_ascii=False),
                             json.dumps(trace, ensure_ascii=False), model, now)).lastrowid
            for x in got["rows"]:
                # 前の回で同じ機種が「提案」のまま残っていれば、新しい回に置き換える（決めたものは触らない）
                dup = c.execute("SELECT id, state FROM device_candidate WHERE series_code=? AND model=? ORDER BY id DESC LIMIT 1",
                                (x["series_code"], x["model"])).fetchone()
                if dup and dup["state"] != "提案":
                    continue
                if dup:
                    c.execute("UPDATE device_candidate SET state='見送り', decided_by='（自動）', decided_at=?, decided_note='新しい回に置き換えた' WHERE id=?",
                              (now, dup["id"]))
                c.execute("INSERT INTO device_candidate (run_id,web_id,series_code,model,release_date,jp_released,confidence,summary,url,seen,created_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (run_id, wid, x["series_code"], x["model"], x["release_date"], x["jp_released"], x["confidence"],
                           x["summary"], x["url"], x["seen"], now))
        ai_score._finish(run_id, "完了", 1, 0, model, cost)
    except ai_score.EnvFailure as e:
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "env", str(e))
    except Exception as e:                                   # 想定外も「実行中」のまま残さない
        ai_score._finish(run_id, "失敗", 0, 1, model, cost, "bug", f"{type(e).__name__}: {e}")


# ── 人が決める ──────────────────────────────────────────
def decide(cid: int, state: str, user_id: str, note: str = "") -> dict:
    if not can_edit(user_id):
        raise PermissionError("候補を決められるのは、商品開発部・管理者・社長の業務ロールの人です")
    if state not in ("案件にした", "展開済み", "見送り"):
        raise ValueError("案件にする／展開済み／見送り のどれかを選んでください")
    c0 = store.one("SELECT c.*, s.sheet, s.label FROM device_candidate c JOIN device_series s ON s.code=c.series_code WHERE c.id=?", (cid,))
    if c0 is None:
        raise LookupError("その候補はありません")
    if c0["state"] != "提案":
        raise ValueError(f"この候補はもう「{c0['state']}」です")
    now = store.now_s()
    pid = None
    if state == "案件にした":
        from . import project
        pid = project.create(user_id, flow_type="newmodel", internal_name=f"{c0['model']} スキンシール",
                             summary=f"新機種調査（AI・{c0['created_at'][:10]}）から。{c0['summary'] or ''}"
                                     + (f" 出典: {c0['url']}" if c0["url"] else ""),
                             origin="newmodel")["id"]
    with store.tx() as c:
        if state in ("案件にした", "展開済み"):
            c.execute("INSERT OR IGNORE INTO device_model (sheet,model,deploy,release,url,note,edited_by,edited_at) VALUES (?,?,?,?,?,?,?,?)",
                      (c0["sheet"], c0["model"], "○" if state == "展開済み" else "検討中", c0["release_date"], c0["url"],
                       f"新機種調査の候補 {cid} から", user_id, now))
        c.execute("UPDATE device_candidate SET state=?, project_id=?, decided_by=?, decided_at=?, decided_note=? WHERE id=?",
                  (state, pid, user_id, now, (note or "").strip()[:200] or None, cid))
    return {"ok": True, "state": state, "project_id": pid}


def view(user_id: str = "") -> dict:
    ensure_series()
    series = store.rows(store.q("SELECT * FROM device_series ORDER BY sort"))
    models = store.rows(store.q("SELECT * FROM device_model ORDER BY sheet, source_row, id"))
    cands = store.rows(store.q("SELECT c.*, s.label AS series_label FROM device_candidate c JOIN device_series s ON s.code=c.series_code "
                               "ORDER BY (c.state='提案') DESC, c.id DESC LIMIT 200"))
    last = store.one("SELECT * FROM ai_web WHERE target_type='device' ORDER BY id DESC LIMIT 1")
    lastv = ai_web._view(last) if last else None
    runs = store.rows(store.q("SELECT id, stage, requested_at, finished_at, error FROM ai_run WHERE kind=? ORDER BY id DESC LIMIT 5", (RUN_KIND,)))
    counts = {s: sum(1 for c in cands if c["state"] == s) for s in STATES}
    return {"series": series, "models": models, "candidates": cands, "counts": counts, "last": lastv, "runs": runs,
            "can_edit": bool(user_id) and can_edit(user_id), "ai": ai_score.status(user_id) if user_id else None,
            "states": list(STATES), "sheets": sorted({s for _, _, s, _ in SERIES})}
