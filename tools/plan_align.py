#!/usr/bin/env python3
"""
年間プランの改訂下書きを、進捗管理シートの年間プランに合わせる（2026-11-09 修正依頼書 H03・R02・ADR-129）。
**既定は書かない。**対応表を出すだけ。

    sudo -u newproduct env ... python3 tools/plan_align.py /tmp/plan.tsv --draft d542 --from 2026-11 --to 2027-04
    sudo -u newproduct env ... python3 tools/plan_align.py /tmp/plan.tsv --draft d542 --from 2026-11 --to 2027-04 --apply --by masateru

1. 下書きの枠に、元の承認版の結び付き（案件）を写す（下書きが ADR-129 の前に作られたもの。中身が同じ枠だけ）
2. 範囲の月について、シートの行と下書きの枠を突き合わせる（商品名。片方がもう片方の頭と一致すれば同じとみる）
   - 両方にある → 発売日・開発タイプ・作成エリア・商品名をシートに合わせる
   - シートにだけある → 枠を足す（「－」だけの行と、商品名も日付も無い行は足さない）
   - 下書きにだけある → 枠を消す（案件と結んでいれば結びを外してから。**案件は消さない**）
3. シートの商品名と同じ案件があれば、その枠と結ぶ（**案件は作らない**）
承認版はさわらない。承認は社長本人（H02 の回答 A）が画面で行う。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import plan, seed, store  # noqa: E402

TYPES = {"ページリニューアル": "pagerenew", "既成デザイン": "readymade", "フリーカット": "freecut",
         "スキンシール": "newmodel", "名入れ": "meire", "Web deco": "webdeco", "Webdeco": "webdeco",
         "資材リニューアル": "material"}


def norm(s: str) -> str:
    return re.sub(r"[\s　（）()【】\[\]・/／※]", "", (s or "")).lower()


def same(a: str, b: str) -> bool:
    x, y = norm(a), norm(b)
    if not x or not y:
        return False
    return x == y or (min(len(x), len(y)) >= 6 and (x.startswith(y) or y.startswith(x)))


def serial(v: str) -> str | None:
    try:
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(v)))).isoformat()
    except (TypeError, ValueError):
        return None


def sheet_rows(path: str, lo: str, hi: str) -> list[dict]:
    rows = list(csv.reader(open(path, encoding="utf-8"), delimiter="\t"))
    out, cur, effort = [], "", {}
    for i, r in enumerate(rows[3:], 4):
        r = r + [""] * 14
        m = re.search(r"(\d{4})年\D*(\d{1,2})月", r[1].replace("\\n", ""))
        if m:
            cur = f"{m.group(1)}-{int(m.group(2)):02d}"
            if r[8].strip():
                effort[cur] = r[8].strip()
        if not cur or not (lo <= cur <= hi):
            continue
        name, date = r[5].strip(), serial(r[11])
        if name in ("", "－", "-") and not date:
            continue
        if name in ("－", "-"):
            name = ""
        out.append({"row": i, "month": (date or cur)[:7] if date else cur, "date": date, "product": name,
                    "area": r[6].strip().replace("\\n", "") or None, "type": r[7].strip() or None,
                    "flow": TYPES.get(r[7].strip()), "launched": r[12].strip() == "TRUE"})
    return out, effort


def kind_of(s: dict) -> str | None:
    if s["area"] == "うちわ":
        return "uchiwa"
    if s["flow"] == "pagerenew":
        return "pagerenew"
    return "original" if s["flow"] else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan_tsv")
    ap.add_argument("--draft", required=True)
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--by", default="")
    a = ap.parse_args()
    if a.apply and not a.by:
        print("--apply には --by（実施者）が要ります", file=sys.stderr)
        return 2
    seed.run()
    v = store.one("SELECT * FROM plan_version WHERE id=?", (a.draft,))
    if v is None or v["state"] != "策定中":
        print("下書き（策定中）の版を指定してください", file=sys.stderr)
        return 2
    src = v["based_on"]
    print(f"# 年間プランの改訂下書き {a.draft} をシートに合わせる（{a.lo}〜{a.hi}・{'書く' if a.apply else '見るだけ'}）\n")

    # 1) 元の承認版の結び付きを写す（中身が同じ枠だけ）
    carry = []
    if src:
        for s in store.q("SELECT * FROM plan_slot WHERE version_id=? AND project_id IS NOT NULL", (src,)):
            d = store.one("SELECT * FROM plan_slot WHERE version_id=? AND project_id IS NULL AND launch_month=? "
                          "AND COALESCE(launch_date,'')=COALESCE(?,'') AND COALESCE(product,'')=COALESCE(?,'')",
                          (a.draft, s["launch_month"], s["launch_date"], s["product"]))
            if d is not None and not store.one("SELECT 1 FROM plan_slot WHERE version_id=? AND project_id=?",
                                               (a.draft, s["project_id"])):
                carry.append((d["id"], s))
    print(f"## 1. 承認版 {src} の結び付きを写す: {len(carry)} 件\n")
    for did, s in carry:
        print(f"- {s['launch_month']} {s['product'] or '（商品名なし）'} → 案件 {s['project_id']}（{s['link_kind']}）")

    # 2) 範囲の月を突き合わせる
    rows, effort = sheet_rows(a.plan_tsv, a.lo, a.hi)
    slots = [dict(r) for r in store.q("SELECT * FROM plan_slot WHERE version_id=? AND launch_month BETWEEN ? AND ? "
                                      "ORDER BY launch_month, launch_date", (a.draft, a.lo, a.hi))]
    carried = {did: s["project_id"] for did, s in carry}
    pname = {p["id"]: p["internal_name"] or "" for p in store.q("SELECT id, internal_name FROM project")}

    def slot_names(sl):
        pid = sl.get("project_id") or carried.get(sl["id"])
        return [sl.get("product") or ""] + ([pname.get(pid, "")] if pid else [])

    # 範囲の外（10月以前など）の枠と結ばれた案件が、シートでは範囲の中へ動いていれば、その枠を「移動」する
    # （同じ商品の枠を2つにしない。二重に数えない・依頼書 R02）
    outside = {}
    for sl in store.q("SELECT * FROM plan_slot WHERE version_id=? AND launch_month NOT BETWEEN ? AND ?", (a.draft, a.lo, a.hi)):
        pid = sl["project_id"] or carried.get(sl["id"])
        if pid:
            outside[pid] = dict(sl)
    used, plan_ops = set(), []
    for r in rows:
        proj = next((pid for pid, n in pname.items() if r["product"] and same(r["product"], n)), None)
        if proj in outside and not any(any(same(r["product"], n) for n in slot_names(sl)) for sl in slots):
            plan_ops.append(("移動", r, outside.pop(proj)))
            continue
        hit = next((sl for sl in slots if sl["id"] not in used and r["product"]
                    and any(same(r["product"], n) for n in slot_names(sl))), None)
        if hit is None and not r["product"]:
            hit = next((sl for sl in slots if sl["id"] not in used and not (sl.get("product") or "")
                        and sl["launch_date"] == r["date"]), None)
        if hit:
            used.add(hit["id"])
            plan_ops.append(("更新", r, hit))
        else:
            plan_ops.append(("追加", r, None))
    for sl in slots:
        if sl["id"] not in used:
            plan_ops.append(("削除", None, sl))
    print(f"\n## 2. {a.lo}〜{a.hi} の突き合わせ（シート {len(rows)} 行・下書き {len(slots)} 枠）\n")
    print("| 操作 | シートの行 | 月 | 商品（シート） | 発売日 | タイプ | エリア | 下書きの枠 | 変わる前 | 結ぶ案件 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    links = []
    for op, r, sl in plan_ops:
        proj = None
        if r and r["product"]:
            proj = next((pid for pid, n in pname.items() if same(r["product"], n)), None)
        before = (f"{sl['launch_date'] or sl['launch_month']} {sl.get('product') or ''} {sl.get('flow_type') or ''} "
                  f"{sl.get('area') or ''}".strip() if sl else "—")
        if r:
            print(f"| {op} | {r['row']} | {r['month']} | {r['product'] or '（枠だけ）'} | {r['date'] or '空'} | "
                  f"{r['type'] or '空'} | {r['area'] or '空'} | {sl['id'] if sl else '（新しく作る）'} | {before} | {proj or '—'} |")
        else:
            print(f"| {op} | — | {sl['launch_month']} | — | — | — | — | {sl['id']} | {before} | "
                  f"{'結びを外す（案件は残す）: ' + (sl.get('project_id') or carried.get(sl['id']) or '') if (sl.get('project_id') or carried.get(sl['id'])) else '—'} |")
        links.append((op, r, sl, proj))

    # 月ごとの本数と工数（シートは月ごとの工数ポイントの合計を1行目に持つ）
    print("\n## 3. 月ごとの本数・工数ポイント（合わせたあと）\n")
    print("| 月 | シートの行 | シートの工数ポイント | 下書きの工数ポイント（開発タイプの係数の合計） |\n|---|---|---|---|")
    ms = sorted({r["month"] for r in rows})
    eff = {f: store.val("SELECT effort_point FROM flow_type WHERE code=?", (f,)) for f in TYPES.values()}
    for m in ms:
        rr = [r for r in rows if r["month"] == m]
        tot = sum(eff.get(r["flow"]) or 0 for r in rr if r["flow"])
        unk = sum(1 for r in rr if not r["flow"])
        print(f"| {m} | {len(rr)} | {effort.get(m, '空')} | {tot:g}" + (f"（タイプ未定 {unk} 行は数えていない）" if unk else "") + " |")

    if not a.apply:
        return 0
    now = store.now_s()
    with store.tx() as c:
        for did, s in carry:
            c.execute("UPDATE plan_slot SET project_id=?, link_kind=?, converted_at=?, converted_by=?, copied_from=?, "
                      "updated_at=?, updated_by=? WHERE id=?",
                      (s["project_id"], s["link_kind"], s["converted_at"], s["converted_by"], s["id"], now, a.by, did))
    for op, r, sl, proj in links:
        if op == "削除":
            if sl.get("project_id") or carried.get(sl["id"]):
                store.ex("UPDATE plan_slot SET project_id=NULL, link_kind=NULL, converted_at=NULL, converted_by=NULL WHERE id=?",
                         (sl["id"],))
            plan.delete_slot(sl["id"])
            continue
        f = {"launch_date": r["date"] or "", "flow_type": r["flow"] or "", "area": r["area"] or "",
             "product": r["product"] or "", "product_kind": kind_of(r) or "", "effort_point": ""}
        if op in ("更新", "移動"):
            if not r["date"]:
                f.pop("launch_date"); f["launch_month"] = r["month"]
            plan.update_slot(sl["id"], a.by, **f)
            sid = sl["id"]
        else:
            f["version_id"] = a.draft
            if not r["date"]:
                f["launch_month"] = r["month"]
            sid = plan.create_slot(a.by, **f)["id"]
        cur = store.val("SELECT project_id FROM plan_slot WHERE id=?", (sid,))
        if proj and not cur and not store.one("SELECT 1 FROM plan_slot WHERE version_id=? AND project_id=?", (a.draft, proj)):
            plan.link_slot(sid, proj, a.by)
    store.audit(a.by, "plan.align_sheet", a.draft, {"from": a.lo, "to": a.hi, "carried": len(carry),
                                                    "ops": {k: sum(1 for o in links if o[0] == k) for k in ("更新", "移動", "追加", "削除")}})
    print("\n書きました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
