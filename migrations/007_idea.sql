-- NEW PRODUCT 007 — アイデア台帳と採点（§4-2・F-1）。
--
-- **4シートを1テーブルにする**（F-1-1）。テーマ別の評価は `theme_id` と
-- rubric のテーマ適合レイヤーで表す。
--
-- **起票のハードルを上げない**（F-1-3）。NOT NULL は `title` と `stage` だけ。
-- 750件＋755件のストックはこの軽さが生んでいる。
--
-- **分からないものを 0 で埋めない**（N-10）。移行分の `origin` が典型で、
-- シートに起票経路の列そのものが無い。**NULL ＋ `origin_note` に理由**を置く。

CREATE TABLE IF NOT EXISTS idea (
  id           TEXT PRIMARY KEY,   -- 短い16進。URL に貼る（#/ideas/2f91）
  title        TEXT NOT NULL,      -- 新商品案。**社内の企画名**（N-6-2 の但し書き）
  summary      TEXT,
  target_scene TEXT,

  -- ここから下は起票に要らない（F-1-3）。採点・案件化のときに埋まる
  design_freedom         INTEGER,  -- 1〜5。NULL = 未入力
  ref_url1 TEXT, ref_url2 TEXT,
  area1 TEXT, area2 TEXT,
  production_feasibility INTEGER,  -- 1〜5。NULL = 未入力。**v2 の減点係数の入力**
  -- F-1-8。通年性は点数をやめて3値（通年／季節／単発）。**枠取りの属性**として使う
  demand_cycle           TEXT,     -- 通年 / 季節 / 単発。NULL = 未入力
  -- F-1-7。高単価化しやすさ（1〜10点）をやめて円建て。NULL = 未入力
  expected_margin_yen    INTEGER,

  -- F-1-4。**必須の新設項目。**ただし移行分は不明なので NULL を許す
  origin       TEXT,
  origin_note  TEXT,               -- NULL でない理由（移行分は「移行時不明」）

  theme_id     TEXT REFERENCES theme(id),
  stage        TEXT NOT NULL DEFAULT '起票',   -- F-1-13。**列で持つ。色で表さない**
  note         TEXT,

  -- 移行の跡。**冪等の鍵**（同じシートの同じ案を2回作らない）
  source_sheet TEXT,               -- NULL = このアプリで起票した
  source_key   TEXT,               -- シート内の自然キー（正規化した商品案名）
  source_row   INTEGER,

  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_idea_stage  ON idea (stage);
CREATE INDEX IF NOT EXISTS idx_idea_theme  ON idea (theme_id);
CREATE INDEX IF NOT EXISTS idx_idea_origin ON idea (origin);
-- **移行の冪等性はここが担保する。**2回流しても重複しない
CREATE UNIQUE INDEX IF NOT EXISTS idx_idea_source
  ON idea (source_sheet, source_key) WHERE source_sheet IS NOT NULL;

-- ── 採点基準の版（F-1-10）──────────────────────────────
-- **既存データは再採点しない。**v1（移行したそのままの点）と v2（新しい軸）を
-- version で分けて併存させる（第8章 ⑦）。**同じ画面に並べるが、合算しない。**
CREATE TABLE IF NOT EXISTS rubric (
  version     TEXT PRIMARY KEY,    -- v1-original / v1-uchiwa / v1-lovot / v1-bukkomi / v2
  label       TEXT NOT NULL,
  generation  INTEGER NOT NULL,    -- 1 or 2。**混ぜないための軸**
  theme_id    TEXT REFERENCES theme(id),   -- v1 はテーマ専用。v2 は共通で NULL
  common_max  REAL,                -- v2 = 70
  theme_max   REAL,                -- v2 = 30
  total_max   REAL,                -- v1-original/uchiwa = 100、v1-lovot = 140
  rank_method TEXT NOT NULL,       -- absolute（v1・シートのまま） / percentile（v2・F-1-9）
  thresholds  TEXT,                -- JSON。absolute は下限点、percentile は上位比率
  feasibility_factors TEXT,        -- JSON。生産方法(1-5) → 係数。v1 は NULL（効いていない）
  margin_bands TEXT,               -- JSON。想定粗利額(円) → 点。v1 は NULL
  active      INTEGER NOT NULL DEFAULT 1,
  note        TEXT,
  created_at  TEXT
);

CREATE TABLE IF NOT EXISTS rubric_axis (
  rubric_version TEXT NOT NULL REFERENCES rubric(version) ON DELETE CASCADE,
  code           TEXT NOT NULL,
  label          TEXT NOT NULL,
  layer          TEXT NOT NULL,    -- common / theme / factor / attribute
  weight         REAL,             -- NULL = 実測できていない（ぶっこみ）
  scale_min      INTEGER,
  scale_max      INTEGER,
  seq            INTEGER NOT NULL,
  note           TEXT,
  PRIMARY KEY (rubric_version, code)
);

-- ── 採点結果 ────────────────────────────────────────────
-- 1つのアイデアは版ごとに1件持つ。**v1 と v2 は別の行。足さない。**
CREATE TABLE IF NOT EXISTS idea_score (
  id             INTEGER PRIMARY KEY,
  idea_id        TEXT NOT NULL REFERENCES idea(id) ON DELETE CASCADE,
  rubric_version TEXT NOT NULL REFERENCES rubric(version),
  axes           TEXT NOT NULL,    -- JSON {軸コード: 素点}
  common_score   REAL,             -- v2 の①共通点 0〜70。v1 は NULL
  theme_fit      REAL,             -- v2 の②テーマ適合点 0〜30。v1 は NULL
  feasibility_factor REAL,         -- v2 の減点係数。v1 は NULL（総合点に入っていない）
  raw_total      REAL,             -- 係数を掛ける前
  total          REAL,             -- v1 はシートの総合点そのまま
  percentile     REAL,             -- テーマ内の上位比率（0=首位）。v1 は NULL
  common_percentile REAL,          -- 共通点だけの横並び（テーマをまたぐ）。v1 は NULL
  rank           TEXT,             -- SS/S/A/B/C
  rank_basis     TEXT,             -- absolute / percentile / sheet
  scored_by      TEXT NOT NULL,    -- human / ai / import
  model          TEXT,             -- AI採点のときだけ（F-1-11）
  scored_at      TEXT NOT NULL,
  source_note    TEXT,
  UNIQUE (idea_id, rubric_version)
);
CREATE INDEX IF NOT EXISTS idx_idea_score_rubric ON idea_score (rubric_version, total DESC);

-- ── 類似候補のキャッシュ（F-1-12）───────────────────────
-- **完全な名寄せはしない。**「似た案が3件あります」を出すだけ。
-- 外部APIを使わず、正規化＋文字2-gram の Dice 係数で決定的に出す。
CREATE TABLE IF NOT EXISTS idea_similar (
  idea_id     TEXT NOT NULL REFERENCES idea(id) ON DELETE CASCADE,
  other_id    TEXT NOT NULL REFERENCES idea(id) ON DELETE CASCADE,
  score       REAL NOT NULL,
  method      TEXT NOT NULL,       -- 例 title-bigram-dice/1
  computed_at TEXT NOT NULL,
  PRIMARY KEY (idea_id, other_id)
);
