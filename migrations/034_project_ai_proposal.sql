-- AI の案（案件のカルテ向け）。最初は商品名の案（FR-149・ADR-082・2026-10-09）
--
-- **AI は案を出すだけ。**カルテの節には直接書かない。人が選んだ名前だけを「商品名の検討」（F.name）に足す。
CREATE TABLE IF NOT EXISTS project_ai_proposal (
  id            INTEGER PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  run_id        INTEGER REFERENCES ai_run(id),
  kind          TEXT NOT NULL,              -- name
  body          TEXT NOT NULL,              -- {"candidates": [{"name", "why"}]}
  unverified    TEXT,                       -- ["確かめられなかったこと", …]（FR-148）
  inputs        TEXT NOT NULL,              -- 渡した項目と出所（FR-147）
  model         TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  state         TEXT NOT NULL DEFAULT '提案',   -- 提案 / 採用 / 見送り
  decided_by    TEXT,
  decided_at    TEXT,
  decided_note  TEXT
);
CREATE INDEX IF NOT EXISTS idx_project_ai_proposal ON project_ai_proposal (project_id, kind, id);
