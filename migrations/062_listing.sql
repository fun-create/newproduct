-- NEW PRODUCT 062 — 商品登録・出品の台帳の土台（段階1・ADR-141・第17章 FR-253〜290）。
-- **今の案件の表（project など）には列を足さない。**新しい表として横に置く。
-- 既存商品の説明文と画像は取り込まない（2026-11-12 20:57 十文字さん）。取り込みで列ごと落とす（app/listing_parse.py DROP）。
-- CIP の products.name / order_lines.product_name / message は読まない（CLAUDE.md §3-1）。ここに入るのは店が書いた掲載の値だけ。

-- 店（FR-253）。9店。カートの種類・CIP での店名・識別子の名前
CREATE TABLE shop (
  code        TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  cart        TEXT NOT NULL CHECK (cart IN ('futureshop','rakuten','yahoo','giftmall','amazon','shopify')),
  cip_store   TEXT,                 -- CIP の店名（段階2で CIP の店コードの対応と照らす。未確認は NULL）
  id_label    TEXT NOT NULL,        -- 店の識別子の名前（商品URLコード・商品管理番号・code・SKU など）
  sort        INTEGER NOT NULL
);
INSERT INTO shop (code,label,cart,cip_store,id_label,sort) VALUES
 ('goods','グッズ本店','futureshop',NULL,'商品URLコード',1),
 ('uchiwa','うちわ本店','futureshop',NULL,'商品URLコード',2),
 ('frame','アットフレーム','futureshop',NULL,'商品URLコード',3),
 ('rakuten','楽天（グッズ）','rakuten',NULL,'商品管理番号',4),
 ('yahoo_goods','Yahoo グッズ','yahoo',NULL,'code',5),
 ('yahoo_frame','Yahoo フレーム','yahoo',NULL,'code',6),
 ('giftmall','ギフトモール','giftmall',NULL,'商品ID',7),
 ('amazon','Amazon','amazon',NULL,'seller-sku',8),
 ('overseas','overseas','shopify',NULL,'Handle',9);

-- カートの決まり（FR-254・255）。**プログラムに埋め込まず表で持つ**（ADR-075 を 11-12 に改めた）。
-- 各行に出どころと確かめた日を持つ。古さは往復の検査と取り込みのエラーで気づく。段階4で埋める
CREATE TABLE cart_rule (
  id          INTEGER PRIMARY KEY,
  cart        TEXT NOT NULL,
  rule_key    TEXT NOT NULL,        -- 例: title_max_chars / option_price_allowed / tax / image_max
  value       TEXT NOT NULL,
  source      TEXT NOT NULL,        -- 出どころ（マニュアルの URL・確かめた画面）
  checked_on  TEXT NOT NULL,        -- 確かめた日 YYYY-MM-DD
  note        TEXT,
  updated_by  TEXT, updated_at TEXT,
  UNIQUE (cart, rule_key)
);

