#!/usr/bin/env python3
"""
seisan（生産管理 / CIP）への商品登録（第3段 ／ FR-103・FR-60 ／ ADR-043）。

2026-09-28 十文字さん: 「NEW PRODUCT 側で seisan 側の商品登録ができるようにしてほしい。
NEW PRODUCT 側にマスタは持たなくても OK。seisan 側で登録するか、NEW PRODUCT 側で
seisan のルールに沿って登録し、マスタ管理は全て seisan 側で持つようにしてほしい」

## 経路は2つ。どちらでも「正」は seisan

1. **この画面から登録する** … seisan のサーバ間API（`/api/svc/master/*`）へ送る。
   **API は吉田さん（seisan の担当・社外）が作る。**できるまでボタンは押せない（理由を出す）
2. **seisan の画面で登録した** … 決まった共通商品コードをここに記録する。
   API があれば seisan に在るかを確かめ、無ければ「未確認」と出す

## 守っていること

- **マスタを持たない。**持つのは下書きと共通商品コードだけ。**登録できたら下書きは消す**
- **seisan の規則で先に止める**（`/opt/seisan/app/store.py create_product()`・2026-09-28 に読んだ）。
  共通商品コード・商品名・販売タイプ（確定10種）・大分類が必須。seisan は**採番しない**
- **分類は seisan の既存値からだけ選ぶ**（seisan の画面がプルダウンなのと同じ。
  共有マスタを自由入力で汚さない）
- **seisan の商品名を読まない。**名入れの入力値（お客さまの個人名）が混ざる列のため。
  API にも名前を返させない（依頼書に明記）
- **トークンを他のアプリから写さない。**`config/seisan.env`（600・git 管理外）に
  このアプリ専用のものを置く
- **検査は外へ出さない。**`transport` を差し替える
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app import gate, store

BASE = Path(__file__).resolve().parent.parent
ENV_FILE = BASE / "config" / "seisan.env"
DEFAULT_URL = "http://127.0.0.1:8791"
TIMEOUT = 10

# 販売タイプの確定10種。**seisan の `store.SALES_TYPES` の写し**（2026-09-28 に読んだ値）。
# 使うのは API が無いあいだの下書きの検査だけ。**API があれば必ず API の値を使う**
SALES_TYPES_COPY = ("Webdeco", "フリーカット", "フルオーダー", "簡単オーダー", "名入れ",
                    "データ入稿", "定型", "資材", "セット", "その他")

# seisan の「新商品として登録」の入力欄と同じ並び（`PRODUCT_EDITABLE` ＋ コード・複製元）
FIELDS = [
    ("code", "共通商品コード", True, "seisan は採番しません。登録する人が決めます"),
    ("name", "商品名", True, "開発部が付ける正式な名前（お客さまの入力値ではない）"),
    ("sales_type", "販売タイプ", True, None),
    ("cat1", "大分類", True, None),
    ("cat2", "中分類", False, None),
    ("cat3", "小分類", False, None),
    ("shape", "形状", False, None),
    ("size", "サイズ", False, None),
    ("color", "色", False, None),
    ("qty_spec", "数量仕様", False, None),
    ("design", "用途デザイン", False, None),
    ("note", "備考", False, None),
    ("copy_recipe_from", "レシピ複製元（似ている既存商品のコード）", False,
     "材料・使用量・工数を写します。空だと材料費は「未確定」のまま登録されます"),
]
FIELD_KEYS = [f[0] for f in FIELDS]
STATES = ("下書き", "登録済")

# 画面から押せる人。**G5（発売可）を判定できる業務ロール**と同じにする。
# seisan 側でも「seisan の管理者か」をもう一度確かめる（依頼書 §3）
REGISTER_GATE = "G5"


class NotConfigured(RuntimeError):
    """seisan の口が使えない。**理由を言葉で持つ。**"""


class Refused(RuntimeError):
    """seisan が断った。seisan の文言をそのまま持つ。"""


# ── 接続 ───────────────────────────────────────────────
def env_path() -> Path:
    return Path(os.environ.get("NEWPRODUCT_SEISAN_ENV") or ENV_FILE)


def _conf() -> tuple[str, str]:
    p = env_path()
    if not p.is_file():
        raise NotConfigured(
            f"seisan への接続設定（{p}）がありません。seisan の口は 2026-09-28 から開通しています。"
            "設定ファイル（SEISAN_SVC_TOKEN=…・権限600・所有者 newproduct）を置くまでは、"
            "seisan の画面で登録して「seisan で登録した」でコードを記録してください")
    vals = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"').strip("'")
    tok = vals.get("SEISAN_SVC_TOKEN", "")
    if not tok:
        raise NotConfigured(f"{p} に SEISAN_SVC_TOKEN の行がありません")
    return (vals.get("SEISAN_URL") or DEFAULT_URL).rstrip("/"), tok


def _http(method: str, path: str, params: dict | None, body: dict | None) -> tuple[int, dict]:
    url, tok = _conf()
    full = url + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(full, data=data, method=method, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        # **トークンを混ぜない。**状態コードと seisan の返事だけ
        try:
            j = json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            j = {}
        return e.code, j if isinstance(j, dict) else {}
    except urllib.error.URLError as e:
        raise NotConfigured(f"seisan へつながりません: {e.reason}") from None


# 検査で差し替える。(method, path, params, body) -> (status, dict)
transport = _http


def _call(method: str, path: str, params=None, body=None) -> dict:
    _conf()                      # **設定が無ければ一度も呼ばない**（差し替え時も同じ）
    status, j = transport(method, path, params, body)
    if status == 404 and not j.get("error"):
        # 口がまだ無い（seisan 側が未実装）。つながらないのと同じ扱いにする
        raise NotConfigured("seisan に登録口（" + path + "）がまだありません")
    if status >= 400:
        raise Refused(j.get("error") or f"seisan が {status} を返しました")
    return j


def configured() -> tuple[bool, str | None]:
    try:
        _conf()
        return True, None
    except NotConfigured as e:
        return False, str(e)


def vocab() -> dict:
    """販売タイプと分類の選択肢。**API が無ければ販売タイプの写しだけ**（分類は出せない）。"""
    try:
        j = _call("GET", "/api/svc/master/vocab")
        return {"source": "seisan", "sales_types": list(j.get("sales_types") or []),
                "cat1": list(j.get("cat1") or []), "cat2": list(j.get("cat2") or []),
                "cat3": list(j.get("cat3") or []), "why": None}
    except (NotConfigured, Refused) as e:
        return {"source": "copy", "sales_types": list(SALES_TYPES_COPY),
                "cat1": None, "cat2": None, "cat3": None, "why": str(e)}


def exists(code: str) -> bool:
    return bool(_call("GET", "/api/svc/master/product", {"code": code}).get("exists"))


def similar(cat1: str = "", cat2: str = "", cat3: str = "") -> list[dict]:
    """レシピ複製元の候補。**名前は来ない**（分類・形状・サイズで選ぶ）。"""
    j = _call("GET", "/api/svc/master/products",
              {k: v for k, v in (("cat1", cat1), ("cat2", cat2), ("cat3", cat3)) if v})
    keep = ("code", "sales_type", "cat1", "cat2", "cat3", "shape", "size", "color",
            "has_recipe")
    # **名前が混ざって返ってきても持たない**（seisan 側の実装がずれても漏れない）
    return [{k: it.get(k) for k in keep} for it in (j.get("items") or [])][:50]


def store_codes() -> dict:
    """店の商品番号 → 共通商品コード・分類（2026-10-01 吉田さん実装・売上実績の段階B用）。

    **名前が混ざって返ってきても持たない**（決めた列だけを拾う）。
    1つの店舗商品コードが複数の共通商品コードに割れる行もそのまま返る。
    """
    j = _call("GET", "/api/svc/master/store_codes")
    ik = ("store", "store_code", "product_code", "cat1", "cat2", "cat3", "shape", "size", "pack_qty")
    fk = ("sku", "product_code", "cat1", "cat2", "cat3", "pack_qty")
    return {"as_of": j.get("as_of"),
            "items": [{k: it.get(k) for k in ik} for it in (j.get("items") or [])],
            "fba": [{k: it.get(k) for k in fk} for it in (j.get("fba") or [])]}


# ── 外注先・原材料（2026-10-01 吉田さん実装・ADR-047・FR-184）────────────
# **マスタは seisan。**ここは読むのと、採用した候補を seisan の規則で登録するだけ。
# 書き込みは seisan 側でも「CIP の管理者か」を確かめる（押した人の共通IDを actor で渡す）
MATERIAL_KINDS_COPY = ("原材料", "メディア", "梱包材")   # 口が無いときの写し（10-01 吉田さん）


def outsourcers() -> list[dict]:
    keep = ("id", "name", "capabilities", "work_centers", "order_method", "active")
    j = _call("GET", "/api/svc/master/outsourcers")
    return [{k: it.get(k) for k in keep} for it in (j.get("items") or [])]


def materials(category: str = "", q: str = "") -> dict:
    j = _call("GET", "/api/svc/master/materials",
              {k: v for k, v in (("category", category), ("q", q)) if v})
    keep = ("code", "kind", "category", "name", "unit_price", "supplier", "lead_days",
            "safety_days", "lot_size", "unit")
    return {"items": [{k: it.get(k) for k in keep} for it in (j.get("items") or [])],
            "kinds": list(j.get("kinds") or MATERIAL_KINDS_COPY),
            "categories": list(j.get("categories") or [])}


def _write(path: str, body: dict, actor: str, ref: str) -> dict:
    return _call("POST", path, body={**body, "actor": actor, "ref": ref})


def outsourcer_save(fields: dict, actor: str, ref: str, oid=None) -> dict:
    body = {"fields": fields}
    if oid:
        body["id"] = int(oid)
    return _write("/api/svc/master/outsourcer/save", body, actor, ref)


def outsource_price_save(fields: dict, actor: str, ref: str) -> dict:
    return _write("/api/svc/master/outsource_price/save", {"fields": fields}, actor, ref)


def material_create(code: str, fields: dict, actor: str, ref: str) -> dict:
    return _write("/api/svc/master/material/create", {"code": code, "fields": fields}, actor, ref)


def material_order_params(code: str, kind: str, fields: dict, actor: str, ref: str) -> dict:
    return _write("/api/svc/master/material/order_params",
                  {"code": code, "kind": kind, "fields": fields}, actor, ref)


# ── 検査（seisan の規則）────────────────────────────────
def validate(d: dict, voc: dict) -> tuple[list[str], list[str]]:
    """(止める理由, 注意)。**止める理由は seisan が断るものと同じ**にする。"""
    errs, warns = [], []
    for key, label, req, _ in FIELDS:
        if req and not (d.get(key) or "").strip():
            errs.append(f"{label}を入れてください")
    st = (d.get("sales_type") or "").strip()
    if st and st not in voc["sales_types"]:
        errs.append("販売タイプは次のいずれかを選んでください: " + "／".join(voc["sales_types"]))
    for k, label in (("cat1", "大分類"), ("cat2", "中分類"), ("cat3", "小分類")):
        v = (d.get(k) or "").strip()
        if not v:
            continue
        if voc.get(k) is None:
            warns.append(f"{label}「{v}」が seisan の既存の分類かは、まだ確かめられません")
        elif v not in voc[k]:
            errs.append(f"{label}「{v}」は seisan にありません。既存の分類から選んでください")
    code = (d.get("code") or "").strip()
    if code and any(ch.isspace() for ch in code):
        errs.append("共通商品コードに空白は入れられません")
    src = (d.get("copy_recipe_from") or "").strip()
    if not src:
        warns.append("レシピ複製元が空です。材料費は「未確定」のまま登録されます")
    elif src == code:
        errs.append("レシピ複製元に、登録する商品自身のコードは指定できません")
    return errs, warns


# ── 対象（案件 or バリエーション）──────────────────────────
def _project(pid: str) -> dict:
    p = store.one("SELECT * FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    return dict(p)


def _targets(pid: str) -> list[dict]:
    vs = store.q("SELECT id, label, product_code FROM project_variant WHERE project_id=? "
                 "ORDER BY label", (pid,))
    if not vs:
        return [{"variant_id": None, "label": "（この案件）"}]
    return [{"variant_id": v["id"], "label": v["label"]} for v in vs]


def _row(pid: str, vid) -> dict | None:
    r = store.one("SELECT * FROM seisan_registration WHERE project_id=? AND "
                  "COALESCE(variant_id,0)=?", (pid, int(vid or 0)))
    return dict(r) if r else None


def _vid(pid: str, vid) -> int | None:
    if vid in (None, "", "0", 0):
        if store.val("SELECT COUNT(*) FROM project_variant WHERE project_id=?", (pid,), 0):
            raise ValueError("この案件はバリエーションごとに登録します。どれかを選んでください")
        return None
    v = store.one("SELECT id FROM project_variant WHERE id=? AND project_id=?",
                  (int(vid), pid))
    if v is None:
        raise LookupError("そのバリエーションはこの案件にありません")
    return int(vid)


def _prefill(p: dict) -> dict:
    """案件から写せるものだけ。**商品名は社内呼称を仮に入れる**（直してもらう前提）。"""
    return {"name": p.get("internal_name") or "", "cat1": p.get("cat1") or "",
            "cat2": p.get("cat2") or "", "cat3": p.get("cat3") or "",
            "size": p.get("size") or ""}


def can_register(user_id: str) -> bool:
    return gate.can_approve(user_id, REGISTER_GATE)


def overview(pid: str, user_id: str) -> dict:
    p = _project(pid)
    ok, why = configured()
    voc = vocab()
    if ok:
        _verify_pending(pid)
    out = []
    for t in _targets(pid):
        r = _row(pid, t["variant_id"])
        draft = (json.loads(r["draft_json"]) if r and r["draft_json"] else None)
        if r is None or (r["state"] == "下書き" and draft is None):
            draft = _prefill(p)
        state = r["state"] if r else "未着手"
        errs, warns = validate(draft, voc) if state != "登録済" else ([], [])
        out.append({**t, "state": state,
                    "draft": draft if state != "登録済" else None,
                    "errors": errs, "warnings": warns,
                    "product_code": r["product_code"] if r else None,
                    "via": r["via"] if r else None,
                    "registered_by": r["registered_by"] if r else None,
                    "registered_at": r["registered_at"] if r else None,
                    "verified": bool(r and r["verified_at"]),
                    "last_error": r["last_error"] if r else None})
    return {"configured": ok, "why": why, "vocab": voc,
            "fields": [{"key": k, "label": lb, "required": rq, "hint": h}
                       for k, lb, rq, h in FIELDS],
            "can_register": can_register(user_id),
            "editable": p["source_of_truth"] == "app",
            "targets": out}


# ── 書き込み ────────────────────────────────────────────
def _guard_edit(pid: str) -> dict:
    p = _project(pid)
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
    return p


def _guard_register(user_id: str):
    if not can_register(user_id):
        raise PermissionError(
            "seisan への登録は、G5（発売可）を判定できる業務ロールの人だけができます")


def _upsert(pid: str, vid, **cols):
    cols["updated_at"] = store.now_s()
    r = _row(pid, vid)
    with store.tx() as c:
        if r is None:
            ks = ["project_id", "variant_id", *cols]
            c.execute(f"INSERT INTO seisan_registration ({','.join(ks)}) "
                      f"VALUES ({','.join('?' * len(ks))})", [pid, vid, *cols.values()])
        else:
            c.execute(f"UPDATE seisan_registration SET "
                      f"{','.join(k + '=?' for k in cols)} WHERE id=?",
                      [*cols.values(), r["id"]])
        if "product_code" in cols and vid is not None:
            c.execute("UPDATE project_variant SET product_code=? WHERE id=?",
                      (cols["product_code"], vid))


def save_draft(pid: str, vid, fields: dict, user_id: str) -> dict:
    _guard_edit(pid)
    vid = _vid(pid, vid)
    r = _row(pid, vid)
    if r and r["state"] == "登録済":
        raise ValueError("登録済みです。直すときは seisan の画面で直してください（正は seisan）")
    d = {k: str(fields.get(k) or "").strip()[:200] for k in FIELD_KEYS}
    _upsert(pid, vid, state="下書き", draft_json=json.dumps(d, ensure_ascii=False),
            updated_by=user_id)
    errs, warns = validate(d, vocab())
    return {"ok": True, "errors": errs, "warnings": warns}


def _mark_done(pid, vid, code, via, user_id, verified: bool):
    now = store.now_s()
    other = store.one("SELECT project_id FROM seisan_registration WHERE product_code=? "
                      "AND NOT (project_id=? AND COALESCE(variant_id,0)=?)",
                      (code, pid, int(vid or 0)))
    if other is not None:
        raise ValueError(f"共通商品コード「{code}」は別の案件（{other['project_id']}）に記録済みです")
    _upsert(pid, vid, state="登録済", draft_json=None, product_code=code, via=via,
            registered_by=user_id, registered_at=now,
            verified_at=now if verified else None, last_error=None, updated_by=user_id)


def register(pid: str, vid, user_id: str) -> dict:
    """この画面から seisan へ登録する。**seisan が受け付けたときだけ**登録済にする。"""
    _guard_edit(pid)
    _guard_register(user_id)
    vid = _vid(pid, vid)
    r = _row(pid, vid)
    if r and r["state"] == "登録済":
        raise ValueError("登録済みです")
    if not (r and r["draft_json"]):
        raise ValueError("下書きを保存してから登録してください")
    _conf()                                   # 無ければ NotConfigured（理由つき）
    d = json.loads(r["draft_json"])
    voc = vocab()
    if voc["source"] != "seisan":
        raise NotConfigured(voc["why"])
    errs, _ = validate(d, voc)
    if errs:
        raise ValueError("／".join(errs))
    fields = {k: d.get(k, "") for k in FIELD_KEYS if k not in ("code", "copy_recipe_from")}
    try:
        j = _call("POST", "/api/svc/master/product/create", body={
            "code": d["code"], "fields": fields,
            "copy_recipe_from": d.get("copy_recipe_from") or "",
            "actor": user_id, "ref": f"newproduct:{pid}" + (f"/{vid}" if vid else "")})
    except Refused as e:
        _upsert(pid, vid, last_error=str(e)[:300], updated_by=user_id)
        msg = str(e)
        if "既に存在" in msg:
            msg += "。seisan で登録済みなら「seisan で登録した」でコードを記録してください"
        raise ValueError(msg) from None
    _mark_done(pid, vid, d["code"], "newproduct", user_id, verified=True)
    return {"ok": True, "product_code": d["code"],
            "copy_error": j.get("copy_error") or None}


def record_code(pid: str, vid, code: str, user_id: str) -> dict:
    """seisan の画面で登録した商品のコードを記録する。**API があれば在るかを確かめる。**"""
    _guard_edit(pid)
    _guard_register(user_id)
    vid = _vid(pid, vid)
    code = (code or "").strip()
    if not code:
        raise ValueError("seisan の共通商品コードを入れてください")
    ok, _ = configured()
    verified = False
    if ok:
        try:
            if not exists(code):
                raise ValueError(f"seisan に共通商品コード「{code}」がありません。"
                                 "登録が済んでいるか、コードの打ち間違いが無いかを確かめてください")
            verified = True
        except NotConfigured:
            verified = False              # 口がまだ無い。申告のまま記録し「未確認」と出す
    _mark_done(pid, vid, code, "seisan", user_id, verified)
    return {"ok": True, "product_code": code, "verified": verified}


def reset(pid: str, vid, user_id: str) -> dict:
    """記録を取り消して下書きに戻す。**seisan の商品は消えない**（消すなら seisan で）。"""
    _guard_edit(pid)
    _guard_register(user_id)
    vid = _vid(pid, vid)
    r = _row(pid, vid)
    if r is None or r["state"] != "登録済":
        raise ValueError("登録済みではありません")
    _upsert(pid, vid, state="下書き", product_code=None, via=None, registered_by=None,
            registered_at=None, verified_at=None, updated_by=user_id)
    return {"ok": True, "note": "このアプリの記録だけを戻しました。seisan の商品は残っています"}


def _verify_pending(pid: str):
    """申告のまま（未確認）の記録を、seisan に在るかで確かめ直す。**無くても消さない。**"""
    for r in store.q("SELECT id, product_code FROM seisan_registration WHERE project_id=? "
                     "AND state='登録済' AND verified_at IS NULL", (pid,)):
        try:
            if exists(r["product_code"]):
                # **確定まで行う。**画面を開く（GET）経路なので、後で誰も commit しない。
                # store.ex のままだと書き込みの途中で残り、他の書き込みを待たせる
                with store.tx() as c:
                    c.execute("UPDATE seisan_registration SET verified_at=? WHERE id=?",
                              (store.now_s(), r["id"]))
        except (NotConfigured, Refused):
            return


# ── ゲート（G5「Seisan の商品コード確定」）───────────────────
def gate_state(pid: str) -> tuple[bool, str]:
    ts = _targets(pid)
    done, unver, rest = 0, 0, []
    for t in ts:
        r = _row(pid, t["variant_id"])
        if r and r["state"] == "登録済":
            done += 1
            if not r["verified_at"]:
                unver += 1
        else:
            rest.append(t["label"])
    why = f"seisan 登録 {done}/{len(ts)}"
    if rest:
        why += "（未: " + "、".join(rest[:5]) + ("…" if len(rest) > 5 else "") + "）"
    if unver:
        why += f"。うち {unver} 件は seisan に在るか未確認（登録口ができたら確かめます）"
    return (done == len(ts)), why
