#!/usr/bin/env python3
"""
業務ロールの割り当て。**設定画面はまだ無い**（第2段の範囲外）ので、当面はこれで行う。

    sudo -u newproduct python3 tools/grant_role.py --list
    sudo -u newproduct python3 tools/grant_role.py --who 十文字 --role president
    sudo -u newproduct python3 tools/grant_role.py --who 十文字 --role president --revoke

**業務ロールはアプリ権限（admin/user）とは別軸**（全体設計書 §10-2 ②）。
ゲートの承認資格はこちらで決まる。`admin` にしても G3 は通せない。

**割り当てが1件も無いあいだは、誰もどのゲートも通せない。それで正しい。**
承認させたいだけの人を admin にする、という回避を作らないための構造。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import seed, store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--who", help="利用者ID（config/users.json の user_id）")
    ap.add_argument("--role", help="業務ロールのコード")
    ap.add_argument("--revoke", action="store_true", help="外す")
    ap.add_argument("--list", action="store_true", help="いまの割り当てを出す")
    a = ap.parse_args()

    seed.run()

    if a.list or not (a.who and a.role):
        print("業務ロール:")
        for r in store.q("SELECT * FROM role ORDER BY sort"):
            print(f"  {r['code']:<10} {r['label']}"
                  + ("  （他部署・AI削減の試算対象外）" if r["external"] else ""))
        print("\nゲートの承認者:")
        for g in store.q("SELECT gate,name,approver_role FROM gate_def ORDER BY seq"):
            print(f"  {g['gate']} {g['name']:<14} {g['approver_role']}")
        print("\nいまの割り当て:")
        rows = store.q("SELECT rm.user_id, rm.role_code, r.label, rm.granted_at "
                       "FROM role_member rm LEFT JOIN role r ON r.code=rm.role_code "
                       "ORDER BY rm.user_id, r.sort")
        if not rows:
            print("  **1件もありません。誰もどのゲートも通せません。**")
        for r in rows:
            print(f"  {r['user_id']:<16} {r['role_code']:<10} {r['label']}"
                  f"  ({r['granted_at']})")
        return 0

    if store.one("SELECT 1 FROM role WHERE code=?", (a.role,)) is None:
        print(f"知らない業務ロール {a.role!r}。--list で一覧を出せます")
        return 1

    if a.revoke:
        store.ex("DELETE FROM role_member WHERE user_id=? AND role_code=?",
                 (a.who, a.role))
        action = "role.revoke"
    else:
        store.ex("INSERT OR REPLACE INTO role_member (role_code,user_id,granted_by,"
                 "granted_at) VALUES (?,?,?,?)",
                 (a.role, a.who, "tools/grant_role.py", store.now_s()))
        action = "role.grant"
    store.conn().commit()
    store.audit("tools/grant_role.py", action, a.who, {"role": a.role}, "local")
    print(f"{action}: {a.who} ← {a.role}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
