-- NEW PRODUCT 050 — 発売予定日を変えたらタスクの日程を計算し直す（FR-225・ADR-124・石原さんの依頼書 §2）。
--
-- rule_start / rule_due … そのタスクの計算ルール（app/schedule_rules.py の書き方。例 L-30・L>2m+4）。
--   案件を起こしたときにひな形の名前から決めて**タスクに持つ**（テンプレートを改訂しても既存の案件のルールは変わらない）。
--   移行したタスクは空。初めて計算し直すときに、名前から決めて書く
-- rule_src … sheet（シートの数式そのまま）／ proposal（アプリの提案）
-- date_locked … 1 なら発売日を変えても日程を動かさない（人が手で直した日程）
ALTER TABLE task ADD COLUMN rule_start TEXT;
ALTER TABLE task ADD COLUMN rule_due   TEXT;
ALTER TABLE task ADD COLUMN rule_src   TEXT;
ALTER TABLE task ADD COLUMN date_locked INTEGER NOT NULL DEFAULT 0;

-- 反映の控え（元に戻す用）。rows は [{id, start_on, due_on, set_start, set_due}]
CREATE TABLE IF NOT EXISTS schedule_undo (
  id          INTEGER PRIMARY KEY,
  project_id  TEXT NOT NULL,
  launch_from TEXT,
  launch_to   TEXT,
  rows        TEXT NOT NULL,
  applied_by  TEXT NOT NULL,
  applied_at  TEXT NOT NULL,
  undone_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedule_undo_project ON schedule_undo (project_id, id DESC);
