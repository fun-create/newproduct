-- 進捗管理シート → アプリの同期（ADR-105・2026-10-18 十文字さん依頼）
--
-- 「スプレッドシート管理からアプリに完全移行するまでは、シートを確認してアプリ側を更新する」。
-- 同期で書いた値の指紋を残し、**次の同期までにアプリ側で直された行は上書きしない**（衝突として報告する）。
CREATE TABLE IF NOT EXISTS sheet_sync (
  kind       TEXT NOT NULL,                 -- task / work_item / project
  local_id   TEXT NOT NULL,                 -- task.id / work_item.id / project.id
  sheet_key  TEXT NOT NULL,                 -- シート側の鍵（案件名＃タスク名＃何番目 など）
  digest     TEXT NOT NULL,                 -- 同期で書いた値の指紋
  synced_at  TEXT NOT NULL,
  PRIMARY KEY (kind, local_id)
);

CREATE TABLE IF NOT EXISTS sheet_sync_run (
  id          INTEGER PRIMARY KEY,
  sheet_date  TEXT NOT NULL,                -- シートを書き出した日
  applied_by  TEXT NOT NULL,
  applied_at  TEXT NOT NULL,
  summary     TEXT NOT NULL                 -- 件数（JSON）
);
