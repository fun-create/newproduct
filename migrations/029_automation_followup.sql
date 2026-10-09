-- NEW PRODUCT 029 — 実装済みの自動化依頼で、作業が実際に減ったかを後追いする（FR-169・2026-10-09・ADR-071）。
-- 自動化したのに作業が残っていたら、それは実装が足りていない。**後の手間を数で残し、前と並べる。**
-- 分からない値は NULL（未計測）。0 で埋めない（N-10）
ALTER TABLE automation_request ADD COLUMN done_at TEXT;              -- 実装済にした日時
ALTER TABLE automation_request ADD COLUMN after_minutes_each REAL;   -- 実装後の1回あたりの分（0 ＝ 手作業が無くなった）
ALTER TABLE automation_request ADD COLUMN after_times_per_month REAL;
ALTER TABLE automation_request ADD COLUMN after_hours_per_month REAL;
ALTER TABLE automation_request ADD COLUMN after_note TEXT;
ALTER TABLE automation_request ADD COLUMN after_checked_by TEXT;
ALTER TABLE automation_request ADD COLUMN after_checked_at TEXT;
