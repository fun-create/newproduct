-- NEW PRODUCT 019 — 年間目標（第4段・F-10-8／F-10-9・FR-109/110）。
-- **自由入力にしない。**3方式のどれかと根拠を必須にする（keiei の basis_json の作法）。
-- **未設定は NULL。0 と書かない**（B-9: 104件中97件が未設定だった）。
-- 単位は**税込の商品代**（発売後の実績＝売上フィードと同じ土台。2026-10-01 決定「税込」）。

CREATE TABLE IF NOT EXISTS sales_target (
  project_id TEXT PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
  annual_yen REAL,            -- 発売から1年の目標（税込・商品代）。NULL = 目標未設定
  method     TEXT,            -- 類似商品法 / 積み上げ法 / 逆算法
  basis      TEXT,            -- 根拠（必須。どの商品の実績から・どう積んだか）
  updated_by TEXT, updated_at TEXT
);
