-- NEW PRODUCT 024 — 競合調査を案件ごとに表で持つ（F-9-6〜8・FR-141〜143）。2026-10-05。
-- カルテ C.competitor（自由記述）はメモとして残す。比べる軸はこの表に置く。
-- **出典（URL）と確認日は必須**（F-9-8）。**分からない値は NULL（未確認）。0 で埋めない**（N-10）。
-- 売上の推計（F-9-7）は保存しない。表示のたびに「レビュー数 ÷ レビュー率 × 価格」で出す
-- （レビュー率は設定 `competitor_review_rate`。未設定なら推計を出さない）。
CREATE TABLE IF NOT EXISTS competitor_item (
  id            INTEGER PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  shop          TEXT NOT NULL,          -- 店・メーカー
  item          TEXT NOT NULL,          -- 競合の商品（相手の商品名。お客さまの情報ではない）
  channel       TEXT,                   -- 楽天 / Amazon / Yahoo! / 自社サイト / その他
  url           TEXT NOT NULL,          -- 出典。http(s) のみ
  checked_on    TEXT NOT NULL,          -- 確認日（YYYY-MM-DD）
  price_yen     REAL,                   -- 税込の販売価格。NULL = 未確認
  spec          TEXT,                   -- 仕様（サイズ・素材・セット内容）
  design        TEXT,                   -- デザイン傾向
  review_count  INTEGER,                -- 確認日時点のレビュー件数（累計）。NULL = 未確認
  review_avg    REAL,                   -- 平均評価 0〜5。NULL = 未確認
  review_note   TEXT,                   -- レビューで目立つ声（良い点・不満）
  note          TEXT,
  created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ci_project ON competitor_item (project_id);
