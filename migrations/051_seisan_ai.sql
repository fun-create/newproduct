-- CIP 登録票の AI 入力アシスト（FR-228・ADR-125・2026-10-23 十文字さん選択）
--
-- 十文字さん「CIP側の商品構成を学習し、AIに商品マスタ…の入力アシストまたは、候補を提案するような仕組みにしてほしい」。
-- AI は、カルテの文と CIP の商品の構成（販売タイプ・分類・形状・サイズ・色・レシピの有無。**商品名は読まない**）から、
-- 登録票の候補（販売タイプ・大中小分類・形状・サイズ・色・レシピ複製元）を理由つきで出すだけ。
-- **CIP に無い値は案にしない**（分類・販売タイプは CIP の選択肢、複製元は CIP にある・レシピのある商品だけ）。
-- 人が「登録票に入れる」を押すと下書きの欄に入る（保存するのも登録するのも人）。
CREATE TABLE IF NOT EXISTS seisan_ai_proposal (
  id           INTEGER PRIMARY KEY,
  project_id   TEXT NOT NULL REFERENCES project(id),
  run_id       INTEGER REFERENCES ai_run(id),
  fields       TEXT NOT NULL,          -- {sales_type, cat1, cat2, cat3, shape, size, color, copy_recipe_from}
  reasons      TEXT,                   -- {欄: 理由}
  unverified   TEXT,
  inputs       TEXT,
  model        TEXT,
  as_of        TEXT,                   -- CIP の構成を読んだ日時
  state        TEXT NOT NULL DEFAULT '提案',   -- 提案 / 使った / 見送り
  created_at   TEXT NOT NULL,
  decided_by   TEXT, decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_seisan_ai_project ON seisan_ai_proposal (project_id, state);
