-- NEW PRODUCT 005 — ゲート（§4-6・F-6）。
--
-- `required_items` を**データで持つ**ことで、画面は「いま何が欠けているか」を
-- 機械的に出せる（F-6-2）。人が思い出す必要をなくす。
--
-- `approver_role` は **業務ロール**（role.code）の配列。
-- **アプリ権限（admin/user）で決めない**（§10-2 ②）。
-- 2値の admin/user に承認を載せると、承認させたいだけの社長を admin にするか、
-- 全ての管理者に社長の承認を許すかのどちらかになる。どちらも誤り。
--
-- `applies_to_flow_types` は **このゲートを通す flow_type の配列**。
-- ここに無いフローでは、そのゲートは「未達」ではなく **`対象外`**（F-6-4・§5-6）。

CREATE TABLE IF NOT EXISTS gate_def (
  gate                  TEXT PRIMARY KEY,   -- G0〜G6
  seq                   INTEGER NOT NULL,
  name                  TEXT NOT NULL,
  approver_role         TEXT NOT NULL,      -- JSON 配列
  required_items        TEXT NOT NULL,      -- JSON 配列
  applies_to_flow_types TEXT NOT NULL,      -- JSON 配列
  note                  TEXT
);

-- 判定の履歴。**差し戻しも残す**（F-6-3）。最後の1件がいまの状態。
CREATE TABLE IF NOT EXISTS gate_review (
  id            INTEGER PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  gate          TEXT NOT NULL REFERENCES gate_def(gate),
  result        TEXT NOT NULL,              -- 通過 / 差戻し / 保留 / 中止
  approved_by   TEXT,
  approved_role TEXT,                       -- どの業務ロールとして通したか
  approved_at   TEXT,
  comment       TEXT,
  reason_code   TEXT,                       -- 保留・中止のときの選択式コード
  missing_items TEXT                        -- 判定時点で欠けていたもの（JSON）
);
CREATE INDEX IF NOT EXISTS idx_gr_project ON gate_review (project_id, gate, id DESC);

-- 選択式のコード表（F-6-5）。**理由が残らない現状（B-14）を塞ぐ。**
-- 自由記述だけにすると、あとから「なぜ止まったか」を数えられない。
CREATE TABLE IF NOT EXISTS hold_reason (
  code  TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  sort  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS abort_reason (
  code  TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  sort  INTEGER NOT NULL,
  kind  TEXT NOT NULL DEFAULT '中止'        -- 中止 / 発売後の撤退
);