-- 売るもの（FR-256〜259）。変わらない内部の番号。店の識別子は出品の側で結ぶ
CREATE TABLE sellable (
  id          TEXT PRIMARY KEY,     -- 内部の番号（変えない）
  label       TEXT NOT NULL,        -- 社内の呼び名（顧客データではない）
  cat1 TEXT, cat2 TEXT, cat3 TEXT,  -- 分類（語は CIP から読む・FR-257）
  sales_type  TEXT,                 -- 販売タイプ（同上）
  base_price  INTEGER,              -- 基準の価格（税込・円）。未定は NULL（0 で埋めない）
  state       TEXT NOT NULL DEFAULT '準備中' CHECK (state IN ('準備中','販売中','終了')),
  project_id  TEXT REFERENCES project(id),   -- 案件と結ばなくてよい（既存商品）
  created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE sellable_revision (    -- 変更の履歴（FR-259）
  id INTEGER PRIMARY KEY, sellable_id TEXT NOT NULL REFERENCES sellable(id),
  changed_at TEXT NOT NULL, changed_by TEXT, what TEXT NOT NULL, detail TEXT
);

-- 選択肢の定義（FR-260〜266）。表だけ先に置く（中身は段階3）
CREATE TABLE option_def (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL CHECK (kind IN ('作るもの','追加料金','お客さまの入力')),
  input_type  TEXT NOT NULL DEFAULT '選ぶ' CHECK (input_type IN ('選ぶ','自由入力')),
  note TEXT, created_by TEXT, created_at TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE option_value (
  id INTEGER PRIMARY KEY, def_id INTEGER NOT NULL REFERENCES option_def(id),
  code TEXT NOT NULL, label TEXT NOT NULL, sort INTEGER NOT NULL DEFAULT 0, add_price INTEGER,
  UNIQUE (def_id, code)
);

-- 店の出品（FR-275〜279）。店 × 商品ページ。**消えた出品も消さない**（gone_import_id を立てる）
CREATE TABLE listing (
  id            INTEGER PRIMARY KEY,
  shop          TEXT NOT NULL REFERENCES shop(code),
  ext_id        TEXT NOT NULL,      -- 店の識別子（shop.id_label）
  ext_sub       TEXT,               -- 2つ目の識別子（商品番号・ASIN など）
  title         TEXT,               -- 店の掲載名（店が書いたもの・CLAUDE.md §3-1）
  price         INTEGER,            -- 店の価格（円）。読めなければ NULL
  state         TEXT NOT NULL CHECK (state IN ('公開','非公開','そのほか')),
  state_raw     TEXT,               -- 店の値そのまま（0/1・Active など）
  sale_from TEXT, sale_to TEXT,
  category      TEXT,               -- 店のカテゴリ・ジャンル（楽天のジャンル ID など）
  layout        TEXT,               -- 自社 EC のレイアウト割当名（FR-326）
  raw_json      TEXT NOT NULL,      -- 取り込んだ元の値（空でない列だけ・説明と画像の列は落とす・FR-289）
  row_hash      TEXT NOT NULL,      -- 元の値と付いている選択肢から作る。同じなら変えない（FR-288）
  sellable_id   TEXT REFERENCES sellable(id),
  first_import_id INTEGER NOT NULL, last_import_id INTEGER NOT NULL,
  gone_import_id  INTEGER,          -- 最後の取り込みのファイルに無かった
  UNIQUE (shop, ext_id)
);
CREATE INDEX listing_shop_state ON listing(shop, state);

-- 出品に付いている選択肢（FR-279）。元のファイルの1行（楽天は選択肢の1値）を1行で持つ
CREATE TABLE listing_option (
  id          INTEGER PRIMARY KEY,
  listing_id  INTEGER NOT NULL REFERENCES listing(id) ON DELETE CASCADE,
  src         TEXT NOT NULL,        -- 元のファイルの種類（selection / opt_basic / opt_select / rakuten_option など）
  item_name   TEXT,                 -- 店での項目名
  value       TEXT,                 -- 店での値
  code        TEXT,                 -- 選択肢コード・SKU
  price       INTEGER,
  sort        INTEGER NOT NULL DEFAULT 0,
  raw_json    TEXT NOT NULL,
  def_value_id INTEGER REFERENCES option_value(id)   -- 定義との対応（段階3）
);
CREATE INDEX listing_option_listing ON listing_option(listing_id);

-- 店の選択肢のマスタ（ギフトモールのオプションのマスタなど・商品と別に定義を持つ店）
CREATE TABLE shop_option_master (
  id INTEGER PRIMARY KEY, shop TEXT NOT NULL REFERENCES shop(code), ext_id TEXT NOT NULL,
  name TEXT, raw_json TEXT NOT NULL, row_hash TEXT NOT NULL,
  first_import_id INTEGER NOT NULL, last_import_id INTEGER NOT NULL, gone_import_id INTEGER,
  UNIQUE (shop, ext_id)
);

-- 取り込みの記録（FR-287〜289）。取り消し用に、変えた行の前の値を listing_import_prev に持つ
CREATE TABLE listing_import (
  id          INTEGER PRIMARY KEY,
  shop        TEXT NOT NULL REFERENCES shop(code),
  files_json  TEXT NOT NULL,        -- [{name, sha256, rows, kind}]（読まなかったファイルと理由も）
  counts_json TEXT NOT NULL,        -- {added, changed, same, gone, options, orphans}
  state       TEXT NOT NULL CHECK (state IN ('適用','取り消し')),
  by_user     TEXT NOT NULL, at TEXT NOT NULL,
  undone_by TEXT, undone_at TEXT
);
CREATE TABLE listing_import_prev (
  import_id   INTEGER NOT NULL REFERENCES listing_import(id),
  tbl         TEXT NOT NULL CHECK (tbl IN ('listing','shop_option_master')),
  ext_id      TEXT NOT NULL,
  action      TEXT NOT NULL CHECK (action IN ('added','changed','gone')),
  before_json TEXT,                 -- changed・gone の前の行（選択肢も）。added は NULL
  PRIMARY KEY (import_id, tbl, ext_id)
);
