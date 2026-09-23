-- NEW PRODUCT 011 — 機会カレンダーの取り込みに要る列（F-2 ／ FR-78・FR-79）。
--
-- 入れ物（`theme` と `theme_score`）は 006 で作ってある。**表は増やさない。**
-- 足りなかったのは、元表が持っていて捨てたくない4つだけ。
--
-- **`source_row` / `source_col` を残す理由。**元表はライフイベントの名前が
-- 行によって **8列目と10列目** にある（1始まり）。列がずれた理由は表計算の書式で、
-- 意味の違いではない —— と**読んだ側が判断した**ので、**どの行をどちらとして
-- 読んだかを残す。**あとから人が元表と突き合わせられないと、判断の是非を検めない。
--
-- **`product_gap` は「商品が作れていない」という現場の申告。**元表に
-- 「新商品が作れていないライフイベント」という別表があり、商品例まで書かれている。
-- **これは需要ではなく、自社の欠けのほう。**スコアと混ぜない。

ALTER TABLE theme ADD COLUMN sellable      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE theme ADD COLUMN product_gap   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE theme ADD COLUMN product_ideas TEXT;
ALTER TABLE theme ADD COLUMN source_row    INTEGER;
ALTER TABLE theme ADD COLUMN source_col    INTEGER;

CREATE INDEX IF NOT EXISTS idx_theme_gap ON theme (product_gap) WHERE product_gap = 1;
