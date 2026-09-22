-- NEW PRODUCT 009 — 年間プランの枠（§4-3・F-3 ／ FR-82〜FR-86）。
--
-- **枠は案件ではない。**「この月にこの種類を1本出す」という**予定の器**で、
-- 中身（アイデア・担当・カルテ）が決まると案件へ変換する（FR-86）。
-- 器のまま年度をまたぐことがあるので、案件表に空レコードを作らない。
--
-- **挿入ルールは警告であって、禁止ではない**（F-3-3 ／ FR-84）。
-- 保存を止めると、現場は表計算に戻る。**止めずに、理由を残させる。**
--
-- **数えられないルールは「OK」と言わない**（N-10）。長期連休の月は Calendar
-- （calfc）の会社休業日が正本だが、まだ繋いでいない。**未設定のまま「違反なし」
-- と出すと、検査したことになってしまう。**`plan_rule` は `unavailable` を返す。

-- ── 商品タイプ（F-3-8）────────────────────────────────
-- 「ページリニューアル」を**正式な商品タイプとして持つ。**計画の38%を占めながら
-- 178行の標準タスクに定義が無い（B-2-1-2(b)）。ここに無いと、枠を作るたびに
-- 「開発タイプ ⑦」で代用することになり、**発売本数に混ざる**（F-10-11）。
--
-- **`ratio_group` を列で持つ理由。**挿入ルールの 3:1 は、出典（2026年度 年間プラン）
-- では「オリジナル（推し活）：うちわ」と書かれている。一方このアプリの評価テーマは
-- 「ライフイベント（オリジナルグッズ）」と「推し活（うちわ）」で、**括弧の中が逆**。
-- どちらの括りが正かは商品開発部にしか決められないので、**コードに焼かずデータに置く。**
-- 付け替えれば比率の分母・分子が変わる。
CREATE TABLE IF NOT EXISTS product_kind (
  code             TEXT PRIMARY KEY,
  label            TEXT NOT NULL,
  seq              INTEGER NOT NULL,
  ratio_group      TEXT,              -- original / uchiwa / NULL = 比率の対象外
  counts_as_launch INTEGER NOT NULL DEFAULT 1,  -- 「月3商品」の本数に入れるか
  default_flow     TEXT REFERENCES flow_type(code),
  note             TEXT
);

-- ── 年間プランの版（F-3-4 ／ FR-85）──────────────────
-- 期首に策定し、期中に改訂する。**承認済みは年度に1つだけ。**
-- 部分ユニーク索引で DB 側から縛る。アプリ側のチェックだけだと、
-- 移行スクリプトや手作業の INSERT が素通りする。
CREATE TABLE IF NOT EXISTS plan_version (
  id          TEXT PRIMARY KEY,       -- 短い16進。URL に貼る（#/plan/7a21）
  fiscal_year INTEGER NOT NULL,       -- 年度（2026年度 = 2026）
  label       TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT '策定中',   -- 策定中 / 承認済 / 失効
  based_on    TEXT REFERENCES plan_version(id), -- 改訂元。NULL = 新規策定
  approved_by TEXT,
  approved_at TEXT,
  note        TEXT,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_version_approved
  ON plan_version (fiscal_year) WHERE state = '承認済';
CREATE INDEX IF NOT EXISTS idx_plan_version_fy ON plan_version (fiscal_year, state);

-- ── 枠（F-3-1 ／ FR-82）────────────────────────────────
-- **月は必須、日は任意。**期首の時点では「5月に1本」までしか決まらない。
-- 日を必須にすると、仮の日付が入り、逆算した期限がその仮日付で動き出す。
CREATE TABLE IF NOT EXISTS plan_slot (
  id           TEXT PRIMARY KEY,
  version_id   TEXT NOT NULL REFERENCES plan_version(id) ON DELETE CASCADE,
  launch_month TEXT NOT NULL,          -- YYYY-MM
  launch_date  TEXT,                   -- YYYY-MM-DD。NULL = 月までしか決まっていない
  product_kind TEXT REFERENCES product_kind(code),
  flow_type    TEXT REFERENCES flow_type(code),
  area         TEXT,
  -- NULL = 未確定。**0 で埋めない。**⑤資材リニューアルは係数そのものが未実測
  effort_point REAL,
  effort_src   TEXT,                   -- manual / flow_type / NULL
  owner        TEXT,
  idea_id      TEXT REFERENCES idea(id),
  occasion     TEXT,                   -- なぜその月か（機会・イベント）
  -- 変換の跡（FR-86）。**枠は消さない。**どの枠がどの案件になったかを残す
  project_id   TEXT REFERENCES project(id),
  converted_at TEXT,
  converted_by TEXT,
  note         TEXT,
  created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_plan_slot_ver   ON plan_slot (version_id, launch_month);
CREATE INDEX IF NOT EXISTS idx_plan_slot_month ON plan_slot (launch_month);
CREATE INDEX IF NOT EXISTS idx_plan_slot_idea  ON plan_slot (idea_id);
-- **1つの枠は1案件まで。**二度押しで案件が2つできるのを DB で止める
CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_slot_project
  ON plan_slot (project_id) WHERE project_id IS NOT NULL;

-- ── 例外の承知（F-3-3 ／ FR-84）────────────────────────
-- **警告を消すためのものではない。**警告は出し続け、「誰がなぜ承知したか」を
-- 並べて出す。消せる作りにすると、理由を書かずに消すほうが速くなる。
CREATE TABLE IF NOT EXISTS plan_exception (
  id          INTEGER PRIMARY KEY,
  version_id  TEXT NOT NULL REFERENCES plan_version(id) ON DELETE CASCADE,
  rule        TEXT NOT NULL,           -- ratio / effort / holiday / count
  scope       TEXT NOT NULL,           -- 年度なら "FY"、月なら "YYYY-MM"
  reason      TEXT NOT NULL,           -- **必須。**空では入れられない
  acked_by    TEXT,
  acked_at    TEXT,
  UNIQUE (version_id, rule, scope)
);
