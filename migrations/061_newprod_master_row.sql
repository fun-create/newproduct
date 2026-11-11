-- 週次レポートの新商品マスタ（キーワード方式）へ足す1行（2026-11-11 依頼書 4-4・十文字さんの選択・ADR-139）。
-- **つなぎ。**CIP の共通商品コードで数える口ができたら、キーワード方式はやめる（HUB tasks/to-cip.md 11-11）。
-- キーワードは商品開発部が NEW PRODUCT で決め、アシロボの PC のスクリプトが sales.csv で当たりを数え、マスタに足す。
ALTER TABLE newprod_reg ADD COLUMN m_label       TEXT;     -- 商品ラベル（マスタの A 列）
ALTER TABLE newprod_reg ADD COLUMN m_include     TEXT;     -- 含むキーワード（; で AND・(A|B) で OR）
ALTER TABLE newprod_reg ADD COLUMN m_exclude     TEXT;     -- 除外キーワード
ALTER TABLE newprod_reg ADD COLUMN m_month_sales REAL;     -- 月間目標売上（12か月同じ値・円）。NULL は 0
ALTER TABLE newprod_reg ADD COLUMN m_month_qty   REAL;     -- 月間目標数量（同上）
ALTER TABLE newprod_reg ADD COLUMN m_state       TEXT;     -- NULL / 書き込み待ち / 追加済み / 要確認 / 失敗
ALTER TABLE newprod_reg ADD COLUMN m_note        TEXT;     -- スクリプトが返した理由
ALTER TABLE newprod_reg ADD COLUMN m_matches     INTEGER;  -- sales.csv で当たった明細の数（発売日から）
ALTER TABLE newprod_reg ADD COLUMN m_overlaps    TEXT;     -- JSON: 同じ明細に当たる既存の行の商品ラベル（二重計上のおそれ）
ALTER TABLE newprod_reg ADD COLUMN m_requested_by TEXT;
ALTER TABLE newprod_reg ADD COLUMN m_requested_at TEXT;
ALTER TABLE newprod_reg ADD COLUMN m_checked_at  TEXT;
