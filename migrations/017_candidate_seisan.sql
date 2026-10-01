-- NEW PRODUCT 017 — 採用した候補を seisan に登録した記録（FR-184・ADR-047）。
-- **マスタの中身は持たない。**どこに入れたか（seisan 側の鍵）と、いつ・誰が、だけ。
ALTER TABLE sourcing_candidate ADD COLUMN seisan_ref TEXT;          -- material:<コード>/<種別> ／ outsourcer:<id>
ALTER TABLE sourcing_candidate ADD COLUMN seisan_registered_by TEXT;
ALTER TABLE sourcing_candidate ADD COLUMN seisan_registered_at TEXT;
ALTER TABLE sourcing_candidate ADD COLUMN seisan_note TEXT;         -- 途中で止まったときの理由（seisan の文言のまま）
