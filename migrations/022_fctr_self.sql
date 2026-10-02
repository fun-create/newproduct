-- NEW PRODUCT 022 — FCTR テーマの自社側70点（F-9-4・FR-138）。
-- 上流（Auto GROWTH）は市場性30点だけ。自社側（商品相性25／製造運用20／発売速度15／利益性10）はここで持つ。
-- **点の付け方の基準は種データに入れない**（商品開発部が付ける）。未採点は NULL（0 と書かない）。
CREATE TABLE IF NOT EXISTS fctr_self_score (
  theme_id   TEXT PRIMARY KEY REFERENCES theme(id) ON DELETE CASCADE,
  fit        REAL,      -- 商品相性（0〜25）
  ops        REAL,      -- 製造運用（0〜20）
  speed      REAL,      -- 発売速度（0〜15）
  profit     REAL,      -- 利益性（0〜10）
  note       TEXT,
  updated_by TEXT, updated_at TEXT
);
