-- 原価・調達の AI 案（FR-202・ADR-117・2026-10-20 十文字さんの選択）
--
-- 本番の目視チェック「CIPと連携してAIで案として入力して欲しい」。AI は CIP の原材料マスタの一覧から、
-- カルテの資材・作り方の文に合うものを選ぶだけ。**単価・仕入先・リードタイムは CIP の値**（AI に作らせない）。
-- **使用量は作らせない**（人が試算原価の行で入れる）。人が「候補に足す」を押したときだけ相見積の候補になる。
CREATE TABLE IF NOT EXISTS cost_ai_proposal (
  id           INTEGER PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES project(id),
  run_id       INTEGER REFERENCES ai_run(id),
  items        TEXT NOT NULL,          -- [{code,name,category,unit,unit_price,supplier,lead_days,lot_size,part,why,state,candidate_id}]
  unverified   TEXT,
  inputs       TEXT,
  model        TEXT,
  as_of        TEXT,                   -- CIP のマスタを読んだ日時
  state        TEXT NOT NULL DEFAULT '提案',   -- 提案 / 済み / 見送り
  created_at   TEXT NOT NULL,
  decided_by   TEXT, decided_at TEXT, decided_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_cost_ai_project ON cost_ai_proposal (project_id, state);
