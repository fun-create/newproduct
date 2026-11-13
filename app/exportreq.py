#!/usr/bin/env python3
"""
店への書き出しの承認と反映の記録（段階4・ADR-152・FR-294・297・298）。

頼む → 書き出す前の検査（止めるものがあれば頼めない）→ 承認者が中身を見て承認／差し戻し → 承認でファイルを作り、
**中身をそのまま保存**（あとで見られる・同じものを何度でも落とせる）→ 管理画面に取り込んだ人が結果（反映済み／反映失敗）を記録。

- 承認できる人は設定（`export.approvers.update`・`export.approvers.new`・ログインID を , 区切り）。
  2026-11-12 20:57 十文字さん: 新規は十文字さん・渡邊さん・商品開発の責任者、既存商品の更新は十文字さん・渡邊さんだけ
- 新規か更新か: 店から取り込んだ出品（台帳に店の識別子がある）は更新。NEW PRODUCT で作った出品（まだ無い）は新規
- **FutureShop の取り込みの形は未確認**（ADR-151）。すべての画面と記録にその旨を出す
- 頼めるのは商品開発部・社長。取り込んだ結果を記録するのは、頼んだ人・承認者・商品開発部管理者
"""
from __future__ import annotations

import hashlib
import json

from app import fs_export, store

REQ_ROLES = {"member", "admin", "devdept", "president"}


def _roles(uid: str) -> set:
    from app import gate
    return set(gate.roles_of(uid))


def approvers(kind: str) -> list[str]:
    key = "export.approvers.new" if kind == "新規" else "export.approvers.update"
    v = store.val("SELECT value FROM setting WHERE key=?", (key,)) or ""
    return [x.strip() for x in v.split(",") if x.strip()]


def create(shop: str, ext_ids: list[str], reason: str, uid: str) -> dict:
    if not _roles(uid) & REQ_ROLES:
        raise PermissionError("書き出しを頼めるのは、商品開発部・社長の業務ロールの人です")
    exts = [x.strip() for x in ext_ids if x and x.strip()]
    if not exts:
        raise ValueError("書き出す出品を選んでください")
    rs = store.q(f"SELECT id, ext_id, first_import_id FROM listing WHERE shop=? AND gone_import_id IS NULL "
                 f"AND ext_id IN ({','.join('?' * len(exts))})", [shop] + exts)
    if len(rs) != len(set(exts)):
        raise LookupError("台帳に無い出品があります（識別子を確かめてください）")
    ids = [r["id"] for r in rs]
    kind = "更新" if all(r["first_import_id"] for r in rs) else "新規"
    chk = fs_export.check(shop, ids)
    if chk["errors"]:
        raise ValueError("書き出す前の検査で止まりました: " + " ／ ".join(chk["errors"][:5]))
    with store.tx() as c:
        rid = c.execute("INSERT INTO export_request (shop,kind,listing_ids,reason,check_json,state,requested_by,requested_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", (shop, kind, json.dumps(ids), (reason or "").strip()[:500] or None,
                                                     json.dumps(chk, ensure_ascii=False), "承認待ち", uid, store.now_s())).lastrowid
    store.audit(uid, "export.request", shop, {"id": rid, "kind": kind, "listings": len(ids)})
    return {"id": rid, "kind": kind, "approvers": approvers(kind), "warnings": chk["warnings"]}


def _req(rid: int) -> dict:
    r = store.one("SELECT * FROM export_request WHERE id=?", (rid,))
    if r is None:
        raise LookupError("その書き出しの依頼はありません")
    return dict(r)


