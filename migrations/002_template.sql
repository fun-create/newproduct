-- NEW PRODUCT 002 — フローテンプレート（§4-5・F-5-1）。
--
-- **テンプレートは1本。**4箇所の複製をやめる（B-1）。
-- 版を持ち、改訂しても進行中の案件は展開済みのタスクを保持する（F-5-6）。

CREATE TABLE IF NOT EXISTS task_template (
  id                INTEGER PRIMARY KEY,
  flow_type         TEXT NOT NULL REFERENCES flow_type(code),
  template_version  INTEGER NOT NULL DEFAULT 1,
  seq               INTEGER NOT NULL,
  title             TEXT NOT NULL,
  role              TEXT NOT NULL REFERENCES role(code),
  standard_hours    REAL,               -- 実作業h。**予備時間を含まない**（§4-5）
  ai_category       TEXT,
  ai_reduction_rate REAL,               -- 0.0〜1.0
  ai_reduction_hours REAL,
  ai_howto          TEXT,
  source            TEXT,               -- どのファイルの何行目から入れたか
  UNIQUE (flow_type, template_version, seq)
);
CREATE INDEX IF NOT EXISTS idx_tt_flow ON task_template (flow_type, template_version, seq);
