-- AI採点の案（FR-74・FR-145・FR-147・FR-148 ／ ADR-081・2026-10-09）
--
-- **AI は案を出すだけ。**点（idea_score）には直接入れない。人が「採用」を押したときに
-- 初めて v2 の点になる。想定粗利額と生産方法は人が入れる項目なので、AI には付けさせない。

-- AIを1回走らせた記録。**走ったまま残った行は起動時に「中断」に倒す**
CREATE TABLE IF NOT EXISTS ai_run (
  id            INTEGER PRIMARY KEY,
  job           TEXT NOT NULL,              -- AI予算の job 名（newproduct-text）
  kind          TEXT NOT NULL,              -- idea_score
  stage         TEXT NOT NULL,              -- 待ち / 実行中 / 完了 / 失敗 / 中断
  target_json   TEXT NOT NULL,              -- 対象のアイデアID
  requested_by  TEXT NOT NULL,
  requested_at  TEXT NOT NULL,
  finished_at   TEXT,
  model         TEXT,                       -- 実際に答えたモデル（claude の返事から）
  cost_usd      REAL,                       -- claude が返した額（定額プランでは目安）
  cost_real     INTEGER,                    -- 1=請求される額 / 0=目安
  n_ok          INTEGER NOT NULL DEFAULT 0,
  n_ng          INTEGER NOT NULL DEFAULT 0,
  error_kind    TEXT,
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_run_stage ON ai_run (stage, requested_at);

CREATE TABLE IF NOT EXISTS idea_ai_proposal (
  id             INTEGER PRIMARY KEY,
  idea_id        TEXT NOT NULL REFERENCES idea(id) ON DELETE CASCADE,
  run_id         INTEGER REFERENCES ai_run(id),
  rubric_version TEXT NOT NULL,
  axes           TEXT NOT NULL,             -- {軸: 1〜10}
  reasons        TEXT NOT NULL,             -- {軸: 根拠の一言}
  unverified     TEXT,                      -- ["確かめられなかったこと", …]（FR-148）
  inputs         TEXT NOT NULL,             -- 渡した項目と出所（FR-147）
  model          TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  state          TEXT NOT NULL DEFAULT '提案',   -- 提案 / 採用 / 見送り
  decided_by     TEXT,
  decided_at     TEXT,
  decided_note   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_proposal_idea ON idea_ai_proposal (idea_id, id);
CREATE INDEX IF NOT EXISTS idx_ai_proposal_state ON idea_ai_proposal (state);
