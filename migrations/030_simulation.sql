-- NEW PRODUCT 030 — 販売計画シミュレーション（F-14・FR-125〜134・2026-10-09・ADR-072）。
-- past_product: 過去の新商品の「発売から12か月」の売上（新商品売上状況の表 2022〜2025年度・9/20 の写し）。**歩留まりの元**
-- sim_plan: 確定した計画の版（社長が確定。前提・使った実績の範囲・試算日時を残す・FR-131）
CREATE TABLE IF NOT EXISTS past_product (
  code          TEXT PRIMARY KEY,        -- P001 など（元の表の商品ID）
  name          TEXT NOT NULL,           -- 社内の商品名（お客さまの情報ではない）
  launch_date   TEXT,                    -- YYYY-MM-DD
  fy            INTEGER,
  first12_yen   REAL,                    -- 発売月から12か月の売上（税込・元の表の月次合計）
  months_seen   INTEGER NOT NULL,        -- 12か月のうち、表に数字があった月数
  complete      INTEGER NOT NULL,        -- 12か月そろったか（そろわない商品は分布に入れない）
  channels_json TEXT,                    -- 12か月のチャネル別（楽天・Yahoo・うちわ・グッズ・ギフトモール・amazon）
  source        TEXT NOT NULL,
  imported_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_plan (
  id           TEXT PRIMARY KEY,
  fiscal_year  INTEGER NOT NULL,
  label        TEXT,
  params_json  TEXT NOT NULL,            -- 前提（目標・本数・構成・工数の上限・歩留まり）
  result_json  TEXT NOT NULL,            -- 試算の結果（分布・月別の配置・工数）
  data_range   TEXT NOT NULL,            -- 使った実績の範囲（例「2022〜2025年度発売・12か月そろった 85 商品」）
  state        TEXT NOT NULL,            -- 確定 / 取消
  decided_by   TEXT NOT NULL,
  decided_at   TEXT NOT NULL,
  note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_plan_fy ON sim_plan (fiscal_year, decided_at DESC);
