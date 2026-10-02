-- NEW PRODUCT 020 — ステージの遷移（FR-33）。保留から再開するときに戻る先を持つ。
ALTER TABLE project ADD COLUMN stage_before_hold TEXT;
