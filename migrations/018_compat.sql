-- NEW PRODUCT 018 — 対応確認の定型チェック（FR-102・F-8-7）。
-- 2026-10-01 十文字さん「商品開発部に決めてもらう」。**項目の中身は種データに入れない**（発明しない）。
-- ひな形（例:「スマホケース」→ iPhone／Android…）は商品開発部・管理者が画面で作る。

CREATE TABLE IF NOT EXISTS compat_template (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,      -- 商品の種類（例: スマホケース・PC 用品）
  items_json TEXT NOT NULL,             -- 確認する項目の並び ["iPhone", "Android", …]
  active     INTEGER NOT NULL DEFAULT 1,
  updated_by TEXT, updated_at TEXT
);

-- 案件ごとの結果。**項目名を写して持つ**（ひな形を後で直しても、確認した記録は変わらない）
CREATE TABLE IF NOT EXISTS compat_check (
  id          INTEGER PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  template_id INTEGER,
  item        TEXT NOT NULL,
  result      TEXT NOT NULL DEFAULT '未確認',   -- 未確認 / 確認済 / 不可
  note        TEXT,
  updated_by  TEXT, updated_at TEXT,
  UNIQUE (project_id, item)
);
