-- NEW PRODUCT 025 — 利用者ごとの表示設定（2026-10-06 十文字さん指示「設定ページに使う上での設定項目を」）。
-- 配色（ライト／ダーク／端末に合わせる）は共通部品 auth.py が持つ（他のアプリと同じ置き場所）。
-- ここは**このアプリだけの**見た目と既定: 文字の大きさ・表の詰め具合・最初に開く画面・トレンドの見せ方。
-- **値は app/prefs.py の許可リストにあるものだけ**（任意の文字列を属性や URL に入れない）。
CREATE TABLE IF NOT EXISTS user_pref (
  user_id    TEXT NOT NULL,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, key)
);
