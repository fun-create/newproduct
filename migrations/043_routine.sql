-- 繰り返しの仕事（ルーティン・ADR-108・2026-10-18 十文字さんの選択）
--
-- シート「ルーティンワーク」（イヤリー・マンスリー・ウィークリー・デイリー）と「進捗共有」（毎週の共有・FUN-Meeting）の置き場。
-- 毎年・毎月・毎週のものは、時期が近づいたら「案件外の仕事」（work_item）を1件起こす。毎日のものはタスクにしない（一覧で見るだけ）。
CREATE TABLE IF NOT EXISTS routine (
  id         INTEGER PRIMARY KEY,
  title      TEXT NOT NULL,
  cadence    TEXT NOT NULL,                 -- yearly / monthly / weekly / daily
  months     TEXT,                          -- yearly: 「4,8,12」
  day        TEXT,                          -- yearly・monthly: 1〜31 か last（月末）
  weekdays   TEXT,                          -- weekly: 0=月 … 6=日（「0,4」）
  timing     TEXT,                          -- daily: 出勤時 / 随時 / 退勤10分前 などの原文
  reason     TEXT,                          -- 実行理由（シートの原文）
  manual     TEXT,                          -- マニュアル・記入シート（URL か原文）
  role       TEXT REFERENCES role(code),
  assignee   TEXT,
  hours      REAL,
  lead_days  INTEGER,                       -- 何日前に起こすか（空なら既定: 週6・月14・年30）
  active     INTEGER NOT NULL DEFAULT 1,
  note       TEXT,                          -- 時期が決まっていない など
  source_row INTEGER,                       -- 取り込み元の行
  edited_by  TEXT, edited_at TEXT, created_at TEXT
);

-- 起こした回（同じ回を二度起こさない）
CREATE TABLE IF NOT EXISTS routine_occurrence (
  routine_id   INTEGER NOT NULL REFERENCES routine(id) ON DELETE CASCADE,
  occurs_on    TEXT NOT NULL,               -- その回の期限（YYYY-MM-DD）
  work_item_id INTEGER REFERENCES work_item(id),
  created_at   TEXT NOT NULL,
  PRIMARY KEY (routine_id, occurs_on)
);
