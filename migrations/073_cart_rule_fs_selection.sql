-- NEW PRODUCT 073 — 072 で入れ忘れた決まり: 項目選択肢のコントロールカラムは n（新規）と d（削除）（ADR-155）。
-- 無いと新規の書き出しで項目選択肢のファイルが作れない（11-13 テストで見つけた）
INSERT OR IGNORE INTO cart_rule (cart, rule_key, value, source, checked_on, note) VALUES
 ('futureshop','control:selection','n,d','https://manual.future-shop.jp/regist/productbasic-goodsSelection','2026-11-13','n 新規・d 削除（その項目名の選択肢を全部消す）。更新は無い');
