#!/usr/bin/env python3
"""
繰り返しの仕事（ルーティン・ADR-108・2026-10-18 十文字さんの選択「繰り返しの仕事を持てるようにする」）。

シート「ルーティンワーク」と「進捗共有」はアプリに置き場が無く、11/9 の切り替え後もシートに残るところだった。
- 毎年（月を選ぶ）・毎月（日 か 月末）・毎週（曜日）の仕事を登録すると、**時期が近づいたら「案件外の仕事」を1件起こす**
  （既定: 毎週は6日前・毎月は14日前・毎年は30日前）。同じ回は二度起こさない（`routine_occurrence`）
- 起こすのは「使う」にしてある分だけ。**登録した日より前の回はさかのぼって起こさない**
- 毎日のもの（出勤時・退勤10分前 など）はタスクにしない（毎日増えるだけ）。一覧で見るだけ
- 起こした仕事は、担当が入っていればカレンダー（Calendar アプリ）のタスクにも出る（ADR-100）
- シートからの取り込み（`import_sheet`）は、時期が読めないもの（「月2回」「9～10月」など）を「使わない」で入れ、理由を書く。**推測で時期を決めない**
"""
from __future__ import annotations

import calendar
import csv
import datetime as dt
import re
from pathlib import Path

from app import store

CADENCES = {"yearly": "毎年", "monthly": "毎月", "weekly": "毎週", "daily": "毎日"}
LEAD = {"weekly": 6, "monthly": 14, "yearly": 30}
WEEK = "月火水木金土日"
EDITORS = ("devdept", "admin", "president")
SEED = Path(__file__).resolve().parent.parent / "seed" / "01_進捗管理商品開発__ルーティンワーク.tsv"
CATEGORY = "ルーティン"


def can_edit(user_id: str) -> bool:
    from app import gate
    return bool(set(gate.roles_of(user_id)) & set(EDITORS))


def _ints(s: str | None, lo: int, hi: int) -> list[int]:
    out = []
    for x in re.split(r"[,、\s]+", (s or "").strip()):
        if x:
            if not x.isdigit() or not lo <= int(x) <= hi:
                raise ValueError(f"{lo}〜{hi} の数を「,」で区切って入れてください")
            out.append(int(x))
    return sorted(set(out))


def _day_of(year: int, month: int, day: str | None) -> dt.date:
    last = calendar.monthrange(year, month)[1]
    if (day or "").strip() in ("last", "月末"):
        return dt.date(year, month, last)
    d = int(day) if (day or "").strip().isdigit() else 1
    return dt.date(year, month, min(d, last))


def occurrences(r: dict, start: dt.date, end: dt.date) -> list[dt.date]:
    """[start, end] にかかる回の期限。**時期の決まっていない行（月・日が空）は回を作らない。**"""
    out = []
    cad = r["cadence"]
    if undetermined(r):
        return out
    if cad == "weekly":
        wds = _ints(r["weekdays"], 0, 6)
        d = start
        while d <= end:
            if d.weekday() in wds:
                out.append(d)
            d += dt.timedelta(days=1)
    elif cad == "monthly":
        y, m = start.year, start.month
        while dt.date(y, m, 1) <= end:
            d = _day_of(y, m, r["day"])
            if start <= d <= end:
                out.append(d)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    elif cad == "yearly":
        months = _ints(r["months"], 1, 12)
        for y in range(start.year, end.year + 1):
            for m in months:
                d = _day_of(y, m, r["day"])
                if start <= d <= end:
                    out.append(d)
    return sorted(out)


