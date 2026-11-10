-- 発売済みにしたときの「外への連携」の結果（2026-11-10 石原さんの追加修正依頼書 4-4・4-5・ADR-137）。
-- **発売済みのステージとは別に持つ。**連携が失敗しても発売済みは戻さない。失敗したものだけ「もう一度」できる。
CREATE TABLE IF NOT EXISTS launch_link (
  project_id  TEXT NOT NULL REFERENCES project(id),
  kind        TEXT NOT NULL,          -- cip（CIP の登録の確認）/ weekly（週次レポートの新商品マスタ）
  state       TEXT NOT NULL,          -- 済 / 要確認 / 失敗
  summary     TEXT NOT NULL,          -- 1行の結果（画面に出す）
  detail      TEXT,                   -- JSON: 対象ごとの結果
  tries       INTEGER NOT NULL DEFAULT 1,
  checked_at  TEXT NOT NULL,
  checked_by  TEXT NOT NULL,
  PRIMARY KEY (project_id, kind)
);
