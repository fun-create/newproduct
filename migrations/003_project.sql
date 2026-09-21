-- NEW PRODUCT 003 — 案件（§4-4・F-4）。
--
-- **1案件1レコード。**1商品1ファイルをやめる（B-3）。
-- **商品名を持たない列で表示する**（N-6-2）。画面に出すのは cat1/cat2/cat3 + size。
-- `internal_name` は社内の呼び名であって顧客データではない（要件定義 N-6-2 の但し書き）。

CREATE TABLE IF NOT EXISTS project (
  id            TEXT PRIMARY KEY,       -- 短い16進。URL に貼る（#/projects/2f91）
  code          TEXT,
  internal_name TEXT,                   -- 社内呼称。**商品名ではない**
  flow_type     TEXT REFERENCES flow_type(code),
  area          TEXT,
  launch_date   TEXT,
  occasion      TEXT,                   -- なぜその日か
  idea_id       TEXT,                   -- 第1段で idea 表ができたら外部キーにする
  summary       TEXT,
  owner         TEXT,
  stage         TEXT NOT NULL DEFAULT '起票',
  -- F-4-9。**既定は false。**ページリニューアルを黙って新商品売上に混ぜない
  revenue_counted INTEGER NOT NULL DEFAULT 0,
  revenue_basis   TEXT,                 -- 全額 / 増分 / NULL
  -- R-2。併存中どちらが正か。**drive が正なら編集させない**（§4-4）
  source_of_truth TEXT NOT NULL DEFAULT 'app',
  cat1 TEXT, cat2 TEXT, cat3 TEXT, size TEXT,
  effort_point  REAL,                   -- NULL = 未確定（フロー係数が未確定の場合を含む）
  created_at TEXT, created_by TEXT,
  updated_at TEXT, updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_project_stage  ON project (stage);
CREATE INDEX IF NOT EXISTS idx_project_launch ON project (launch_date);
CREATE INDEX IF NOT EXISTS idx_project_flow   ON project (flow_type);

-- カルテの節。**17枚を17テーブルにしない**（§4-4）。
-- section_key は "C.diff" のような2段。頭文字が §5-4 の6節に対応する。
-- **現行の壊れた採番（⑤が2枚・⑥⑦欠番）は保存しない**（B-11）。
CREATE TABLE IF NOT EXISTS project_section (
  project_id  TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  section_key TEXT NOT NULL,
  body        TEXT,
  updated_by  TEXT,
  updated_at  TEXT,
  PRIMARY KEY (project_id, section_key)
);

-- ゲートの必須項目のうち、**このアプリがまだ持っていないデータ**（試算原価・年間目標など、
-- 第3段/第4段で入る）を人が明示的に確認したという記録。
-- **「無い」を「有る」に化けさせないために、誰がいつ確認したかを必ず持つ。**
CREATE TABLE IF NOT EXISTS project_check (
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  item_key   TEXT NOT NULL,
  done       INTEGER NOT NULL DEFAULT 0,
  note       TEXT,
  updated_by TEXT,
  updated_at TEXT,
  PRIMARY KEY (project_id, item_key)
);

-- バリエーション展開（スキンシールの本体モデル等）。**40本の複製を作らない**（F-4-5）
CREATE TABLE IF NOT EXISTS project_variant (
  id           INTEGER PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  label        TEXT NOT NULL,
  spec         TEXT,
  state        TEXT NOT NULL DEFAULT '未対応',
  launch_date  TEXT,
  product_code TEXT,
  UNIQUE (project_id, label)
);

-- 改訂履歴（F-4-6）。誰が・いつ・何を
CREATE TABLE IF NOT EXISTS project_revision (
  id         INTEGER PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  changed_at TEXT NOT NULL,
  changed_by TEXT,
  what       TEXT NOT NULL,
  detail     TEXT
);
CREATE INDEX IF NOT EXISTS idx_prev_project ON project_revision (project_id, changed_at DESC);
