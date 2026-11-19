-- NEW PRODUCT 074 — ラベル×日の売上（金額だけ）。sync_newprod_master.ps1（月曜 8:30）が sales.csv と新商品マスタから数えて送る（ADR-161）。
-- 5月からの年度累計で、発売から1年になる月を日で区切るのに使う（依頼書 R01）。**商品名は持たない**
CREATE TABLE IF NOT EXISTS newprod_daily (
  label   TEXT NOT NULL,          -- 週次レポートの商品ラベル（新商品マスタの1列目）
  day     TEXT NOT NULL,          -- YYYY-MM-DD（sales.csv の売上日）
  amount  REAL NOT NULL,          -- その日の売上の合計（列は送る側が amount_col で名乗る）
  PRIMARY KEY (label, day)
);
CREATE TABLE IF NOT EXISTS newprod_daily_run (
  id          INTEGER PRIMARY KEY,
  received_at TEXT NOT NULL,
  source      TEXT,
  day_from    TEXT NOT NULL,
  day_to      TEXT NOT NULL,
  amount_col  TEXT,
  labels      INTEGER,
  days        INTEGER,
  total       REAL,
  check_json  TEXT                -- 週次レポートの月別の表との突き合わせ（{compared_months, pairs, mismatch[...], ...}）
);
