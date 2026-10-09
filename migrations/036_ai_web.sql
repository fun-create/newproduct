-- AI のウェブ調査（ADR-088・2026-10-09 十文字さんの選択「両方」「検索＋どのページでも読む」）
--
-- **AI は調べた結果の案を出すだけ。**競合の表・採点には直接入れない。
-- 検索した言葉・開いた URL・検索結果に出た URL を全部残す（trace）。カルテの文が外へ送られていないかを後から見るため。
CREATE TABLE IF NOT EXISTS ai_web (
  id            INTEGER PRIMARY KEY,
  target_type   TEXT NOT NULL,              -- project（競合） / idea（需要・市場）
  target_id     TEXT NOT NULL,
  run_id        INTEGER REFERENCES ai_run(id),
  kind          TEXT NOT NULL,              -- competitor / demand
  body          TEXT NOT NULL,              -- 競合: {"rows": [...]} ／ 需要: {"summary", "findings": [{"point","url"}]}
  unverified    TEXT,
  inputs        TEXT NOT NULL,              -- 渡した項目
  trace         TEXT NOT NULL,              -- {"calls": [{"tool","what"}], "opened": [...], "seen": [...]}
  model         TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  state         TEXT NOT NULL DEFAULT '提案',   -- 提案 / 採用 / 見送り
  decided_by    TEXT,
  decided_at    TEXT,
  decided_note  TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_web_target ON ai_web (target_type, target_id, kind, id);
