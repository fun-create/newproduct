-- 新機種調査（FR-140・F-9-5・ADR-102・2026-10-18）
--
-- 十文字さんの選択: NEW PRODUCT が AI にウェブ調査させる（毎月・既存の「AI にウェブで探させる」の仕組み）。
-- 元は Google シート「スキンシール本体モデル展開管理」（10シート＝本体モデル×展開の表、「手順」＝ChatGPT に投げる文）。
-- **展開の表の正本はこのアプリに移す**（取り込みは tools/import_device_models.py。画面で直した行は取り込みで上書きしない）。

-- 調べる対象のシリーズ（「手順」の12）。sheet は展開の表のシート名（12→10）
CREATE TABLE IF NOT EXISTS device_series (
  code    TEXT PRIMARY KEY,
  label   TEXT NOT NULL,
  sheet   TEXT NOT NULL,
  hint    TEXT,                              -- 「手順」の解釈ルール（Ploom ブランド全体 など）
  sort    INTEGER NOT NULL,
  active  INTEGER NOT NULL DEFAULT 1
);

-- 本体モデル × スキンシール展開（展開の表）
CREATE TABLE IF NOT EXISTS device_model (
  id           INTEGER PRIMARY KEY,
  sheet        TEXT NOT NULL,                -- device_series.sheet
  model        TEXT NOT NULL,                -- 本体モデル（元表のまま）
  model_no     TEXT,                         -- モデル番号（iPad・MacBook の表にだけある）
  deploy       TEXT,                         -- ○ / 互換性あり / しない / 掲載終了 など元表の語のまま。空＝未記入
  release      TEXT,                         -- 発売日・発売年（元表の書き方のまま）
  ended        TEXT,                         -- 掲載終了の記載（元表のまま）
  url          TEXT,                         -- 公式サイト・出典 URL
  note         TEXT,
  source_row   INTEGER,                      -- 取り込み元の行（0 始まり）
  edited_by    TEXT,
  edited_at    TEXT,                         -- 画面で直した印。取り込みは触らない
  UNIQUE (sheet, model)
);

-- AI が出した新機種の候補（人が 案件にする／展開済み／見送り を押す）
CREATE TABLE IF NOT EXISTS device_candidate (
  id           INTEGER PRIMARY KEY,
  run_id       INTEGER REFERENCES ai_run(id),
  web_id       INTEGER REFERENCES ai_web(id),   -- 検索語・開いた URL（trace）はこちら
  series_code  TEXT NOT NULL REFERENCES device_series(code),
  model        TEXT NOT NULL,
  release_date TEXT,                         -- AI が書いたまま（確かめるのは人）
  jp_released  TEXT,                         -- 発売済み / 発売予定 / 日本未発売 / 不明
  confidence   TEXT,                         -- 公式 / 報道 / 噂
  summary      TEXT,
  url          TEXT,
  seen         TEXT,                         -- 開いた / 検索結果に出た / 見ていない
  state        TEXT NOT NULL DEFAULT '提案', -- 提案 / 案件にした / 展開済み / 見送り
  project_id   TEXT REFERENCES project(id),
  decided_by   TEXT,
  decided_at   TEXT,
  decided_note TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_device_candidate_state ON device_candidate (state, series_code);
