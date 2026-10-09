-- NEW PRODUCT 032 — 他部署への依頼の受け渡しを記録する（FR-117・2026-10-09・ADR-079）。
-- 依頼日（created_at）・受領日（received_at）・完了日（accepted_at）の3つで、どこで止まっているかを見る
ALTER TABLE work_item ADD COLUMN received_at TEXT;
ALTER TABLE work_item ADD COLUMN received_by TEXT;
