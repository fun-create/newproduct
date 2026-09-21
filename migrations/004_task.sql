-- NEW PRODUCT 004 — タスクと案件外の仕事（§4-5・F-5）。
--
-- **一覧は1画面。**「今日のタスク」〜「6日後のタスク」の7枚の複製をやめる（B-2）。
-- **工数は発売月ではなくタスク実施月に積む**（F-3-6）。積む月は due_on の月、
-- 無ければ start_on の月。どちらも無い行は「期限なし」として別に数える。

CREATE TABLE IF NOT EXISTS task (
  id          INTEGER PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  template_id INTEGER REFERENCES task_template(id),
  seq         INTEGER,
  title       TEXT NOT NULL,
  role        TEXT REFERENCES role(code),
  assignee    TEXT,
  start_on    TEXT,
  due_on      TEXT,
  hours       REAL,
  -- 未着手 / 着手 / 完了 / 保留 / **対象外**（§5-6。対象外が無いと永久に未達に見える）
  status      TEXT NOT NULL DEFAULT '未着手',
  done_at     TEXT,
  ai_used     INTEGER NOT NULL DEFAULT 0,
  ai_category TEXT,
  ai_reduction_rate REAL,
  created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_due     ON task (due_on);
CREATE INDEX IF NOT EXISTS idx_task_project ON task (project_id, seq);
CREATE INDEX IF NOT EXISTS idx_task_status  ON task (status);

-- **商品に紐づかない作業**（FBA納品・BtoB整備・旧商品修正・仕組み化）。
-- 実運用台帳397件のうち6フロー外の行がここに入る。
-- **同じ工数勘定に載せないと月次の負荷が実態と合わない。**
-- kind='他部署依頼' は F-11-3。**受け側の完了をもって完了。**送った時点で閉じない。
CREATE TABLE IF NOT EXISTS work_item (
  id         INTEGER PRIMARY KEY,
  kind       TEXT NOT NULL DEFAULT '案件外',   -- 案件外 / 他部署依頼
  title      TEXT NOT NULL,
  category   TEXT,
  dept       TEXT,                              -- 他部署依頼のときの相手
  role       TEXT REFERENCES role(code),
  assignee   TEXT,
  start_on   TEXT,
  due_on     TEXT,
  hours      REAL,
  status     TEXT NOT NULL DEFAULT '未着手',
  done_at    TEXT,
  accepted_at TEXT,                             -- 受け側が完了を返した日時
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wi_due ON work_item (due_on);
