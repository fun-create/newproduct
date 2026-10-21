-- NEW PRODUCT 048 — 年間プランの枠の「発売する商品」（点検 H-4・ADR-119・2026-10-21）。
--
-- 進捗管理シートの年間プランの中心だった「発売する商品」は、取り込みで枠の note に
-- 「商品: …／タスク設定期限（元表）: …」と入っただけで、画面に出ず、足す・直す欄も無かった。
-- 社内の企画名（seisan の商品名ではない）。いまの note から写す。「（空）」は空のまま。
ALTER TABLE plan_slot ADD COLUMN product TEXT;

UPDATE plan_slot
   SET product = NULLIF(TRIM(CASE WHEN instr(note, '／') > 0
                                  THEN substr(note, 5, instr(note, '／') - 5)
                                  ELSE substr(note, 5) END), '（空）')
 WHERE product IS NULL AND note LIKE '商品: %';
