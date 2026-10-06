-- NEW PRODUCT 027 — 標準タスクのひな形を設定ページで改訂する（2026-10-06・ADR-061）。
-- **版1は種データ専用**（起動のたびに seed が書き戻す）。画面で作る版は2から。
-- 開発タイプごとに「使用中の版」を持ち、案件を起こすときはその版から展開する。
-- **起こし済みの案件のタスクは変えない**（展開済みなら何もしない・F-5-6 のまま）。
ALTER TABLE flow_type ADD COLUMN active_template_version INTEGER NOT NULL DEFAULT 1;
-- 工数ポイント係数・ひな形の有無を画面で決めたら印を付ける。seed はその行を書き戻さない
ALTER TABLE flow_type ADD COLUMN decided_by TEXT;
ALTER TABLE flow_type ADD COLUMN decided_at TEXT;

CREATE TABLE IF NOT EXISTS template_version (
  flow_type    TEXT NOT NULL REFERENCES flow_type(code),
  version      INTEGER NOT NULL,
  state        TEXT NOT NULL,          -- 下書き / 使用中 / 過去
  note         TEXT,
  created_by   TEXT, created_at TEXT,
  activated_by TEXT, activated_at TEXT,
  PRIMARY KEY (flow_type, version)
);
