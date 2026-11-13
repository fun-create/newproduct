#!/usr/bin/env python3
"""
WEBチームへ渡すページ（段階 W・ADR-141・FR-303〜314）。

商品開発がアイデアから順にコンセプトを固め、ページの材料を足し、1枚のページにして WEBチームへ渡す。
- **今のカルテの上に足す**: ①アイデア ②〜⑥はカルテ C 節（競合・ターゲット・ニーズ・差別化・コンセプト）をそのまま使う。
  ⑦ページの材料だけを新しく持つ（`web_material`）
- **止めはしない**（FR-304）: 前の段が空でも先へ進める。必須を確かめるのは「渡す」ときだけ
- **「渡す」で版を固める**（FR-309）: 中身をそのまま `web_version` に写す。あとで直して渡し直すと版が上がり、前の版との違いが出る
- **WEBチームは業務ロール `webmkt`（Webマーケ）**。新しいロールは作らない
- 店ごとの商品ページ案（⑧・AI のモックアップ）は、AI の予算の枠（job `newproduct-pagedraft`）が入ってから。今は「まだありません」と出す
- 知らせ（FR-310・ChatWork）は WEBチームの宛先が決まってから。今は送らない
- お客さまの情報は扱わない（カルテに無い）。CIP の商品名は読まない（CLAUDE.md §3-1）
"""
from __future__ import annotations

import json

from app import store

PENDING = "（未記入）"
# ページの材料（FR-305）: (キー, 名前, 書き方, 渡すときに必須か)
MATERIALS = [
    ("appeal", "訴える点とその順番", "1行1点。上から順にページに並べたい順で", True),
    ("catch", "キャッチコピーの案", "1行1案", True),
    ("photos", "載せたい写真・図", "1行1点（例: 使っている場面の写真、サイズの比較図）。上げられる素材は次の版で", False),
    ("options", "選択肢と価格の見せ方", "ページでどう見せたいか（価格そのものは試算原価の版が正）", False),
    ("notes", "納期・注意書き", "納期の目安、名入れの注意、返品できない理由など", False),
    ("refs", "参考にしたい他社のページ", "1行1件。URL と、どこを参考にしたいか", False),
    ("deadline", "WEBチームの締切", "YYYY-MM-DD", True),
    ("contact", "連絡先（商品開発の担当）", "名前。質問はこのページのやり取りへ", True),
]
MAT_KEYS = {k for k, *_ in MATERIALS}
# カルテから使う欄: (キー, 名前, 必須か)
KARTE = [("C.competitor", "競合", False), ("C.target", "ターゲット・使用シーン", True), ("C.needs", "ニーズ", True),
         ("C.diff", "差別化", True), ("C.concept", "コンセプト", True)]
STATES = ("渡した", "WEBチーム確認中", "制作中", "仕上がり受領")
DRAFT_SHOPS = ("グッズ本店", "うちわ本店", "楽天")          # 最初にモックアップを作る店（11-12 20:53 十文字さん）

DEV_ROLES = {"member", "admin", "devdept", "president"}        # 材料を書く・渡す・答える
WEB_ROLES = {"webmkt"}                                         # WEBチーム
STATE_BY = {"WEBチーム確認中": WEB_ROLES | {"president"}, "制作中": WEB_ROLES | {"president"},
            "仕上がり受領": DEV_ROLES}


def _roles(uid: str) -> set:
    from app import gate
    return set(gate.roles_of(uid))


