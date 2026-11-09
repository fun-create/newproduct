-- 新商品の売上を週次レポートから取り込む（2026-11-09 修正依頼書 H06 段階1・十文字さん「段階1を進める」・ADR-131）
--
-- 週次レポート（社内の Windows・毎週月曜 9:00・前週木曜締めの14日分）の分析 Excel の「新商品」シートの
-- **集計だけ**を持つ。明細・メッセージ欄は読まない・持たない（同じシートの下にある明細の行も読まない）。
CREATE TABLE IF NOT EXISTS newprod_weekly (
  id           INTEGER PRIMARY KEY,
  period_from  TEXT NOT NULL,            -- 週次レポートの期間（ファイル名の日付）
  period_to    TEXT NOT NULL,
  file_name    TEXT,
  rows         TEXT NOT NULL,            -- [{label,count,qty,sales,target_sales,target_qty}]（JSON）
  monthly      TEXT NOT NULL,            -- {label: {months:[YYYY-MM], sales:[], qty:[], target_sales:[], target_qty:[]}}（JSON）
  received_at  TEXT NOT NULL,
  received_by  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_newprod_weekly_period ON newprod_weekly (period_from, period_to);

-- 新商品の売上の登録（G5 で「登録待ち」・人が新商品マスタに足したら「登録済み」）。
-- 1つの案件に週次レポートの商品ラベルを複数結べる（例: フルオーダー目印チャームの競技別）
CREATE TABLE IF NOT EXISTS newprod_reg (
  project_id    TEXT PRIMARY KEY REFERENCES project(id),
  state         TEXT NOT NULL DEFAULT '登録待ち',   -- 登録待ち／登録済み
  labels        TEXT,                               -- 週次レポートの商品ラベル（JSON の配列）
  created_at    TEXT NOT NULL,
  registered_at TEXT,
  registered_by TEXT
);
