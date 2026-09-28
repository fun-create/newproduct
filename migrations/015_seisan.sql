-- NEW PRODUCT 015 — seisan への商品登録（第3段・FR-103／FR-60・ADR-043）。
--
-- **マスタはここに持たない。正は seisan。**（2026-09-28 十文字さん「マスタ管理は全て seisan 側で」）
-- ここにあるのは「登録する前の下書き」と「登録された共通商品コード」だけ。
-- **登録できたら下書きは消す。**残すと seisan で直した内容と食い違い、どちらが正か分からなくなる。
--
-- 1案件に複数の商品（本体モデル別のバリエーション）があり得るので、
-- **登録の単位はバリエーション**（無ければ案件そのもの＝variant_id NULL）。

CREATE TABLE IF NOT EXISTS seisan_registration (
  id            INTEGER PRIMARY KEY,
  project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  variant_id    INTEGER REFERENCES project_variant(id) ON DELETE CASCADE,
  state         TEXT NOT NULL DEFAULT '下書き',   -- 下書き / 登録済
  draft_json    TEXT,                             -- 登録前の入力。登録済になったら NULL
  product_code  TEXT,                             -- seisan の共通商品コード（登録済のとき）
  via           TEXT,                             -- newproduct（この画面から）/ seisan（seisan の画面で）
  registered_by TEXT,
  registered_at TEXT,
  -- seisan に**在ると確かめた**時刻。seisan の口ができるまでは NULL（＝人の申告のまま）
  verified_at   TEXT,
  last_error    TEXT,                             -- 最後に seisan が断った理由（そのまま）
  updated_by    TEXT,
  updated_at    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_seisan_reg_target
  ON seisan_registration (project_id, COALESCE(variant_id, 0));
CREATE UNIQUE INDEX IF NOT EXISTS ux_seisan_reg_code
  ON seisan_registration (product_code) WHERE product_code IS NOT NULL;
