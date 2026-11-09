-- 発売後の仕事を新商品の案件から外す（2026-11-09 修正依頼書 H04・H05・H07・ADR-128）
--
-- 1) ゲートの記録に「移行補正」を区別する印。発売済みの案件で記録の無いゲートを通過済みに整えるとき、
--    **過去の個人の承認と混ぜない**（承認者・承認日を推測して埋めない）。
ALTER TABLE gate_review ADD COLUMN kind TEXT NOT NULL DEFAULT 'review';   -- review（人の判定）／ migration（移行補正）

-- 2) 発売後の仕事（生産確認・モール出品・FBA化の検討）は案件外の仕事として作る。元の案件へ戻れるように案件を持つ。
--    origin は何の仕事か（plc・mall・fba）。**同じ案件・同じ origin は1件だけ**（G5 の通し直し・補正・再計算で二重に作らない）
ALTER TABLE work_item ADD COLUMN project_id TEXT REFERENCES project(id);
ALTER TABLE work_item ADD COLUMN origin TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_work_item_origin ON work_item (project_id, origin) WHERE origin IS NOT NULL;

-- 3) 発売後の生産確認の要否と時期（H07）。NULL は開発タイプの既定。
--    'none'＝不要／'L>2m' のような式（発売日から）／'YYYY-MM-DD'＝期限日の指定
ALTER TABLE project ADD COLUMN plc_rule TEXT;