def ensure(today: dt.date | None = None) -> int:
    """時期が近づいた回を「案件外の仕事」に起こす。何度呼んでも同じ（起こした回は覚えている）。"""
    today = today or store.today()
    n = 0
    rows = store.rows(store.q("SELECT * FROM routine WHERE active=1 AND cadence IN ('yearly','monthly','weekly')"))
    if not rows:
        return 0
    now = store.now_s()
    with store.tx() as c:
        for r in rows:
            since = dt.date.fromisoformat((r["edited_at"] or r["created_at"] or now)[:10])
            lead = r["lead_days"] if r["lead_days"] is not None else LEAD[r["cadence"]]
            try:
                dates = occurrences(r, max(today, since), today + dt.timedelta(days=lead))
            except ValueError:
                continue                                    # 時期の書き方が壊れている行は飛ばす（画面に出る）
            for d in dates:
                if c.execute("SELECT 1 FROM routine_occurrence WHERE routine_id=? AND occurs_on=?",
                             (r["id"], d.isoformat())).fetchone():
                    continue
                wid = c.execute("INSERT INTO work_item (kind,title,category,role,assignee,due_on,hours,status,created_at) "
                                "VALUES ('案件外',?,?,?,?,?,?,'未着手',?)",
                                (r["title"], CATEGORY, r["role"], r["assignee"], d.isoformat(), r["hours"], now)).lastrowid
                c.execute("INSERT INTO routine_occurrence (routine_id,occurs_on,work_item_id,created_at) VALUES (?,?,?,?)",
                          (r["id"], d.isoformat(), wid, now))
                n += 1
    return n


def undetermined(r: dict) -> str | None:
    """時期が決まっていなければ、何が足りないか（取り込みで読めなかった行。2026-10-18 通しの点検）。"""
    if r["cadence"] == "yearly" and not (r["months"] or "").strip():
        return "月が未定"
    if r["cadence"] in ("yearly", "monthly") and not (r["day"] or "").strip():
        return "日が未定"
    return None


def rule_text(r: dict) -> str:
    cad = r["cadence"]
    if undetermined(r):
        return CADENCES.get(cad, cad) + "（" + undetermined(r) + "）"
    try:
        if cad == "weekly":
            return "毎週 " + "・".join(WEEK[i] for i in _ints(r["weekdays"], 0, 6)) + "曜"
        if cad == "monthly":
            return "毎月 " + ("月末" if (r["day"] or "") in ("last", "月末") else f"{r['day'] or 1}日")
        if cad == "yearly":
            return "毎年 " + "・".join(str(m) for m in _ints(r["months"], 1, 12)) + "月" + \
                ("末" if (r["day"] or "") in ("last", "月末") else f" {r['day'] or 1}日")
    except ValueError:
        return "時期の書き方が読めません"
    return "毎日 " + (r["timing"] or "")


def listing(today: dt.date | None = None) -> dict:
    today = today or store.today()
    rows = store.rows(store.q("SELECT r.*, ro.label AS role_label FROM routine r LEFT JOIN role ro ON ro.code=r.role "
                              "ORDER BY CASE r.cadence WHEN 'yearly' THEN 0 WHEN 'monthly' THEN 1 WHEN 'weekly' THEN 2 ELSE 3 END, r.id"))
    for r in rows:
        r["rule"] = rule_text(r)
        r["cadence_label"] = CADENCES.get(r["cadence"], r["cadence"])
        nxt = None
        if r["cadence"] != "daily":
            try:
                ds = occurrences(r, today, today + dt.timedelta(days=400))
                nxt = ds[0].isoformat() if ds else None
            except ValueError:
                pass
        r["next"] = nxt
        last = store.one("SELECT o.occurs_on, w.status FROM routine_occurrence o LEFT JOIN work_item w ON w.id=o.work_item_id "
                         "WHERE o.routine_id=? ORDER BY o.occurs_on DESC LIMIT 1", (r["id"],))
        r["last"] = dict(last) if last else None
    return {"rows": rows, "cadences": [{"code": k, "label": v} for k, v in CADENCES.items()],
            "counts": {k: sum(1 for r in rows if r["cadence"] == k) for k in CADENCES},
            "active": sum(1 for r in rows if r["active"])}


