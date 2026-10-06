-- NEW PRODUCT 026 — 年間・ライフイベントを設定ページで登録・修正する（2026-10-06・ADR-060）。
-- **消さない。**「使わない」にすると機会カレンダーから外れ、既存の枠の記録には残る。
-- 画面で直した行には印（edited_at）を付け、取り込みツール（tools/import_events.py）は上書きしない。
ALTER TABLE theme ADD COLUMN active    INTEGER NOT NULL DEFAULT 1;
ALTER TABLE theme ADD COLUMN edited_by TEXT;
ALTER TABLE theme ADD COLUMN edited_at TEXT;
