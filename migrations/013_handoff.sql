-- NEW PRODUCT 013 — 渡した記録（F-15-6 ／ FR-171〜FR-174）。2026-09-24 十文字さん選択C。
--
-- **「渡した」を1行の自由記入で終わらせない。**いつ・どこへ・何を送ったかを残す。
-- ChatWork は**取り消せない。**送った証跡が無いと、二重送信したかも分からない。

ALTER TABLE automation_request ADD COLUMN sent_at      TEXT;
ALTER TABLE automation_request ADD COLUMN sent_room    TEXT;
ALTER TABLE automation_request ADD COLUMN sent_by      TEXT;
ALTER TABLE automation_request ADD COLUMN sent_message_id TEXT;
ALTER TABLE automation_request ADD COLUMN sent_count   INTEGER NOT NULL DEFAULT 0;
