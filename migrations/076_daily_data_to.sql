-- NEW PRODUCT 076 — ラベル×日の売上が「どの日まで実際にあったか」（ADR-165・2026-11-24）。
-- 送る側（sync_newprod_master.ps1）は期間の終わりを「今日」にしているが、sales.csv は毎日は更新されない
-- （11/23 の初回: 期間 5/1〜11/23、明細は 11/19 まで）。期間の終わりで「ここまで数えた」と出すと、
-- 11/20〜22 の売上が抜けたまま年度累計が「確定」になる。
-- data_to = 送られた明細の最新日（送る側が data_to を名乗ればその日）。数えた範囲の終わりはこちらを使う。
ALTER TABLE newprod_daily_run ADD COLUMN data_to TEXT;
UPDATE newprod_daily_run SET data_to = (
  SELECT MAX(day) FROM newprod_daily d WHERE d.day BETWEEN newprod_daily_run.day_from AND newprod_daily_run.day_to);