def _project(pid: str) -> dict:
    p = store.one("SELECT p.*, f.label AS flow_label FROM project p LEFT JOIN flow_type f ON f.code=p.flow_type WHERE p.id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    return dict(p)


def _secs(pid: str) -> dict:
    return {r["section_key"]: (r["body"] or "").strip() for r in store.q(
        "SELECT section_key, body FROM project_section WHERE project_id=?", (pid,))}


def _mats(pid: str) -> dict:
    return {r["key"]: (r["body"] or "").strip() for r in store.q("SELECT key, body FROM web_material WHERE project_id=?", (pid,))}


def _lines(s: str) -> list[str]:
    return [x.strip().lstrip("・-").strip() for x in (s or "").splitlines() if x.strip()]


def steps(pid: str) -> list[dict]:
    """①〜⑨のどこまで済んだか（FR-303）。済んだかは中身があるかだけで見る（良し悪しは人が見る）。"""
    p, s, m = _project(pid), _secs(pid), _mats(pid)
    need = [k for k, _, _, req in MATERIALS if req]
    mat_done = sum(1 for k in need if m.get(k))
    last = store.one("SELECT ver, state FROM web_version WHERE project_id=? ORDER BY ver DESC LIMIT 1", (pid,))
    out = [{"n": 1, "label": "アイデア", "done": bool(p.get("idea_id") or (p.get("summary") or "").strip()),
            "how": "アイデアから起こした案件か、ヘッダの概要があれば済み", "goto": "A"}]
    for i, (k, label, _) in enumerate(KARTE, start=2):
        out.append({"n": i, "label": label, "done": bool(s.get(k)), "how": "カルテ C 節", "goto": "C"})
    out.append({"n": 7, "label": "ページの材料", "done": mat_done == len(need),
                "how": f"必須 {mat_done} / {len(need)}", "goto": "materials"})
    out.append({"n": 8, "label": "商品ページ案", "done": False, "pending": True,
                "how": "AI のモックアップは予算の枠が入ってから（今は飛ばして渡せます）", "goto": "draft"})
    out.append({"n": 9, "label": "渡す", "done": last is not None,
                "how": (f"v{last['ver']}・{last['state']}" if last else "まだ渡していません"), "goto": "hand"})
    return out


def build(pid: str) -> dict:
    """渡すページの中身（FR-307）。上から 概要 → 誰に → なぜ選ばれるか → コンセプト → 店ごとの案 → 素材 → 価格と選択肢 → 日付 → 連絡先。"""
    from app import cost, project as project_m
    p, s, m = _project(pid), _secs(pid), _mats(pid)
    missing = []

    def item(label, v, req=False, lines=False):
        v = (v or "").strip() if isinstance(v, str) else v
        if not v:
            if req:
                missing.append(label)
            return {"label": label, "text": PENDING, "missing": True, "required": req}
        return {"label": label, "lines": _lines(v)} if lines else {"label": label, "text": v}

    price = []
    v = store.one("SELECT * FROM cost_version WHERE project_id=? ORDER BY version DESC LIMIT 1", (pid,))
    if v is not None and v["price_ex_tax"] is not None:
        rate = v["tax_rate"] if v["tax_rate"] is not None else cost.TAX_DEFAULT
        price.append({"label": "販売価格", "text": f"{v['price_ex_tax']:,.0f}円（税抜）／{round(v['price_ex_tax'] * (1 + rate / 100)):,}円（税込）"
                                                   f"　※試算原価 v{v['version']}"})
    else:
        price.append(item("販売価格", s.get("D.price", "")))
    vs = store.q("SELECT label, spec FROM project_variant WHERE project_id=? AND state != '見送り' ORDER BY label", (pid,))
    price.append({"label": "バリエーション", "lines": [r["label"] + (f"（{r['spec']}）" if r["spec"] else "") for r in vs] or ["なし"]})
    price.append(item("選択肢と価格の見せ方", m.get("options"), lines=True))
    sections = [
        {"key": "overview", "title": "商品の概要", "items": [
            {"label": "社内呼称", "text": p.get("internal_name") or PENDING}, {"label": "分類", "text": project_m.product_label(p)},
            item("開発タイプ", p.get("flow_label")), item("概要・仕様", p.get("summary"))]},
        {"key": "who", "title": "誰に・どんな場面で", "items": [item("ターゲット・使用シーン", s.get("C.target"), True)]},
        {"key": "why", "title": "なぜ選ばれるか", "items": [
            item("ニーズ", s.get("C.needs"), True), item("差別化", s.get("C.diff"), True, lines=True),
            item("訴える点とその順番", m.get("appeal"), True, lines=True), item("キャッチコピーの案", m.get("catch"), True, lines=True),
            item("競合", s.get("C.competitor"))]},
        {"key": "concept", "title": "コンセプト", "items": [item("コンセプト", s.get("C.concept"), True)]},
        {"key": "draft", "title": "店ごとの商品ページ案", "items": [
            {"label": x, "text": "まだありません（AI のモックアップは予算の枠が入ってから）", "pending": True} for x in DRAFT_SHOPS]},
        {"key": "assets", "title": "素材", "items": [item("載せたい写真・図", m.get("photos"), lines=True),
                                                    item("参考にしたい他社のページ", m.get("refs"), lines=True)]},
        {"key": "price", "title": "価格と選択肢", "items": price},
        {"key": "dates", "title": "発売日と締切", "items": [
            item("発売予定日", p.get("launch_date"), True), item("WEBチームの締切", m.get("deadline"), True),
            item("納期・注意書き", m.get("notes"), lines=True)]},
        {"key": "contact", "title": "連絡先", "items": [item("連絡先（商品開発の担当）", m.get("contact"), True),
                                                     item("案件の担当", p.get("owner"))]},
    ]
    return {"project_id": pid, "internal_name": p.get("internal_name"), "built_at": store.now_s(),
            "sections": sections, "missing": missing}


def _body_of(page: dict) -> dict:
    """版の比べ方: 作った時刻を除いた中身。"""
    return {"sections": page["sections"]}


def changed_sections(old: dict, new: dict) -> list[str]:
    o = {x["key"]: x for x in old["sections"]}
    return [x["title"] for x in new["sections"] if json.dumps(o.get(x["key"]), ensure_ascii=False, sort_keys=True)
            != json.dumps(x, ensure_ascii=False, sort_keys=True)]


def overview(pid: str, uid: str) -> dict:
    p = _project(pid)
    m = _mats(pid)
    rs = _roles(uid)
    vers = []
    prev = None
    for r in store.q("SELECT * FROM web_version WHERE project_id=? ORDER BY ver", (pid,)):
        c = json.loads(r["content_json"])
        vers.append({"id": r["id"], "ver": r["ver"], "state": r["state"], "handed_by": r["handed_by"], "handed_at": r["handed_at"],
                     "state_by": r["state_by"], "state_at": r["state_at"],
                     "changed": changed_sections(prev, c) if prev else None,
                     "comments": store.rows(store.q("SELECT id, kind, body, reply_to, by_user, at FROM web_comment WHERE version_id=? "
                                                    "ORDER BY id", (r["id"],)))})
        prev = c
    cur = build(pid)
    since = changed_sections(prev, cur) if prev else None
    return {"project": {"id": pid, "internal_name": p.get("internal_name"), "launch_date": p.get("launch_date"),
                        "editable": p.get("source_of_truth") == "app"},
            "steps": steps(pid),
            "materials": [{"key": k, "label": lb, "hint": h, "required": req, "body": m.get(k, "")} for k, lb, h, req in MATERIALS],
            "page": cur, "versions": list(reversed(vers)), "changed_since_last": since,
            "states": list(STATES),
            "can": {"edit": bool(rs & DEV_ROLES), "hand": bool(rs & DEV_ROLES),
                    "comment": bool(rs & (DEV_ROLES | WEB_ROLES)),
                    "states": [s for s, who in STATE_BY.items() if rs & who]},
            "notify": "WEBチームへの知らせ（ChatWork）は、宛先が決まってから送れるようにします。今は渡したことをこの画面で伝えてください"}


def _need(uid: str, roles: set, what: str):
    if not _roles(uid) & roles:
        raise PermissionError(f"{what}のは、商品開発部・社長の業務ロールの人です" if roles == DEV_ROLES
                              else f"{what}のは、この業務ロールの人です: {'・'.join(sorted(roles))}")


def save_material(pid: str, key: str, body: str, uid: str) -> dict:
    p = _project(pid)
    if key not in MAT_KEYS:
        raise ValueError(f"知らない欄です: {key!r}")
    if p.get("source_of_truth") != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
    _need(uid, DEV_ROLES, "ページの材料を書ける")
    body = (body or "").strip()
    if key == "deadline" and body:
        import datetime as dt
        try:
            dt.date.fromisoformat(body)
        except ValueError:
            raise ValueError("締切は YYYY-MM-DD で入れてください") from None
    now = store.now_s()
    with store.tx() as c:
        c.execute("INSERT INTO web_material (project_id,key,body,updated_by,updated_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(project_id,key) DO UPDATE SET body=excluded.body, updated_by=excluded.updated_by, "
                  "updated_at=excluded.updated_at", (pid, key, body, uid, now))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what) VALUES (?,?,?,?)",
                  (pid, now, uid, f"ページの材料「{dict((k, lb) for k, lb, *_ in MATERIALS)[key]}」を更新"))
    return {"ok": True}


