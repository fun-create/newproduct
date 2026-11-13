-- NEW PRODUCT 072 — FutureShop の登録の決まり（段階4・ADR-154・FR-254・255）と、列の並びの全体。
-- 出どころ: FutureShop 公式マニュアル「標準CSV」の項目仕様（2026-11-13 読んだ）。うちの店の出力は Shift_JIS で
-- 「標準CSV（従来項目版）」とみられ、細かい決まりが違うことがある → 試しの1件で確かめる（ADR-151）
ALTER TABLE export_layout ADD COLUMN full_json TEXT;     -- 落とした列も含めた見出しの並び（書き出しはこの並びで、落とした列は空）

INSERT OR IGNORE INTO cart_rule (cart, rule_key, value, source, checked_on, note) VALUES
 ('futureshop','pattern:goods:商品URLコード','^[0-9A-Za-z_-]{2,32}$','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13','2〜32文字・半角英数字と - _'),
 ('futureshop','max_chars:goods:商品番号','32','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13',NULL),
 ('futureshop','forbid:goods:商品番号','~','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13','~ はエラー'),
 ('futureshop','max_chars:goods:商品名','300','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13','HTML は <br> だけ'),
 ('futureshop','only_br:goods:商品名','1','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13',NULL),
 ('futureshop','max_chars:goods:キャッチコピー','200','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13','HTML は <br> だけ'),
 ('futureshop','only_br:goods:キャッチコピー','1','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13',NULL),
 ('futureshop','max_chars:goods:レイアウト割当名','100','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13','管理画面に在る名前だけ'),
 ('futureshop','max_rows:goods','5000','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13','1ファイル 5,000 件・30MB'),
 ('futureshop','max_chars:selection:セレクト／ラジオボタン用項目名','100','https://manual.future-shop.jp/regist/productbasic-goodsSelection','2026-11-13','マニュアルの列名は半角 /'),
 ('futureshop','forbid:selection:セレクト／ラジオボタン用項目名','~/','https://manual.future-shop.jp/regist/productbasic-goodsSelection','2026-11-13',NULL),
 ('futureshop','max_chars:selection:セレクト／ラジオボタン用選択肢','100','https://manual.future-shop.jp/regist/productbasic-goodsSelection','2026-11-13',NULL),
 ('futureshop','forbid:selection:セレクト／ラジオボタン用選択肢','~/','https://manual.future-shop.jp/regist/productbasic-goodsSelection','2026-11-13',NULL),
 ('futureshop','no_update:selection','1','https://manual.future-shop.jp/regist/productbasic-goodsSelection','2026-11-13','項目選択肢は n（新規）と d（削除）だけ。更新できない'),
 ('futureshop','max_chars:opt_basic:項目名【基本情報】','100','https://manual.future-shop.jp/regist/productbasic-goodsOptionPriceBasic','2026-11-13','改行・タブ不可。<br> だけ'),
 ('futureshop','max_items:opt_basic','20','https://manual.future-shop.jp/regist/productbasic-goodsOptionPriceBasic','2026-11-13','1商品のオプション項目は 20 まで'),
 ('futureshop','max_chars:opt_select:選択肢【選択肢・価格設定】','100','https://manual.future-shop.jp/regist/productbasic-goodsOptionPriceSelection','2026-11-13',NULL),
 ('futureshop','pattern:opt_select:選択肢コード【選択肢・価格設定／価格設定】','^[0-9A-Za-z_-]{0,50}$','https://manual.future-shop.jp/regist/productbasic-goodsOptionPriceSelection','2026-11-13','50文字・半角英数字と - _'),
 ('futureshop','control:goods','n,u','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13','n 新規・u 更新（削除は別のファイル）。更新で空の欄は変えない'),
 ('futureshop','control:opt_basic','n,u,d','https://manual.future-shop.jp/regist/productbasic-goodsOptionPriceBasic','2026-11-13',NULL),
 ('futureshop','control:opt_select','n,u,d','https://manual.future-shop.jp/regist/productbasic-goodsOptionPriceSelection','2026-11-13',NULL),
 ('futureshop','encoding_manual','UTF-8（BOMあり）・CRLF','https://manual.future-shop.jp/regist/productbasic-ccGoods','2026-11-13','標準CSV の場合。うちの出力は Shift_JIS（従来項目版とみられる）。試しの1件で確かめる');
