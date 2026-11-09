-- 月次レポートを図で見られるように、同じ数字をデータでも持つ（2026-11-09 十文字さん「テキストが多く視覚的に見にくい」・ADR-134）
ALTER TABLE monthly_report ADD COLUMN data TEXT;
