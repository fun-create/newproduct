-- NEW PRODUCT 021 — FCTR の週次結果の受け取り（F-9-1〜3・FR-135〜137）。
-- 出どころは Auto GROWTH `/opt/autogrowth/data/export/fctr_weekly.json`（contract_version 1・毎週月曜）。
-- 上流が出すのは**市場性（30点）だけ**。自社側70点は本アプリの担当（FR-138・未着手）。
-- 減衰は**表示のときにかける**（素点はそのまま残す）。恒久スコアとは合算しない。
ALTER TABLE theme_signal ADD COLUMN segment TEXT;        -- funcreate_btoc 等
ALTER TABLE theme_signal ADD COLUMN segment_name TEXT;
ALTER TABLE theme_signal ADD COLUMN score_max REAL;      -- その週に取れた上限（欠測成分を除く）
ALTER TABLE theme_signal ADD COLUMN detail_json TEXT;    -- 内訳（強さ・継続・方向・季節性）と根拠
