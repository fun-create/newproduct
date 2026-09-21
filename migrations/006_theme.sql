-- NEW PRODUCT 006 — テーマ（§4-2）。
--
-- **`theme` は2つの役割を兼ねる。**
--   ① 採点の「テーマ適合点」がどのテーマに対するものか（kind='評価テーマ'）
--   ② 機会カレンダー（年間イベント・ライフイベント）の暦（kind='年間イベント'/'ライフイベント'）
--
-- **①だけを入れてある。**②（F-2 機会カレンダー）はまだ取り込んでいない。
-- 元表 `01_進捗管理商品開発__イベント.tsv` は、行によって
-- ライフイベントの列位置が 8列目と10列目にずれており、
-- **機械的に読むと静かに取り違える。**半分だけ読んで入れるより、
-- 0件のまま「未取込」と言うほうがよい（N-10）。
--
-- `theme_score` の式は実測で確定している（採点12行すべて誤差0）:
--   総合 = 記念品購買意欲×2 ＋ 写真親和性×1 ＋ 発生頻度×2（満点40）
-- 式は列で持たず、`theme_score.total` に**計算済みの値**を入れる。

CREATE TABLE IF NOT EXISTS theme (
  id       TEXT PRIMARY KEY,
  label    TEXT NOT NULL,
  kind     TEXT NOT NULL,          -- 評価テーマ / 年間イベント / ライフイベント
  month    INTEGER,                -- 暦のときだけ。NULL = 月が決まっていない
  day      TEXT,                   -- 「1月上旬」「第2月曜日」等があるので TEXT
  sort     INTEGER NOT NULL DEFAULT 0,
  note     TEXT
);
CREATE INDEX IF NOT EXISTS idx_theme_kind ON theme (kind, sort);

-- ライフイベント採点（実測で式が確定している恒久スコア）。
CREATE TABLE IF NOT EXISTS theme_score (
  theme_id    TEXT PRIMARY KEY REFERENCES theme(id) ON DELETE CASCADE,
  gift_intent INTEGER,             -- 記念品購買意欲
  photo_fit   INTEGER,             -- 写真親和性
  frequency   INTEGER,             -- 発生頻度（20〜50代女性）
  total       INTEGER,             -- 購買意欲×2 ＋ 写真親和性×1 ＋ 発生頻度×2（満点40）
  priority    INTEGER,
  source      TEXT NOT NULL,
  captured_at TEXT
);

-- **FCTR 由来の時限スコア（§4-2・Q2）。**
-- 恒久スコア（idea_score）と**合算しない。**合算すると
-- 「一過性の高得点」と「恒久的に筋が良い」が区別できなくなる。
-- いまは0件。AutoGrowth からの取込（F-9）は未実装。
CREATE TABLE IF NOT EXISTS theme_signal (
  id            INTEGER PRIMARY KEY,
  theme_id      TEXT NOT NULL REFERENCES theme(id) ON DELETE CASCADE,
  week_id       TEXT NOT NULL,     -- 2026-W38
  market_score  REAL,              -- 0〜30
  weeks_present INTEGER,           -- 何週連続で出ているか
  novelty       TEXT,
  cycle         TEXT,
  source        TEXT NOT NULL,     -- **出どころ必須**（N-10）
  fetched_at    TEXT NOT NULL,
  UNIQUE (theme_id, week_id, source)
);
CREATE INDEX IF NOT EXISTS idx_theme_signal_week ON theme_signal (week_id);
