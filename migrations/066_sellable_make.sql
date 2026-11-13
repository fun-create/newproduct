-- NEW PRODUCT 066 — 売るものと「作るもの」の結び（段階2・ADR-147・FR-280 の一部）。
-- 選択肢の値の組ごとに作るもののコードと数量を持つ（PL-50）。段階2では「選択肢なし（combo=''）→ 1つのコード」だけを作る
CREATE TABLE sellable_make (
  id           INTEGER PRIMARY KEY,
  sellable_id  TEXT NOT NULL REFERENCES sellable(id) ON DELETE CASCADE,
  combo        TEXT NOT NULL DEFAULT '',   -- 選択肢の値の組（段階3）。'' は選択肢なし
  product_code TEXT NOT NULL,              -- CIP の共通商品コード
  qty          INTEGER NOT NULL DEFAULT 1,
  UNIQUE (sellable_id, combo, product_code)
);
CREATE INDEX listing_sellable ON listing(sellable_id);
CREATE INDEX sellable_make_code ON sellable_make(product_code);
