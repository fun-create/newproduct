-- NEW PRODUCT 065 — 出品と CIP の「作るもの」を結ぶ（段階2・ADR-146・FR-280〜286 の一部）。
-- CIP の店コードの対応（/api/svc/master/store_codes・読み取りだけ）を種にする（PL-56）。
-- 持つのはコードと分類だけ。CIP の商品名・店別の仕様（受注メッセージの写し）は持たない（CLAUDE.md §3-1）
CREATE TABLE listing_cip_link (
  id           INTEGER PRIMARY KEY,
  listing_id   INTEGER NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
  option_id    INTEGER REFERENCES listing_option(id) ON DELETE CASCADE,  -- SKU・規格に当たったとき
  cip_store    TEXT NOT NULL,       -- CIP での店名
  store_code   TEXT NOT NULL,       -- CIP の店コード
  product_code TEXT NOT NULL,       -- CIP の共通商品コード（作るもの）
  matched_by   TEXT NOT NULL CHECK (matched_by IN ('ext_id','ext_sub','option')),
  cat1 TEXT, cat2 TEXT, cat3 TEXT, shape TEXT, size TEXT, pack_qty INTEGER,
  as_of        TEXT,                -- CIP の対応表の時点
  linked_at    TEXT NOT NULL,
  linked_by    TEXT NOT NULL
);
CREATE INDEX listing_cip_link_listing ON listing_cip_link(listing_id);
CREATE INDEX listing_cip_link_code ON listing_cip_link(product_code);

-- 結び直しの記録（何回目・件数・時点）
CREATE TABLE listing_cip_run (
  id INTEGER PRIMARY KEY, as_of TEXT, counts_json TEXT NOT NULL, by_user TEXT NOT NULL, at TEXT NOT NULL
);

-- 店のマスタに CIP での店名を入れる（11-13 本番の対応表で確かめた。アットフレーム・overseas は CIP に無い）
UPDATE shop SET cip_store='グッズ本店' WHERE code='goods';
UPDATE shop SET cip_store='うちわ本店' WHERE code='uchiwa';
UPDATE shop SET cip_store='楽天' WHERE code='rakuten';
UPDATE shop SET cip_store='Yahoo' WHERE code IN ('yahoo_goods','yahoo_frame');
UPDATE shop SET cip_store='amazon' WHERE code='amazon';
UPDATE shop SET cip_store='ギフトモール' WHERE code='giftmall';
