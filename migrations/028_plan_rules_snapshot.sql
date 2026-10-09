-- NEW PRODUCT 028 — 年間プランを承認した時点の挿入ルールの値を版に残す（2026-10-09・ADR-069）。
-- これまでは設定を変えると、承認済みの版の警告も変わっていた（app-ui の提案 1章 #8）。
-- JSON {設定キー: 値}。NULL ＝ 承認時の値が無い（この列ができる前の承認・策定中）→ いまの設定で判定する
ALTER TABLE plan_version ADD COLUMN rules_snapshot TEXT;
