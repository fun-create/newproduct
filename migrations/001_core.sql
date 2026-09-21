-- NEW PRODUCT 001 — 軸（全体設計書 §4-1）と監査。
--
-- **前に進むだけ。**戻す方向は用意しない（戻したい状況ではバックアップのほうが早い）。
-- secretary の作法にならい、migrations/*.sql を名前順に流す。

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── 開発タイプ ────────────────────────────────────────
-- effort_point は **NULL を許す**。⑤資材リニューアルは年間プランで1件も
-- 使われておらず係数が実測できない。**推測で埋めない**（0 で埋めると
-- 「工数ゼロの安いフロー」に見えてしまい、逆に危ない）。
CREATE TABLE IF NOT EXISTS flow_type (
  code              TEXT PRIMARY KEY,
  seq               INTEGER NOT NULL,
  label             TEXT NOT NULL,
  tsv_label         TEXT,               -- 種データTSV の「開発フロー」列の表記
  effort_point      REAL,               -- NULL = 未確定
  effort_point_note TEXT,
  has_template      INTEGER NOT NULL DEFAULT 1,
  note              TEXT
);

-- ── 業務ロール（§10-2 ②）─────────────────────────────
-- **アプリ権限（admin/user）とは別軸。**ゲートの承認資格はこちらで決める。
-- external=1 は「他部署（AI削減試算の対象外）」。自部署の工数と足さない。
CREATE TABLE IF NOT EXISTS role (
  code     TEXT PRIMARY KEY,
  label    TEXT NOT NULL,
  sort     INTEGER NOT NULL,
  external INTEGER NOT NULL DEFAULT 0,
  note     TEXT
);

CREATE TABLE IF NOT EXISTS role_member (
  role_code  TEXT NOT NULL REFERENCES role(code),
  user_id    TEXT NOT NULL,
  granted_by TEXT,
  granted_at TEXT,
  PRIMARY KEY (role_code, user_id)
);
CREATE INDEX IF NOT EXISTS idx_role_member_user ON role_member (user_id);

-- ── 監査（§4-9）──────────────────────────────────────
-- **全操作を残す。**誰が・いつ・何を。
CREATE TABLE IF NOT EXISTS audit (
  id      INTEGER PRIMARY KEY,
  at      TEXT NOT NULL,
  user_id TEXT,
  action  TEXT NOT NULL,
  target  TEXT,
  detail  TEXT,
  ip      TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit (at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit (target);