def hand(pid: str, uid: str) -> dict:
    """「渡す」（FR-309）。必須が埋まっていなければ止め、前の版と同じなら版を上げない。"""
    _project(pid)
    _need(uid, DEV_ROLES, "渡せる")
    page = build(pid)
    if page["missing"]:
        raise ValueError("渡す前に埋めてください: " + "、".join(page["missing"]))
    last = store.one("SELECT ver, content_json FROM web_version WHERE project_id=? ORDER BY ver DESC LIMIT 1", (pid,))
    if last is not None and _body_of(json.loads(last["content_json"])) == _body_of(page):
        raise ValueError(f"v{last['ver']} から変わっていません（同じ中身で版を上げません）")
    ver = (last["ver"] if last else 0) + 1
    now = store.now_s()
    with store.tx() as c:
        vid = c.execute("INSERT INTO web_version (project_id,ver,content_json,state,handed_by,handed_at) VALUES (?,?,?,?,?,?)",
                        (pid, ver, json.dumps(page, ensure_ascii=False), "渡した", uid, now)).lastrowid
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what) VALUES (?,?,?,?)",
                  (pid, now, uid, f"WEBチームへ渡した（v{ver}）"))
    store.audit(uid, "webpass.hand", pid, {"ver": ver})
    return {"version_id": vid, "ver": ver,
            "changed": changed_sections(json.loads(last["content_json"]), page) if last else None}


