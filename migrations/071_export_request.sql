-- NEW PRODUCT 071 — 書き出しの承認と反映の記録（段階4・ADR-152・FR-294・297・298）。
-- 頼む → 検査 → 承認（差し戻し）→ 書き出す（中身をそのまま保存）→ 管理画面に取り込んだ人が結果を記録
CREATE TABLE export_request (
  id          INTEGER PRIMARY KEY,
  shop        TEXT NOT NULL REFERENCES shop(code),
  kind        TEXT NOT NULL CHECK (kind IN ('新規','更新')),
  listing_ids TEXT NOT NULL,          -- JSON
  reason      TEXT,
  check_json  TEXT NOT NULL,          -- 頼んだときの検査の結果
  state       TEXT NOT NULL CHECK (state IN ('承認待ち','差し戻し','書き出し済み','反映済み','反映失敗')),
  requested_by TEXT NOT NULL, requested_at TEXT NOT NULL,
  decided_by  TEXT, decided_at TEXT, decision_note TEXT,
  applied_by  TEXT, applied_at TEXT, applied_note TEXT
);
CREATE TABLE export_file (
  id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES export_request(id),
  name TEXT NOT NULL, kind TEXT NOT NULL, rows INTEGER NOT NULL, sha256 TEXT NOT NULL, data BLOB NOT NULL
);
-- 承認できる人（2026-11-12 20:57 十文字さん: 新規は十文字さん・渡邊さん・商品開発の責任者、既存商品の更新は十文字さん・渡邊さんだけ）
INSERT OR IGNORE INTO setting (key, value, label, kind, why) VALUES
 ('export.approvers.update', 'masateru,tsubasa', '店への書き出しの承認者（既存商品の更新）', 'text', NULL),
 ('export.approvers.new', 'masateru,tsubasa', '店への書き出しの承認者（新規登録）', 'text',
  '商品開発の責任者のお名前が未定（決まったらログインIDを , で足す）');
