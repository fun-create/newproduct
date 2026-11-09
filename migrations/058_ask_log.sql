-- ChatWork「FUN 商品開発」へ、承認・確認・依頼・催促を TO 付きで送った記録（2026-11-09 十文字さん・ADR-135）。
-- **送ったものは消さない**（ChatWork 側も取り消せない）。前回いつ誰に送ったかを画面に出し、二度押しを止めるのに使う。
CREATE TABLE IF NOT EXISTS ask_log (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,          -- gate / plan / task / work / improve
  target      TEXT NOT NULL,          -- 案件ID・版ID・タスクID・仕事ID・要望ID
  gate        TEXT,                   -- kind=gate のときのゲート
  purpose     TEXT NOT NULL,          -- 承認 / 確認 / 依頼 / 催促
  to_users    TEXT NOT NULL,          -- JSON: [user_id, ...]
  room        TEXT NOT NULL,
  message_id  TEXT,
  note        TEXT,
  sent_by     TEXT NOT NULL,
  sent_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ask_log_target ON ask_log (kind, target, gate, id);
