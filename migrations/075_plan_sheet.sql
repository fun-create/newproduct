-- NEW PRODUCT 075 — 企画スプシの URL（FR-332 段階1・ADR-163）。
-- 商品ごとの企画は Google スプレッドシートで書いている（石原さん 11/19）。段階1は URL の欄と「URL を入れる」タスクだけ。
-- 内容の毎日の写しは段階2（見本のスプシで対応表を作り、NP 専用の読み取りアカウントができてから）
ALTER TABLE project ADD COLUMN plan_sheet_url TEXT;   -- https://docs.google.com/spreadsheets/d/<id> の形にそろえて持つ

-- アプリが自分で足したタスクの印（plan_sheet ＝ 企画スプシの URL を入れる）。同じ案件に同じ印は1行だけ
ALTER TABLE task ADD COLUMN origin TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_origin ON task (project_id, origin) WHERE origin IS NOT NULL;
