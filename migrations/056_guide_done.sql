-- やること（ガイド）で、アプリが確かめられないものを人が「済にする」（2026-11-09 十文字さん・ADR-133）
CREATE TABLE IF NOT EXISTS guide_done (
  key      TEXT PRIMARY KEY,
  done_at  TEXT NOT NULL,
  done_by  TEXT
);
