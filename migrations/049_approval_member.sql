-- NEW PRODUCT 049 — 担当区分と承認できる人を分ける（2026-10-23 石原さんの依頼書 §3・ADR-123）。
--
-- それまで role_member（業務ロール）1つで「タスクは誰の業務か」と「どのゲートを承認できるか」を兼ねていた。
-- 増地さんに「管理者」を付けると G1・G5・G6 を承認できる代わりに、石原さんの「管理者」タスクが
-- 増地さんの「自分のやること」にも出た（本番で 81 件）。
-- **role_member ＝ 担当区分**（自分のやること・割り振り）、**approval_member ＝ 承認できる人**（ゲート・書く権限）。
-- いまの承認の資格は落とさない: 承認に使われている区分の割り当てを、そのまま approval_member に写す。
CREATE TABLE IF NOT EXISTS approval_member (
  role_code  TEXT NOT NULL REFERENCES role(code),
  user_id    TEXT NOT NULL,
  granted_by TEXT,
  granted_at TEXT,
  PRIMARY KEY (role_code, user_id)
);
CREATE INDEX IF NOT EXISTS idx_approval_member_user ON approval_member (user_id);

INSERT OR IGNORE INTO approval_member (role_code, user_id, granted_by, granted_at)
  SELECT role_code, user_id, 'migration 049', granted_at FROM role_member
   WHERE role_code IN ('president', 'admin', 'prod', 'devdept');
