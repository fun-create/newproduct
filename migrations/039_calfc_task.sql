-- カレンダー（calfc）へ送ったタスク（FR-121・ADR-100・2026-10-17）
--
-- 十文字さんの決定（10-13）: 予定ではなく**タスク**として送る。範囲は期限と担当の入ったタスク全部。
-- カレンダー側は `ext_ref` で冪等（同じ ext_ref は上書き）。こちらは**送った中身の指紋**を覚え、
-- 変わったものだけ送り直し、送る対象から外れたもの（対象外・中止・期限や担当が消えた）は消す。
CREATE TABLE IF NOT EXISTS calfc_task_sent (
  ext_ref       TEXT PRIMARY KEY,           -- newproduct:task:<id> ／ newproduct:work:<id>
  kind          TEXT NOT NULL,              -- task / work
  local_id      INTEGER NOT NULL,
  digest        TEXT NOT NULL,              -- 送った中身（題・期限・状態・担当）の指紋
  calfc_task_id TEXT,                       -- カレンダー側の task_id（返事にあれば）
  sent_at       TEXT NOT NULL,
  state         TEXT NOT NULL DEFAULT '送った'   -- 送った / 消した
);
