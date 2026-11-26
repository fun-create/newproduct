-- NEW PRODUCT 077 — その回に送られたラベル（ADR-166・2026-11-26 十文字さんの選択「NP 側で消さない」）。
-- 新商品マスタで「有効」を外したラベルは、次の回から送られない。期間をまるごと入れ替えると、そのラベルの
-- 年度内の日ごとの記録が消え、年度累計から落ちる（発売から1年で人が外す決まりなので、年度の途中で必ず起きる）。
-- 入れ替えるのは**送られたラベルだけ**にし、送られなかったラベルの行は残す。どれが残りかを画面に出すため、
-- 回ごとに送られたラベルの一覧を持つ。既にある回（11/23 の1回）は、その時点の行のラベルで埋める。
ALTER TABLE newprod_daily_run ADD COLUMN sent_labels TEXT;   -- JSON の配列
UPDATE newprod_daily_run SET sent_labels = (SELECT json_group_array(label) FROM (SELECT DISTINCT label FROM newprod_daily ORDER BY label))
 WHERE id = (SELECT MAX(id) FROM newprod_daily_run);
