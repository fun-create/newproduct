#!/usr/bin/env python3
"""
上流からの複製の検知（共通ルール `rules/apps-common.md` §6）。

    python3 -m unittest tests.test_upstream -v
    python3 tests/test_upstream.py          # skip の件数を必ず言う

`auth.py` は **lpscope → business-analysis(keiei) → new-product** と複製されている。
**下流で直接編集しない。**片方だけ直すと静かにズレる。

この検査が見るのは2つ。

1. **上流が変わっていないか。**`_upstream.json` に記録した sha256 と、いまの
   `/opt/keiei/app/auth.py` を突き合わせる
2. **こちらで余計な編集をしていないか。**ローカルの `auth.py` と上流の差分が、
   `_upstream.json` の `local_edits` に申告した分だけで説明できるかを見る。
   **想定外の差分が1行でもあれば落とす**

**上流が読めない環境では落とさず skip する。**このアプリは VPS の上で動いていて、
手元や別サーバーには `/opt/keiei` が無い。そこで落とすと、
「上流がズレた」と「上流が見えない」が区別できなくなる。

ただし共通ルール §7 のとおり **`OK` は「落ちなかった」であって「動いた」ではない。
skip は緑に混ざる。**だから:

- skip の理由には**探した場所**を必ず書く（`skipTest` のメッセージ）
- `test_manifest_is_wellformed` は**上流が無くても必ず走る**。
  4件すべてが skip になることはない
- このファイルを直接実行すると、最後に **`skip N 件`** を大きく出す
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MANIFEST = BASE / "_upstream.json"

# 上流の置き場。環境変数で差し替えられるようにしておく（検証用クローンを指すとき用）。
# **`Path("")` は `.`（カレント）になる。**空文字のまま Path に通すと、
# 上流のつもりで **自分自身**を読んで「上流が変わった」と誤報する（2026-09-21 に踏んだ）。
# 文字列のうちに空かどうかを見る。
UPSTREAM_DIR = os.environ.get("NEWPRODUCT_UPSTREAM_DIR", "").strip()


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def upstream_dir(man: dict) -> Path:
    return Path(UPSTREAM_DIR) if UPSTREAM_DIR else Path(man["upstream"])


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ── ローカル変更の許容規則 ──────────────────────────────
#
# `_upstream.json` の `local_edits["auth.py"]` は**人が読む申告**。
# こちらは**機械が突き合わせる規則**。1対1で対応させ、件数が合わないと落とす
# （申告だけ増やす・規則だけ増やす、のどちらも通さない）。
#
# 各規則は (id, 説明, 判定関数) の3つ組。判定関数は
# 「上流から消えた行のかたまり」「ローカルで増えた行のかたまり」と、
# **それぞれが何行目から始まるか**を受け取り、
# その差分が自分の説明する変更かどうかを返す。
#
# 行番号を渡すのは、先頭の由来コメントのためだけ。
# 「コメント行なら何でも許す」にすると、**ファイルのどこであっても**
# コメントの書き換えが素通りする。先頭の由来ブロックに限る。

HEADER_LINES = 14   # 由来コメントはファイル冒頭のこの範囲に収まっている

def _joined(lines: list[str]) -> str:
    return "".join(lines)


def _is_header_note(old: list[str], new: list[str], i1: int, j1: int) -> bool:
    """先頭の由来コメント。複製元の書き換えだけで、挙動には効かない。

    **冒頭 HEADER_LINES 行のコメント行に限る。**
    ここを緩めると、ファイルのどこであってもコメントの書き換えが素通りする。
    """
    if i1 >= HEADER_LINES or j1 >= HEADER_LINES:
        return False
    if not (old or new):
        return False
    return all(l.lstrip().startswith("#") for l in old + new)


def _is_config_dir(old: list[str], new: list[str], i1: int, j1: int) -> bool:
    """keiei は `app/auth.py`、本アプリは直下 `auth.py`。親を1段減らす。"""
    return ('CONFIG = Path(__file__).resolve().parent.parent / "config"'
            in _joined(old)
            and 'CONFIG = Path(__file__).resolve().parent / "config"'
            in _joined(new))


def _is_session_cookie(old: list[str], new: list[str], i1: int, j1: int) -> bool:
    return ('SESSION_COOKIE = "keiei_sid"' in _joined(old)
            and 'SESSION_COOKIE = "newproduct_sid"' in _joined(new))


def _is_csrf_header(old: list[str], new: list[str], i1: int, j1: int) -> bool:
    return ('CSRF_HEADER = "X-KEIEI-CSRF"' in _joined(old)
            and 'CSRF_HEADER = "X-NEWPRODUCT-CSRF"' in _joined(new))


ALLOWED_EDITS = [
    ("header",         "先頭の「出どころ」コメント",              _is_header_note),
    ("config_dir",     "CONFIG の置き場（parent.parent → parent）", _is_config_dir),
    ("session_cookie", "SESSION_COOKIE（keiei_sid → newproduct_sid）", _is_session_cookie),
    ("csrf_header",    "CSRF_HEADER（X-KEIEI-CSRF → X-NEWPRODUCT-CSRF）", _is_csrf_header),
]

TARGET = "auth.py"


def classify(old_lines: list[str], new_lines: list[str]):
    """差分のかたまりを1つずつ、許容規則に当てはめる。

    返すのは (説明できた規則id の集合, 説明できなかったかたまりの一覧)。
    """
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    matched: set[str] = set()
    unexplained: list[tuple[list[str], list[str]]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old = old_lines[i1:i2]
        new = new_lines[j1:j2]
        for eid, _label, fn in ALLOWED_EDITS:
            if fn(old, new, i1, j1):
                matched.add(eid)
                break
        else:
            unexplained.append((old, new))
    return matched, unexplained


def _show(old: list[str], new: list[str], limit: int = 6) -> str:
    out = [f"  - 上流から消えた {len(old)} 行 / ローカルで増えた {len(new)} 行"]
    for l in old[:limit]:
        out.append("    - " + l.rstrip())
    for l in new[:limit]:
        out.append("    + " + l.rstrip())
    if len(old) > limit or len(new) > limit:
        out.append("    …（以下省略）")
    return "\n".join(out)


class UpstreamCopy(unittest.TestCase):

    # ── 上流が無くても必ず走る ────────────────────────────
    def test_manifest_is_wellformed(self):
        """`_upstream.json` と複製したファイルが揃っているか。**skip しない。**"""
        self.assertTrue(MANIFEST.is_file(), f"{MANIFEST} がありません")
        man = manifest()
        for key in ("upstream", "taken_at", "files", "local_edits"):
            self.assertIn(key, man, f"_upstream.json に {key} がありません")
        self.assertIn(TARGET, man["files"], "files に auth.py の指紋がありません")
        self.assertRegex(man["files"][TARGET], r"^[0-9a-f]{64}$",
                         "指紋が sha256 の形をしていません")
        self.assertTrue((BASE / TARGET).is_file(), f"{BASE / TARGET} がありません")

        declared = man["local_edits"].get(TARGET, [])
        self.assertEqual(
            len(declared), len(ALLOWED_EDITS),
            "_upstream.json の申告と、この検査の許容規則の件数が合いません。\n"
            f"  申告 {len(declared)} 件 / 規則 {len(ALLOWED_EDITS)} 件\n"
            "  **どちらか片方だけを増やさないこと。**申告を足したら規則も足す。")

    # ── ここから先は上流が要る ───────────────────────────
    def _upstream_file(self) -> Path:
        man = manifest()
        p = upstream_dir(man) / TARGET
        if not p.is_file():
            self.skipTest(
                f"上流が読めないので検査していません（探した場所: {p}）。"
                " このアプリは VPS 上で動きます。手元では上流がありません。"
                " 別の場所を見るなら NEWPRODUCT_UPSTREAM_DIR を渡してください")
        try:
            p.read_bytes()
        except OSError as e:
            self.skipTest(f"上流 {p} を読めません（{e}）。検査していません")
        return p

    def test_upstream_unchanged(self):
        """**上流が変わっていないか。**変わっていたら取り込み直しの合図。"""
        man = manifest()
        p = self._upstream_file()
        self.assertEqual(
            sha256(p), man["files"][TARGET],
            f"\n上流 {p} が {man['taken_at']} の記録から変わっています。\n"
            "  **ここで直接直さないこと。**上流（BUSINESS ANALYSIS セッション）へ\n"
            "  差異の確認を依頼し、取り込み直してから _upstream.json を更新してください\n"
            "  （共通ルール §6・§9）。")

    def test_only_declared_local_edits(self):
        """**申告していない差分が1行でもあれば落とす。**"""
        p = self._upstream_file()
        old = p.read_text(encoding="utf-8").splitlines(keepends=True)
        new = (BASE / TARGET).read_text(encoding="utf-8").splitlines(keepends=True)
        _matched, unexplained = classify(old, new)
        if unexplained:
            detail = "\n".join(_show(o, n) for o, n in unexplained)
            self.fail(
                f"\n{BASE / TARGET} に、申告していない差分が {len(unexplained)} 箇所あります。\n"
                f"{detail}\n"
                "  下流で直接編集しないでください（共通ルール §6）。\n"
                "  正当な変更なら _upstream.json の local_edits と\n"
                "  tests/test_upstream.py の ALLOWED_EDITS の両方に足してください。")

    def test_declared_edits_are_present(self):
        """**申告した変更が実際にあるか。**消えた申告も差分のうち。"""
        p = self._upstream_file()
        old = p.read_text(encoding="utf-8").splitlines(keepends=True)
        new = (BASE / TARGET).read_text(encoding="utf-8").splitlines(keepends=True)
        matched, _ = classify(old, new)
        missing = [f"{eid}（{label}）" for eid, label, _fn in ALLOWED_EDITS
                   if eid not in matched]
        self.assertFalse(
            missing,
            "\n申告しているのに、実際には差分として見つからない変更があります: "
            + "／".join(missing)
            + "\n  上流を取り込み直したときに消えた可能性があります。"
            "\n  **Cookie 名や CSRF ヘッダ名が上流のまま**だと、"
            "keiei と同じ Cookie 名で動くことになります。")


def main() -> int:
    """直接実行したとき。**skip を数えて、最後に必ず出す。**

    `OK` は「落ちなかった」であって「動いた」ではない（共通ルール §7）。
    skip が混ざっていることが分かる形で終わる。
    """
    suite = unittest.TestLoader().loadTestsFromTestCase(UpstreamCopy)
    r = unittest.TextTestRunner(verbosity=2).run(suite)
    n_skip = len(r.skipped)
    print("─" * 60)
    print(f"{r.testsRun}件 実行 / 失敗 {len(r.failures) + len(r.errors)}件 / "
          f"**skip {n_skip}件**")
    if n_skip:
        print("  skip は「通った」ではありません。理由:")
        for t, why in r.skipped:
            print(f"   - {t.id().split('.')[-1]}: {why}")
        print("  上流を見られる環境（VPS）で走らせるまで、"
              "**複製のズレは検知できていません。**")
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
