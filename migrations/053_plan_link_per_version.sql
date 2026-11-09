-- 年間プランの改訂で、案件との結び付きを新しい版へ引き継ぐ（2026-11-09 修正依頼書 R02・H03・ADR-129）
--
-- 以前は「1つの案件は全部の版を通して1つの枠だけ」だったので、改訂版（策定中）に結び付きを写せず、
-- 改訂版を承認すると、結んだ案件が全部「どの枠とも結ばれていない」に戻っていた（d542 は案件化済みが各月0件）。
-- **1つの版の中で1つの枠**に改める。二度押しで案件が2つできるのを止める目的は、版の中で守れば足りる
DROP INDEX IF EXISTS idx_plan_slot_project;
CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_slot_project_ver
  ON plan_slot (version_id, project_id) WHERE project_id IS NOT NULL;

-- 改訂で写した元の枠（どの約束を引き継いだかを後からたどる）
ALTER TABLE plan_slot ADD COLUMN copied_from TEXT;
