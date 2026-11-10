-- 週次レポート（週次売上分析_*.xlsx）を受け取った記録（2026-11-10 石原さんの追加修正依頼書 2-5・ADR-138）。
-- Windows からの自動送信と画面からの取り込みの両方を残す。**中身は持たない**（集計値は newprod_weekly）。
-- 同じファイル（sha256）は2度取り込まない。同じ期間で中身が違えば訂正版として入れ替える（足し合わせない）。
CREATE TABLE IF NOT EXISTS weekly_feed_log (
  id           INTEGER PRIMARY KEY,
  file_name    TEXT NOT NULL,
  sha256       TEXT NOT NULL,
  period_from  TEXT,
  period_to    TEXT,
  result       TEXT NOT NULL,          -- 取り込み / 訂正版で入れ替え / 取り込み済み（同じ内容） / エラー
  error        TEXT,
  source       TEXT NOT NULL,          -- windows / 画面:<user_id>
  received_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wfl_period ON weekly_feed_log (period_from, period_to, id);
CREATE INDEX IF NOT EXISTS idx_wfl_sha ON weekly_feed_log (sha256);
