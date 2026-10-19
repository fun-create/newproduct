-- 繰り返しの仕事の自動化の AI 案（FR-201・ADR-116・2026-10-19 十文字さんの選択）
--
-- 本番の目視チェック「AIによる自動化提案機能がほしい」。**AI は案を出すだけ**（FR-145）。
-- 人が「自動化依頼にする」を押したときだけ、自動化依頼（automation_request）の下書きができる（送りはしない）。
CREATE TABLE IF NOT EXISTS routine_ai_proposal (
  id            INTEGER PRIMARY KEY,
  routine_id    INTEGER NOT NULL REFERENCES routine(id) ON DELETE CASCADE,
  run_id        INTEGER REFERENCES ai_run(id),
  verdict       TEXT NOT NULL,         -- できる / 一部できる / 難しい
  how           TEXT,                  -- どう自動化できるか（1〜3文）
  keep_human    TEXT,                  -- 人が残すところ
  impact        TEXT,                  -- 効果の大きさ: 大 / 中 / 小（**分は作らせない**。分×回数は人が自動化依頼で入れる）
  reason        TEXT,
  unverified    TEXT,
  inputs        TEXT,
  model         TEXT,
  state         TEXT NOT NULL DEFAULT '提案',   -- 提案 / 依頼にした / 見送り
  request_id    TEXT,                  -- 依頼にしたときの automation_request.id
  created_at    TEXT NOT NULL,
  decided_by    TEXT, decided_at TEXT, decided_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_routine_ai ON routine_ai_proposal (routine_id, state);
