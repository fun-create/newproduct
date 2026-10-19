-- トレンド（FCTR）の自社側70点の AI 案（FR-200・ADR-115・2026-10-19 十文字さんの選択）
--
-- 本番の目視チェック「AIによる自動判定（案として）入力させたい」。**AI は案を出すだけ**（FR-145）。
-- 人が「採る」（直してもよい）を押したときだけ fctr_self_score に入る。見送った案も消さずに残す。
CREATE TABLE IF NOT EXISTS fctr_ai_proposal (
  id           INTEGER PRIMARY KEY,
  theme_id     TEXT NOT NULL REFERENCES theme(id),
  run_id       INTEGER REFERENCES ai_run(id),
  axes         TEXT NOT NULL,          -- {"fit": 18, "ops": 12, ...}（根拠の無い軸は入れない）
  reasons      TEXT NOT NULL,          -- {"fit": "…", ...}
  unverified   TEXT,                   -- ["…", ...]
  inputs       TEXT,                   -- 渡した項目と出所
  model        TEXT,
  state        TEXT NOT NULL DEFAULT '提案',   -- 提案 / 採用 / 見送り
  created_at   TEXT NOT NULL,
  decided_by   TEXT, decided_at TEXT, decided_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_fctr_ai_theme ON fctr_ai_proposal (theme_id, state);
