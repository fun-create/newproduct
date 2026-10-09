-- NEW PRODUCT 031 — 確定した販売計画を経営管理へ渡す（FR-124・2026-10-09・ADR-078）。
-- past_product.months_json: 発売月から12か月の月ごとの売上（発売後の売れ方の形＝月ごとの割合を出すため）
-- keiei_outbox: 経営管理へ渡す中身と、渡せたか。**渡せなくても消さない**（受け口ができたら送り直す）
ALTER TABLE past_product ADD COLUMN months_json TEXT;

CREATE TABLE IF NOT EXISTS keiei_outbox (
  id          INTEGER PRIMARY KEY,
  sim_plan_id TEXT NOT NULL,
  fiscal_year INTEGER NOT NULL,
  payload     TEXT NOT NULL,            -- {rows:[{fy,month,dept_key,metric,value}], basis_json:{…}}
  state       TEXT NOT NULL,            -- 未送信 / 送信済 / 失敗
  tries       INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  created_at  TEXT NOT NULL,
  sent_at     TEXT
);
