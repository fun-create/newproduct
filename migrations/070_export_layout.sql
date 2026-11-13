-- NEW PRODUCT 070 — 店の登録用ファイルの列の並び（段階4・ADR-151・FR-292）。
-- 取り込んだ出力ファイルの見出しを、店 × ファイルの種類ごとに覚える（説明文・画像・在庫の列は落とした後）。
-- 書き出しはこの並びに従う。**取り込みの形（必須の列・操作の列）が出力の形と同じかは未確認**（管理画面での試しの1件で確かめる）
CREATE TABLE export_layout (
  shop        TEXT NOT NULL REFERENCES shop(code),
  kind        TEXT NOT NULL,            -- goods / selection / opt_basic / opt_select など（listing_parse.KINDS）
  columns_json TEXT NOT NULL,           -- 列名の並び
  dropped_json TEXT NOT NULL,           -- 落とした列（書き出さない）
  encoding    TEXT NOT NULL,
  source_file TEXT NOT NULL, captured_at TEXT NOT NULL,
  PRIMARY KEY (shop, kind)
);
