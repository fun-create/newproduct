-- NEW PRODUCT 063 — WEBチームへ渡すページ（段階 W・ADR-141・FR-303〜314）。
-- 今のカルテ（C 節「なぜ作るか」）と LP依頼書（FR-118）の上に足す。**案件の表には列を足さない。**
-- WEBチームの業務ロールは既にある `webmkt`（Webマーケ）を使う（新しいロールは作らない）。

-- ページの材料（FR-305）。案件 × 欄
CREATE TABLE web_material (
  project_id  TEXT NOT NULL REFERENCES project(id),
  key         TEXT NOT NULL,
  body        TEXT NOT NULL DEFAULT '',
  updated_by  TEXT, updated_at TEXT,
  PRIMARY KEY (project_id, key)
);

-- 渡すページの版（FR-309・312）。「渡す」を押した時点の中身を固めて持つ。状態は版ごと
CREATE TABLE web_version (
  id          INTEGER PRIMARY KEY,
  project_id  TEXT NOT NULL REFERENCES project(id),
  ver         INTEGER NOT NULL,
  content_json TEXT NOT NULL,       -- 固めたページ（app/webpass.py build() の返り）
  state       TEXT NOT NULL DEFAULT '渡した'
              CHECK (state IN ('渡した','WEBチーム確認中','制作中','仕上がり受領')),
  handed_by   TEXT NOT NULL, handed_at TEXT NOT NULL,
  state_by    TEXT, state_at TEXT,
  UNIQUE (project_id, ver)
);

-- やり取り（FR-311）。版ごとに残す
CREATE TABLE web_comment (
  id          INTEGER PRIMARY KEY,
  version_id  INTEGER NOT NULL REFERENCES web_version(id),
  kind        TEXT NOT NULL CHECK (kind IN ('質問','修正の希望','答え')),
  body        TEXT NOT NULL,
  reply_to    INTEGER REFERENCES web_comment(id),
  by_user     TEXT NOT NULL, at TEXT NOT NULL
);
CREATE INDEX web_comment_version ON web_comment(version_id);