def save(f: dict, user_id: str) -> dict:
    if not can_edit(user_id):
        raise PermissionError("繰り返しの仕事を登録・修正できるのは、商品開発部・管理者・社長の業務ロールの人です")
    cad = (f.get("cadence") or "").strip()
    if cad not in CADENCES:
        raise ValueError("毎年・毎月・毎週・毎日のどれかを選んでください")
    title = (f.get("title") or "").strip()[:200]
    if not title:
        raise ValueError("何をするかを入れてください")
    vals = {"title": title, "cadence": cad, "months": None, "day": None, "weekdays": None, "timing": None}
    if cad == "yearly":
        if not _ints(f.get("months"), 1, 12):
            raise ValueError("毎年のときは月を入れてください（例 4,8,12）")
        vals["months"] = ",".join(str(x) for x in _ints(f.get("months"), 1, 12))
    if cad in ("yearly", "monthly"):
        day = (f.get("day") or "").strip()
        if day and day not in ("last", "月末") and not (day.isdigit() and 1 <= int(day) <= 31):
            raise ValueError("日は 1〜31 か「月末」で入れてください")
        if not day:
            raise ValueError("日を入れてください（1〜31 か「月末」）")
        vals["day"] = "last" if day == "月末" else day
    if cad == "weekly":
        if not _ints(f.get("weekdays"), 0, 6):
            raise ValueError("毎週のときは曜日を選んでください")
        vals["weekdays"] = ",".join(str(x) for x in _ints(f.get("weekdays"), 0, 6))
    if cad == "daily":
        vals["timing"] = (f.get("timing") or "").strip()[:40] or None
    role = (f.get("role") or "").strip() or None
    if role and store.one("SELECT 1 FROM role WHERE code=?", (role,)) is None:
        raise ValueError("知らないロールです")
    hours = (f.get("hours") or "").strip()
    try:
        hours = float(hours) if hours else None
    except ValueError:
        raise ValueError("時間は数字で入れてください") from None
    lead = (f.get("lead_days") or "").strip()
    if lead and not (lead.isdigit() and int(lead) <= 120):
        raise ValueError("何日前に起こすかは 0〜120 で入れてください")
    from app.task import check_user as _check_user
    vals.update({"reason": (f.get("reason") or "").strip()[:300] or None, "manual": (f.get("manual") or "").strip()[:500] or None,
                 "role": role, "assignee": _check_user(f.get("assignee")) or None, "hours": hours,
                 "lead_days": int(lead) if lead else None,
                 # 取り込みで付けた「時期を決めてください」は、時期を決めて保存したら外す
                 "note": re.sub(r"^時期「[^」]*」が1つの(月|日)に決まらないので、使う前に(月|日)を決めてください$", "",
                                (f.get("note") or "").strip())[:300] or None,
                 "active": 0 if str(f.get("active", "1")) in ("0", "false") else 1})
    now = store.now_s()
    rid = f.get("id")
    with store.tx() as c:
        if rid:
            if c.execute("SELECT 1 FROM routine WHERE id=?", (int(rid),)).fetchone() is None:
                raise LookupError("その繰り返しの仕事がありません")
            c.execute(f"UPDATE routine SET {','.join(k + '=?' for k in vals)},edited_by=?,edited_at=? WHERE id=?",
                      (*vals.values(), user_id, now, int(rid)))
        else:
            rid = c.execute(f"INSERT INTO routine ({','.join(vals)},edited_by,edited_at,created_at) VALUES "
                            f"({','.join('?' * len(vals))},?,?,?)", (*vals.values(), user_id, now, now)).lastrowid
    ensure()
    return {"ok": True, "id": int(rid)}


def set_quick(rid: int, user_id: str, active=None, assignee=None) -> dict:
    """一覧の行から「使う／使わない」と担当だけを変える（点検 M-6: 19行を1行ずつ「直す」で開いていた）。
    時期が決まっていない行は「使う」にできない（起こす日が無い）。担当は利用者台帳で確かめる。"""
    if not can_edit(user_id):
        raise PermissionError("繰り返しの仕事を登録・修正できるのは、商品開発部・管理者・社長の業務ロールの人です")
    r = store.one("SELECT * FROM routine WHERE id=?", (int(rid),))
    if r is None:
        raise LookupError("その繰り返しの仕事がありません")
    sets: dict = {}
    if active is not None:
        on = str(active) in ("1", "true", "on")
        if on and undetermined(dict(r)):
            raise ValueError(f"時期が決まっていません（{undetermined(dict(r))}）。「直す」で時期を入れてから「使う」にしてください")
        sets["active"] = 1 if on else 0
    if assignee is not None:
        from app.task import check_user
        sets["assignee"] = check_user(assignee) or None
    if not sets:
        return {"ok": True, "id": int(rid)}
    with store.tx() as c:
        c.execute(f"UPDATE routine SET {','.join(k + '=?' for k in sets)},edited_by=?,edited_at=? WHERE id=?",
                  (*sets.values(), user_id, store.now_s(), int(rid)))
    ensure()
    return {"ok": True, "id": int(rid), **sets}


