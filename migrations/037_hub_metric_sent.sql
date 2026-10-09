-- HUB（Auto GROWTH の metrics）へ送った点（FR-123・ADR-091・2026-10-09）
--
-- Auto GROWTH の `POST /api/metrics` は**同じ点を置き換えない**（送るたびに行が増える）。
-- こちらで送った点を覚え、同じ (metric, dims, captured_at) は二度送らない。
CREATE TABLE IF NOT EXISTS hub_metric_sent (
  metric      TEXT NOT NULL,
  dims        TEXT NOT NULL,               -- 送ったとおりの JSON（キーの順・空白まで同じ）
  captured_at TEXT NOT NULL,
  value       REAL NOT NULL,
  sent_at     TEXT NOT NULL,
  PRIMARY KEY (metric, dims, captured_at)
);
