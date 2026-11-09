-- 発売後の改善要望（2026-11-09 十文字さん「新商品販売開始後の改善要望を受け付ける口・リスト化と優先順位・担当部署（担当者）・
-- 期限・タスク化（カレンダーアプリのタスクに入れる）。後からでも編集できる」・ADR-132）
--
-- タスク化は案件外の仕事（work_item）を1件作る。担当者と期限が入っていれば、今の仕組み（FR-121）でカレンダーに出る。
-- 消さない（見送りは状態で残す）。直した履歴は監査ログ（improve.*）に残る
CREATE TABLE IF NOT EXISTS improve_request (
  id           INTEGER PRIMARY KEY,
  title        TEXT NOT NULL,                    -- 何を直すか（一言）
  detail       TEXT,                             -- くわしく・決めたこと
  source       TEXT,                             -- 受付元（外部署から・お客様の問合せ・社内 など）
  project_id   TEXT REFERENCES project(id),      -- 関係する商品（案件）。無くてもよい
  priority     TEXT NOT NULL DEFAULT '未設定',    -- 高／中／低／未設定
  status       TEXT NOT NULL DEFAULT '受付',      -- 受付／検討中／対応中／完了／見送り
  role         TEXT REFERENCES role(code),       -- 担当部署（担当区分）
  assignee     TEXT,                             -- 担当者（共通ログインID）
  due_on       TEXT,                             -- 期限
  work_item_id INTEGER REFERENCES work_item(id), -- タスクにしたときの案件外の仕事
  received_on  TEXT NOT NULL,                    -- 受付日
  received_by  TEXT,
  updated_at   TEXT, updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_improve_status ON improve_request (status, priority);
CREATE INDEX IF NOT EXISTS idx_improve_project ON improve_request (project_id);
