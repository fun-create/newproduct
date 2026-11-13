-- NEW PRODUCT 067 — 取り込んだ項目名を選択肢の定義に寄せる（段階3・ADR-148・FR-260〜266）。
-- 店 × 元のファイル × 項目名 → 定義。店での項目名はここに残る（店ごとの出し方・PL-24 の下地）
CREATE TABLE option_alias (
  shop       TEXT NOT NULL REFERENCES shop(code),
  src        TEXT NOT NULL,
  item_name  TEXT NOT NULL,
  def_id     INTEGER NOT NULL REFERENCES option_def(id) ON DELETE CASCADE,
  by_user    TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (shop, src, item_name)
);
CREATE INDEX option_alias_def ON option_alias(def_id);
CREATE INDEX listing_option_item ON listing_option(src, item_name);
-- 定義の変更の履歴
CREATE TABLE option_def_revision (
  id INTEGER PRIMARY KEY, def_id INTEGER NOT NULL, changed_at TEXT NOT NULL, changed_by TEXT NOT NULL, what TEXT NOT NULL, detail TEXT
);
