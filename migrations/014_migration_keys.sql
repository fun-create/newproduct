-- NEW PRODUCT 014 — 移行の跡（2026-09-26 十文字さんの選択A「使える状態にしてから第3段」）。
--
-- 進行中の案件（進捗管理シート 27案件・396タスク）と 年間プラン2026年度（26枠）を
-- 取り込むにあたり、**どの行がどこから来たかを残す。**`idea` と `theme` と同じ作法。
--
-- **冪等の鍵でもある。**2回流しても重複しない。手で直した行を種で戻さない。
-- `source_sheet IS NULL` ＝ このアプリで起こしたもの。

ALTER TABLE project   ADD COLUMN source_sheet TEXT;
ALTER TABLE project   ADD COLUMN source_key   TEXT;
ALTER TABLE project   ADD COLUMN source_row   INTEGER;
ALTER TABLE work_item ADD COLUMN source_sheet TEXT;
ALTER TABLE work_item ADD COLUMN source_key   TEXT;
ALTER TABLE work_item ADD COLUMN source_row   INTEGER;
ALTER TABLE task      ADD COLUMN source_row   INTEGER;
ALTER TABLE plan_slot ADD COLUMN source_sheet TEXT;
ALTER TABLE plan_slot ADD COLUMN source_key   TEXT;
ALTER TABLE plan_slot ADD COLUMN source_row   INTEGER;

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_source
  ON project (source_sheet, source_key) WHERE source_sheet IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_work_item_source
  ON work_item (source_sheet, source_key) WHERE source_sheet IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_slot_source
  ON plan_slot (source_sheet, source_key) WHERE source_sheet IS NOT NULL;