# ── シートからの取り込み ──────────────────────────────────────
_WD = {c: i for i, c in enumerate(WEEK)}


def parse_sheet(rows: list[list[str]]) -> list[dict]:
    """「ルーティンワーク」シート → 登録の候補。時期は（ ）の中の書き方から。読めないものは active=0 と理由。"""
    out, sect = [], None
    heads = {"イヤリー": "yearly", "マンスリー": "monthly", "ウィークリー": "weekly", "デイリー": "daily"}
    for i, r in enumerate(rows, start=1):
        c1 = r[1].strip() if len(r) > 1 else ""
        if c1 in heads:
            sect = heads[c1]
            continue
        if not sect or not c1.startswith("・"):
            continue
        text = c1.lstrip("・").strip()
        reason = r[2].strip() if len(r) > 2 else ""
        manual = r[3].strip() if len(r) > 3 else ""
        m = re.search(r"（([^）]*)）\s*$", text)
        when = m.group(1) if m else ""
        title = text[:m.start()].strip() if m else text
        rec = {"title": title, "cadence": sect, "reason": reason or None, "manual": manual or None, "source_row": i,
               "months": None, "day": None, "weekdays": None, "timing": None, "active": 1, "note": None}
        if sect == "yearly":
            ms = re.fullmatch(r"([\d・]+)月", when)
            if ms and "～" not in when:
                rec["months"] = ",".join(str(int(x)) for x in ms.group(1).split("・"))
                rec["day"] = "1"
            else:
                rec["active"], rec["note"] = 0, f"時期「{when or '書いていない'}」が1つの月に決まらないので、使う前に月を決めてください"
        elif sect == "monthly":
            if when == "月初":
                rec["day"] = "1"
            elif when == "月末":
                rec["day"] = "last"
            else:
                rec["active"], rec["note"] = 0, f"時期「{when or '書いていない'}」が1つの日に決まらないので、使う前に日を決めてください"
        elif sect == "weekly":
            if when in _WD:
                rec["weekdays"] = str(_WD[when])
            else:
                rec["active"], rec["note"] = 0, f"曜日「{when or '書いていない'}」が読めないので、使う前に曜日を決めてください"
        else:
            rec["timing"] = when or None
        out.append(rec)
    return out


def import_sheet(user_id: str = "import", path: Path | None = None, active: bool = True) -> dict:
    """シートから入れる。**同じ行（source_row）は二度入れない。**`active=False` なら全部「使わない」で入れる。"""
    p = path or SEED
    with p.open(encoding="utf-8") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    n = {"added": 0, "kept": 0, "inactive": 0}
    now = store.now_s()
    with store.tx() as c:
        for rec in parse_sheet(rows):
            if c.execute("SELECT 1 FROM routine WHERE source_row=? AND title=?", (rec["source_row"], rec["title"])).fetchone():
                n["kept"] += 1
                continue
            act = rec["active"] if active else 0
            c.execute("INSERT INTO routine (title,cadence,months,day,weekdays,timing,reason,manual,role,active,note,source_row,created_at,edited_by,edited_at) "
                      "VALUES (?,?,?,?,?,?,?,?,'devdept',?,?,?,?,?,?)",
                      (rec["title"], rec["cadence"], rec["months"], rec["day"], rec["weekdays"], rec["timing"], rec["reason"],
                       rec["manual"], act, rec["note"], rec["source_row"], now, user_id, now))
            n["added"] += 1
            n["inactive"] += 0 if act else 1
    return n
