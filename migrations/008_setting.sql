-- NEW PRODUCT 008 — 設定（§4-9）。
--
-- **仮定値は画面に出す**（N-10）。ここに無い値は「未設定」であって 0 ではない。
-- 例: `monthly_launch_target`（月間発売目標本数）は 36本 vs 52本 が未決で、
-- F-14 のシミュレーションで決める。**決まるまで「未計測」と出す。**

CREATE TABLE IF NOT EXISTS setting (
  key        TEXT PRIMARY KEY,
  value      TEXT,                 -- NULL = 未設定（0 ではない）
  label      TEXT NOT NULL,
  kind       TEXT NOT NULL,        -- number / text / bool
  unit       TEXT,
  why        TEXT,                 -- 未設定のときに画面へ出す理由
  updated_by TEXT,
  updated_at TEXT
);
