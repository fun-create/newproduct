-- 採点の校正の記録（FR-111・F-10-6／F-10-10・ADR-101・2026-10-17）
--
-- 発売後の実績（発売から12か月の売上）と、アイデアの採点時の軸の点を突き合わせ、
-- **人が**「当たった軸・外した軸」を判断して rubric に残す。アプリは相関と件数を出すだけで、重みを勝手に変えない。
-- 記録は消さない（判断の履歴）。
CREATE TABLE IF NOT EXISTS calibration_note (
  id             INTEGER PRIMARY KEY,
  rubric_version TEXT NOT NULL,               -- どの版の軸について
  axis           TEXT NOT NULL,               -- 軸コード（total＝総合点）
  verdict        TEXT NOT NULL,               -- 当たった / 外した / 保留
  reason         TEXT NOT NULL,
  n_pairs        INTEGER,                     -- 判断したときの突き合わせ件数
  rho            REAL,                        -- 判断したときの順位相関（出ていなければ NULL）
  noted_by       TEXT NOT NULL,
  noted_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calnote_axis ON calibration_note (rubric_version, axis);
