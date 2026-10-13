-- 発売後の「試算 vs 確定」の突き合わせ（FR-93・F-7-4/F-7-8・ADR-095・2026-10-13）
--
-- 発売から60日の受注が締まった時点の値を**1回だけ残す**（試算がどれだけ当たったかを残す）。
-- seisan は後から原価を計算し直せる（管理者の「いまの原価で計算し直す」）ので、
-- 見るたびに取り直すだけだと「当時どれだけ外れたか」が残らない。
-- **残した行は書き換えない。**直したいときは消さずに、画面のいまの値と並べて見る。
CREATE TABLE IF NOT EXISTS cost_check (
  project_id   TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  product_code TEXT NOT NULL,                 -- seisan の共通商品コード
  window_from  TEXT NOT NULL,                 -- 発売日
  window_to    TEXT NOT NULL,                 -- 発売日 + 59日（60日分）
  est_json     TEXT NOT NULL,                 -- 比べた試算（版・1個あたりの材料/外注/直接費/工数・未確定の行数）
  actual_json  TEXT NOT NULL,                 -- seisan から受け取ったとおり（決めた列だけ）
  recorded_at  TEXT NOT NULL,
  recorded_by  TEXT NOT NULL,                 -- 見た人の ID ／ timer
  PRIMARY KEY (project_id, product_code)
);
