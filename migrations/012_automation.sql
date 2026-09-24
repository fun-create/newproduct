-- NEW PRODUCT 012 — 自動化依頼（F-15 ／ FR-159〜FR-170）。
-- 2026-09-24 十文字さん指示（商品開発 増地さんの要望）。
--
-- **「自動化してほしい作業」を受け付け、質問で要件に変えて、実装へ渡す。**
--
-- なぜ表を分けるか（`work_item` に入れない）:
--   `work_item` は**やる仕事**。こちらは**やらなくて済ませたい仕事**で、
--   終わり方が違う。work_item は「完了」で閉じるが、自動化依頼は
--   **「実装されて、以後その作業が発生しなくなる」**ことで閉じる。
--   同じ表に入れると、月次の工数に二重に載る。
--
-- **原文を書き換えない。**`raw_request` に増地さんの言葉のまま置く。
-- 要約は別の列に持つ。要約が原文を上書きすると、
-- 「本当は何に困っていたか」が最初の整理で消える。
--
-- **標準タスクに当たるかを持つ**（`template_id`）。178行のどれかに当たるなら、
-- AI削減の見込み（`ai_reduction_rate` / `ai_reduction_hours`）が既にある。
-- **当たらないものも捨てない。**2026-09-24 の実測では、増地さんの15件のうち
-- **10件が178行のどこにも無かった。**「標準タスクに無い＝やっていない」ではなく、
-- **表のほうが現場に追いついていない。**`in_template=0` で数えられるようにする。

CREATE TABLE IF NOT EXISTS automation_request (
  id          TEXT PRIMARY KEY,      -- 短い16進。URL に貼る（#/automation/3f2a）
  title       TEXT NOT NULL,         -- 作業名（増地さんの書き方のまま）
  raw_request TEXT,                  -- **原文。書き換えない**
  requester   TEXT,                  -- 出した人
  dept        TEXT,                  -- 出した部署

  -- 仕分け
  stage       TEXT NOT NULL DEFAULT '起票',
  -- 起票 / 質問中 / 要件確定 / 実装待ち / 実装中 / 実装済 / 見送り
  template_id INTEGER REFERENCES task_template(id),
  in_template INTEGER NOT NULL DEFAULT 0,   -- 178行に当たるか。**0 も意味を持つ**
  category    TEXT,                  -- 画像/データ作成・登録作業・調査・書類作成 など

  -- 効果（答えが揃ってから計算する。**推測で埋めない**）
  minutes_each    REAL,              -- 1回あたりの分。NULL = 未回答
  times_per_month REAL,              -- 月あたりの回数。NULL = 未回答
  hours_per_month REAL,              -- 上2つから計算。片方でも NULL なら NULL

  -- 判断の記録
  decided_by  TEXT, decided_at TEXT,
  handoff_to  TEXT,                  -- 実装を誰に渡したか
  note        TEXT,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_ar_stage ON automation_request (stage);
CREATE INDEX IF NOT EXISTS idx_ar_tpl   ON automation_request (in_template);
-- **同じ作業を二度受け付けない。**冪等の鍵でもある
CREATE UNIQUE INDEX IF NOT EXISTS idx_ar_title ON automation_request (title);

-- ── 質問と答え（F-15-2）────────────────────────────────
-- **質問は固定。**AI に作らせない（AI予算枠が未取得・FR-146）。
-- 順番と文言は `app/automation.py` の `QUESTIONS` が正本で、
-- ここには**答えだけ**を置く。質問文を写すと、直したとき古い答えが残る。
--
-- **答えていないことと、空で答えたことを区別する。**行が無い＝未回答。
-- 行があって `answer=''` は「聞いたが、無いという答え」。
CREATE TABLE IF NOT EXISTS automation_answer (
  request_id  TEXT NOT NULL REFERENCES automation_request(id) ON DELETE CASCADE,
  q_key       TEXT NOT NULL,
  answer      TEXT NOT NULL,
  answered_by TEXT,
  answered_at TEXT NOT NULL,
  PRIMARY KEY (request_id, q_key)
);

-- ── やりとり（F-15-3）──────────────────────────────────
-- 質問への答えが足りないときの往復。**チャットにしない。**
-- どの質問についての話かを必ず持たせる（`q_key`）。
CREATE TABLE IF NOT EXISTS automation_note (
  id         INTEGER PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES automation_request(id) ON DELETE CASCADE,
  q_key      TEXT,                   -- NULL = 全体について
  body       TEXT NOT NULL,
  by_user    TEXT,
  at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_an_req ON automation_note (request_id, at);
