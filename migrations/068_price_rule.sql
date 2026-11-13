-- NEW PRODUCT 068 — 価格の決まり（数量で単価が変わる・段階3・ADR-149・FR-265・PL-25）。
-- 店は「15%引き（3個購入）」のような選択肢（マイナスの価格）で表している。選択肢の定義とは別に持つ
CREATE TABLE price_rule (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL CHECK (kind IN ('数量で割引')),
  tiers_json  TEXT NOT NULL,        -- [{"min":1,"max":1,"rate":0}, {"min":5,"max":19,"rate":25}, …]（率は %。max が null は「以上」）
  note        TEXT,
  created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE price_rule_alias (
  shop TEXT NOT NULL REFERENCES shop(code), src TEXT NOT NULL, item_name TEXT NOT NULL,
  rule_id INTEGER NOT NULL REFERENCES price_rule(id) ON DELETE CASCADE,
  by_user TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (shop, src, item_name)
);
CREATE TABLE price_rule_revision (
  id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL, changed_at TEXT NOT NULL, changed_by TEXT NOT NULL, what TEXT NOT NULL, detail TEXT
);

-- 出品ごとにどの段の表を使っているか（11-13 グッズ本店の「単価割引率」は出品によって3種類の表だった）
CREATE TABLE price_rule_listing (
  listing_id INTEGER NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
  rule_id    INTEGER NOT NULL REFERENCES price_rule(id) ON DELETE CASCADE,
  shop TEXT NOT NULL, src TEXT NOT NULL, item_name TEXT NOT NULL,
  PRIMARY KEY (listing_id, src, item_name)
);
CREATE INDEX price_rule_listing_rule ON price_rule_listing(rule_id);
