-- NEW PRODUCT 016 — 第3段 原価・調達（ADR-047・2026-10-01）。
--
-- **マスタは持たない。**外注先・仕入先・原材料の一覧は seisan が正。
-- ここにあるのは**案件ごとの検討中のもの**だけ（相見積の候補・試算原価の版）。
-- 採用した候補は seisan に登録する（FR-184。seisan の口ができてから）。
--
-- **金額は円・税抜**（十文字さん「為替は持たない（円で入れる）」）。
-- **分からない値は NULL（未確定）。0 で埋めない**（FR-91）。

-- 相見積の候補（F-8-4・FR-98）。資材も外注も同じ表に置く（比べる軸が同じため）
CREATE TABLE IF NOT EXISTS sourcing_candidate (
  id                 INTEGER PRIMARY KEY,
  project_id         TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  kind               TEXT NOT NULL,          -- 資材 / 外注
  part               TEXT,                   -- 本体 / 付属品 / 専用資材 / その他（資材のとき）
  supplier           TEXT NOT NULL,          -- 仕入先・外注先の名前（**一覧ではなく、この案件で比べた相手**）
  shape_size         TEXT,
  url                TEXT,
  unit_price         REAL,                   -- 円・税抜。NULL = 未確定
  min_lot            REAL,                   -- 最低ロット（MOQ）。NULL = 未確定
  lead_days          REAL,                   -- 発注→入荷（日）。NULL = 未確定
  min_designs        INTEGER,                -- 最低デザイン数（外注のとき・F-8-2）
  sample_ok          INTEGER,                -- 印刷サンプルの提供 1/0。NULL = 未確認
  stability          TEXT,                   -- 安定供給の見込み（自由記述）
  features           TEXT,
  adopted            INTEGER NOT NULL DEFAULT 0,
  not_adopted_reason TEXT,                   -- **採用しなかった理由**（F-8-4。消さずに残す）
  note               TEXT,
  created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sc_project ON sourcing_candidate (project_id);

-- 試算原価の版（F-7-1・F-7-7・FR-88/92）。**前の版は書き換えない**（新しい版を作る）
CREATE TABLE IF NOT EXISTS cost_version (
  id           INTEGER PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  version      INTEGER NOT NULL,
  confidence   TEXT,                         -- 高 / 中 / 低
  price_ex_tax REAL,                         -- 販売価格（税抜）。NULL = 未確定
  tax_rate     REAL,                         -- 作った時点の消費税率（%）の写し
  note         TEXT,
  created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT,
  UNIQUE (project_id, version)
);

CREATE TABLE IF NOT EXISTS cost_line (
  id           INTEGER PRIMARY KEY,
  version_id   INTEGER NOT NULL REFERENCES cost_version(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL DEFAULT 0,
  part         TEXT NOT NULL,                -- 本体 / 付属品 / 専用資材 / その他 / 外注 / 工数
  name         TEXT NOT NULL,                -- 何か（資材名・工程名。お客さまの情報ではない）
  qty          REAL,                         -- 商品1個あたりの使用量。NULL = 未確定
  unit         TEXT,
  unit_price   REAL,                         -- 円・税抜。NULL = 未確定（**写し**。候補を後で直しても変わらない）
  candidate_id INTEGER,                      -- どの候補の単価を写したか（任意）
  note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_cl_version ON cost_line (version_id);