def decide(rid: int, ok: bool, note: str, uid: str) -> dict:
    r = _req(rid)
    if uid not in approvers(r["kind"]):
        raise PermissionError(f"この依頼（{r['kind']}）を承認できるのは: " + "、".join(approvers(r["kind"])))
    if r["state"] != "承認待ち":
        raise ValueError(f"この依頼はもう「{r['state']}」です")
    now = store.now_s()
    if not ok:
        if not (note or "").strip():
            raise ValueError("差し戻すときは理由を書いてください")
        with store.tx() as c:
            c.execute("UPDATE export_request SET state='差し戻し', decided_by=?, decided_at=?, decision_note=? WHERE id=?",
                      (uid, now, note.strip()[:500], rid))
        store.audit(uid, "export.reject", r["shop"], {"id": rid})
        return {"state": "差し戻し"}
    ids = json.loads(r["listing_ids"])
    chk = fs_export.check(r["shop"], ids)                      # 承認の時点でもう一度（頼んだ後に変わっていないか）
    if chk["errors"]:
        raise ValueError("承認の時点の検査で止まりました: " + " ／ ".join(chk["errors"][:5]))
    stamp = now.replace("-", "").replace(":", "").replace(" ", "")
    files = []
    control = "u" if r["kind"] == "更新" else "n"
    for kind in fs_export.KINDS:
        if control == "u" and kind == "selection":
            continue                       # 項目選択肢は更新できない（n・d だけ・ADR-154）
        rs = fs_export.rows(r["shop"], kind, ids)
        if not rs:
            continue
        data = fs_export.to_csv(r["shop"], kind, rs, control)
        files.append((f"{fs_export.FILE_PREFIX[kind]}_NP{rid}_{stamp}.csv", kind, len(rs), hashlib.sha256(data).hexdigest(), data))
    with store.tx() as c:
        c.executemany("INSERT INTO export_file (request_id,name,kind,rows,sha256,data) VALUES (?,?,?,?,?,?)",
                      [(rid,) + f for f in files])
        c.execute("UPDATE export_request SET state='書き出し済み', decided_by=?, decided_at=?, decision_note=? WHERE id=?",
                  (uid, now, (note or "").strip()[:500] or None, rid))
    store.audit(uid, "export.approve", r["shop"], {"id": rid, "files": [f[0] for f in files]})
    return {"state": "書き出し済み", "files": [f[0] for f in files]}


def record_result(rid: int, ok: bool, note: str, uid: str) -> dict:
    r = _req(rid)
    if uid not in ({r["requested_by"]} | set(approvers(r["kind"]))) and not _roles(uid) & {"admin", "president"}:
        raise PermissionError("結果を記録できるのは、頼んだ人・承認者・商品開発部管理者です")
    if r["state"] not in ("書き出し済み", "反映失敗"):
        raise ValueError("書き出し済みの依頼だけ、取り込んだ結果を記録できます")
    if not ok and not (note or "").strip():
        raise ValueError("失敗のときは、管理画面に出たエラーの内容を書いてください（お客さまの情報は書かない）")
    with store.tx() as c:
        c.execute("UPDATE export_request SET state=?, applied_by=?, applied_at=?, applied_note=? WHERE id=?",
                  ("反映済み" if ok else "反映失敗", uid, store.now_s(), (note or "").strip()[:1000] or None, rid))
    store.audit(uid, "export.result", r["shop"], {"id": rid, "ok": ok})
    return {"state": "反映済み" if ok else "反映失敗"}


def listing_(args: dict) -> dict:
    st = args.get("state") or ""
    rows = store.rows(store.q(
        "SELECT e.id, e.shop, s.label shop_label, e.kind, e.listing_ids, e.reason, e.state, e.requested_by, e.requested_at, "
        "e.decided_by, e.decided_at, e.applied_by, e.applied_at FROM export_request e JOIN shop s ON s.code=e.shop "
        + ("WHERE e.state=? " if st else "") + "ORDER BY e.id DESC LIMIT 200", (st,) if st else ()))
    for r in rows:
        r["n_listings"] = len(json.loads(r.pop("listing_ids")))
    return {"rows": rows, "unverified": fs_export.UNVERIFIED}


def detail(rid: int, uid: str) -> dict:
    r = _req(rid)
    ids = json.loads(r.pop("listing_ids"))
    r["check"] = json.loads(r.pop("check_json"))
    r["listings"] = store.rows(store.q(
        f"SELECT l.id, l.ext_id, l.title, l.price, l.state, (SELECT COUNT(*) FROM listing_option o WHERE o.listing_id=l.id) n_options "
        f"FROM listing l WHERE l.id IN ({','.join('?' * len(ids))}) ORDER BY l.ext_id", ids)) if ids else []
    r["files"] = store.rows(store.q("SELECT id, name, kind, rows, sha256 FROM export_file WHERE request_id=? ORDER BY id", (rid,)))
    r["approvers"] = approvers(r["kind"])
    r["can_decide"] = uid in r["approvers"] and r["state"] == "承認待ち"
    r["can_record"] = r["state"] in ("書き出し済み", "反映失敗") and (
        uid in ({r["requested_by"]} | set(r["approvers"])) or bool(_roles(uid) & {"admin", "president"}))
    r["unverified"] = fs_export.UNVERIFIED
    # 承認する人が見る中身: 商品ファイルの行（列 → 値）。説明文・画像の列は無い
    r["preview"] = fs_export.rows(r["shop"], "goods", ids)[:20]
    return r


def file_bytes(rid: int, fid: int) -> tuple[str, bytes]:
    f = store.one("SELECT name, data FROM export_file WHERE id=? AND request_id=?", (fid, rid))
    if f is None:
        raise LookupError("そのファイルはありません")
    return f["name"], bytes(f["data"])
