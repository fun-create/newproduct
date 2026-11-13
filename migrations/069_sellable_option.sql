-- NEW PRODUCT 069 — 売るものに付ける選択肢（段階3・ADR-150・FR-263）。
-- 使う値を絞り、価格を商品ごとに上書きできる。price が NULL なら定義の追加の価格を使う
CREATE TABLE sellable_option (
  sellable_id TEXT NOT NULL REFERENCES sellable(id) ON DELETE CASCADE,
  def_id      INTEGER NOT NULL REFERENCES option_def(id) ON DELETE CASCADE,
  sort        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (sellable_id, def_id)
);
CREATE TABLE sellable_option_value (
  sellable_id TEXT NOT NULL REFERENCES sellable(id) ON DELETE CASCADE,
  value_id    INTEGER NOT NULL REFERENCES option_value(id) ON DELETE CASCADE,
  enabled     INTEGER NOT NULL DEFAULT 1,
  price       INTEGER,
  PRIMARY KEY (sellable_id, value_id)
);
