-- タスクの目安の日付（FR-41・ADR-084・2026-10-09 十文字さんの選択）
--
-- **期限（start_on / due_on）とは別の列。**目安は機械が計算したもので、人が「目安を期限にする」を
-- 押すまで期限にはならない（ADR-012「誰も決めていない期限を入れない」を守る）。
ALTER TABLE task ADD COLUMN plan_start TEXT;
ALTER TABLE task ADD COLUMN plan_due TEXT;
ALTER TABLE task ADD COLUMN plan_at TEXT;      -- 計算した日時
