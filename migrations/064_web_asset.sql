-- NEW PRODUCT 064 — WEBチームへ渡す素材（写真・図・試作品の写真）（段階 W・ADR-144・FR-306）。
-- 2026-11-12 21:04 十文字さん: **VPS に置き、画像だけ毎日差分で控える**（30世代の DB の控えには入れない）。
-- ファイルは data/media/<案件>/ に中身の sha256 の名前で置く。表には置き場と元の名前だけ。
-- 消すのは印だけ（deleted_at）。渡した版はファイルを指したままなので、ファイルは消さない
CREATE TABLE web_asset (
  id          INTEGER PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES project(id),
  kind        TEXT NOT NULL CHECK (kind IN ('写真','図','試作品の写真','そのほか')),
  name        TEXT NOT NULL,        -- 元のファイル名（画面に出すだけ。置き場には使わない）
  stored      TEXT NOT NULL,        -- data/media/ からの置き場
  mime        TEXT NOT NULL,
  bytes       INTEGER NOT NULL,
  sha256      TEXT NOT NULL,
  note        TEXT,
  uploaded_by TEXT NOT NULL, uploaded_at TEXT NOT NULL,
  deleted_by  TEXT, deleted_at TEXT
);
CREATE INDEX web_asset_project ON web_asset(project_id);
