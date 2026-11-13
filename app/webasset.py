#!/usr/bin/env python3
"""
WEBチームへ渡す素材（段階 W・ADR-144・FR-306）。写真・図・試作品の写真を案件ごとに上げる。

- **置き場は VPS の data/media/<案件>/**（2026-11-12 21:04 十文字さん）。名前は中身の sha256（元の名前は表にだけ持つ。
  置き場の名前に人の付けた名前を使わない＝`../` などで外へ出られない）
- **控えは画像だけ毎日差分**（`deploy/backup_media.sh`）。30世代の DB の控え（backup.sh）には入れない
- **種類は中身で見分ける**（拡張子は信じない）。JPEG・PNG・GIF・WebP・HEIC・PDF だけ。**SVG は受けない**（中に script を持てる）
- 1つ 10MB まで・1案件 200 個まで。消すのは印だけ（渡した版が指したまま残るため、ファイルは消さない）
- お客さまの写真（名入れの実例など）は上げない前提。画面にもそう書く
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app import store

MAX_BYTES = 10 * 1024 * 1024
MAX_PER_PROJECT = 200
KINDS = ("写真", "図", "試作品の写真", "そのほか")
DEV_ROLES = {"member", "admin", "devdept", "president"}


def media_dir() -> Path:
    return Path(os.environ.get("NEWPRODUCT_MEDIA") or (store.db_path().parent / "media"))


def sniff(b: bytes) -> tuple[str, str] | None:
    """中身の頭で種類を決める。(mime, 拡張子)。知らないものは None。"""
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp", "webp"
    if b[4:8] == b"ftyp" and b[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        return "image/heic", "heic"
    if b[:5] == b"%PDF-":
        return "application/pdf", "pdf"
    return None


def _need_dev(uid: str):
    from app import gate
    if not set(gate.roles_of(uid)) & DEV_ROLES:
        raise PermissionError("素材を上げる・消すのは、商品開発部・社長の業務ロールの人です")


def _project(pid: str) -> dict:
    p = store.one("SELECT id, source_of_truth FROM project WHERE id=?", (pid,))
    if p is None:
        raise LookupError("案件がありません")
    return dict(p)


def save(pid: str, name: str, raw: bytes, kind: str, note: str, uid: str) -> dict:
    p = _project(pid)
    if p["source_of_truth"] != "app":
        raise PermissionError("Drive 側が正本の案件はアプリで編集できません")
    _need_dev(uid)
    if kind not in KINDS:
        raise ValueError("種類は 写真・図・試作品の写真・そのほか のどれかです")
    if not raw:
        raise ValueError("ファイルが空です")
    if len(raw) > MAX_BYTES:
        raise ValueError(f"大きすぎます（{len(raw) / 1024 / 1024:.1f}MB。1つ {MAX_BYTES // 1024 // 1024}MB まで）")
    t = sniff(raw)
    if t is None:
        raise ValueError("受け付けない種類です（JPEG・PNG・GIF・WebP・HEIC・PDF だけ。SVG は受けません）")
    n = store.val("SELECT COUNT(*) FROM web_asset WHERE project_id=? AND deleted_at IS NULL", (pid,))
    if n >= MAX_PER_PROJECT:
        raise ValueError(f"この案件の素材が {MAX_PER_PROJECT} 個に達しています。要らないものを消してください")
    h = hashlib.sha256(raw).hexdigest()
    rel = f"{pid}/{h}.{t[1]}"
    path = media_dir() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        os.chmod(tmp, 0o640)
        tmp.replace(path)
    name = (Path(str(name or "")).name or f"素材.{t[1]}")[:200]
    now = store.now_s()
    with store.tx() as c:
        aid = c.execute("INSERT INTO web_asset (project_id,kind,name,stored,mime,bytes,sha256,note,uploaded_by,uploaded_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (pid, kind, name, rel, t[0], len(raw), h, (note or "").strip()[:500] or None, uid, now)).lastrowid
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what) VALUES (?,?,?,?)",
                  (pid, now, uid, f"素材を上げた（{kind}・{name}）"))
    store.audit(uid, "webasset.save", pid, {"id": aid, "kind": kind, "bytes": len(raw), "sha256": h})
    return {"id": aid, "mime": t[0]}


def listing(pid: str) -> list[dict]:
    return store.rows(store.q("SELECT id, kind, name, mime, bytes, note, uploaded_by, uploaded_at FROM web_asset "
                              "WHERE project_id=? AND deleted_at IS NULL ORDER BY id", (pid,)))


def get(pid: str, aid: int) -> tuple[Path, str, str]:
    """消した印があっても返す（渡した版が指しているため）。ログインした人だけ（/api/ の関門）。"""
    r = store.one("SELECT stored, mime, name FROM web_asset WHERE id=? AND project_id=?", (aid, pid))
    if r is None:
        raise LookupError("素材がありません")
    base = media_dir().resolve()
    path = (base / r["stored"]).resolve()
    if base not in path.parents:
        raise LookupError("素材がありません")
    if not path.exists():
        raise LookupError("素材のファイルが見つかりません（控えから戻せます）")
    return path, r["mime"], r["name"]


def delete(pid: str, aid: int, uid: str) -> dict:
    _project(pid)
    _need_dev(uid)
    r = store.one("SELECT name, kind FROM web_asset WHERE id=? AND project_id=? AND deleted_at IS NULL", (aid, pid))
    if r is None:
        raise LookupError("素材がありません")
    now = store.now_s()
    with store.tx() as c:
        c.execute("UPDATE web_asset SET deleted_by=?, deleted_at=? WHERE id=?", (uid, now, aid))
        c.execute("INSERT INTO project_revision (project_id,changed_at,changed_by,what) VALUES (?,?,?,?)",
                  (pid, now, uid, f"素材を外した（{r['kind']}・{r['name']}）"))
    store.audit(uid, "webasset.delete", pid, {"id": aid})
    return {"ok": True}