def set_state(pid: str, state: str, uid: str) -> dict:
    if state not in STATE_BY:
        raise ValueError(f"この状態には変えられません: {state!r}")
    _need(uid, STATE_BY[state], f"「{state}」にできる")
    last = store.one("SELECT id, ver, state FROM web_version WHERE project_id=? ORDER BY ver DESC LIMIT 1", (pid,))
    if last is None:
        raise ValueError("まだ渡していません")
    if STATES.index(state) <= STATES.index(last["state"]) and state != last["state"]:
        raise ValueError(f"v{last['ver']} はもう「{last['state']}」です（戻す向きには変えません）")
    now = store.now_s()
    with store.tx() as c:
        c.execute("UPDATE web_version SET state=?, state_by=?, state_at=? WHERE id=?", (state, uid, now, last["id"]))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what) VALUES (?,?,?,?)",
                  (pid, now, uid, f"渡したページ v{last['ver']} を「{state}」に"))
    store.audit(uid, "webpass.state", pid, {"ver": last["ver"], "state": state})
    return {"ok": True}


def comment(pid: str, version_id: int, kind: str, body: str, reply_to, uid: str) -> dict:
    _need(uid, DEV_ROLES | WEB_ROLES, "書き込める")
    v = store.one("SELECT id FROM web_version WHERE id=? AND project_id=?", (version_id, pid))
    if v is None:
        raise LookupError("その版はありません")
    body = (body or "").strip()
    if kind not in ("質問", "修正の希望", "答え"):
        raise ValueError("種類は 質問・修正の希望・答え のどれかです")
    if not body:
        raise ValueError("中身が空です")
    rt = int(reply_to) if str(reply_to or "").isdigit() else None
    if kind == "答え":
        if rt is None or store.one("SELECT 1 FROM web_comment WHERE id=? AND version_id=?", (rt, version_id)) is None:
            raise ValueError("答えは、同じ版の質問か修正の希望に付けてください")
    now = store.now_s()
    with store.tx() as c:
        cid = c.execute("INSERT INTO web_comment (version_id,kind,body,reply_to,by_user,at) VALUES (?,?,?,?,?,?)",
                        (version_id, kind, body, rt, uid, now)).lastrowid
    store.audit(uid, "webpass.comment", pid, {"version_id": version_id, "kind": kind})
    return {"id": cid}


def version(pid: str, ver: int) -> dict:
    r = store.one("SELECT * FROM web_version WHERE project_id=? AND ver=?", (pid, ver))
    if r is None:
        raise LookupError("その版はありません")
    d = dict(r)
    d["page"] = json.loads(d.pop("content_json"))
    return d
