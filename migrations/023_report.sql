-- NEW PRODUCT 023 — 月次レポート（F-10-7・FR-116）。2026-10-02 十文字さん「提案に従う・お任せ」。
-- 毎月2日 10:00 に前月分を作って保存する（systemd timer）。**送らない**（画面で読む）。
-- 作り直したら上書きする（同じ月は1本）。
CREATE TABLE IF NOT EXISTS monthly_report (
  month        TEXT PRIMARY KEY,     -- 2026-09
  body_md      TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  generated_by TEXT NOT NULL,        -- timer / 利用者ID
  problems     TEXT                  -- 作れなかった節（理由つき）。無ければ NULL
);
