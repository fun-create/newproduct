-- 既にある案件を年間プランの枠に紐づける（FR-203・ADR-118・2026-10-20 十文字さん承認。9/23 の提案②）
-- converted_* は「案件にする」で起こした跡。link_kind で見分ける（NULL＝前からの「案件にする」）
ALTER TABLE plan_slot ADD COLUMN link_kind TEXT;   -- convert / link
