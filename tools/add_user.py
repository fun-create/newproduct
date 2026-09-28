#!/usr/bin/env python3
"""
このアプリの利用者を足す・役割を変える（**画面ができるまでの繋ぎ**）。

    sudo -u newproduct python3 tools/add_user.py --list
    sudo -u newproduct python3 tools/add_user.py --who yoko --role user
    sudo -u newproduct python3 tools/add_user.py --who tsubasa --role admin
    sudo -u newproduct python3 tools/add_user.py --who yoko --off        # 停止（消さない）

## どこに書くか（2026-09-27 に確かめ直したこと）

ログインを決めているのは **`config/users.json`**（`auth.USERS_PATH`）。
`/opt/accounts/roles/newproduct.json` は**誰も読まない写し**で、足しても入れない
（私が 09-21 に手で書いたもの。Calendar セッションの指摘で判明）。

共通ログイン（`ACCOUNTS_DIR=/opt/accounts`・本番で有効）では、合言葉の照合は
共通台帳が行い、**このファイルの `password` は使われない。**だから足すときは
`auth.create()` に**誰も知らないでたらめな合言葉**を渡す（表示も保存もしない。
ダイジェストだけが残り、照合には使われない）。`auth.create()` を通すのは、
錠・世代・0600 を守るため。**JSON を手で書かない。**

## 共通台帳の利用許可（`roles/`）に切り替えた後（2026-09-28〜）

`roles/_enforced.json` に newproduct が載ったら、**入れるか・admin/user かは
`/opt/accounts/roles/newproduct.json` が決める**（カレンダーの ☰ →「人とアプリ」）。
そのあとは、ここで足しても役割を変えても**変わったように見えて何も効かない**ので、
`--list` 以外は `auth.ROLES_MOVED` を出して断る（上流の経営・LP SCOPE の画面と同じ）。
**業務上の役割（`role_member`・`tools/grant_role.py`）は引き続きこのアプリが持つ。**

**共通台帳（`/opt/accounts/users.json`）に居ない人は足さない。**居ない人を
ここに足しても、合言葉の照合ができず入れない。表示名は共通台帳から写す。
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import auth  # noqa: E402

SHARED = Path(os.environ.get("ACCOUNTS_DIR") or "/opt/accounts") / "users.json"


def shared_people() -> dict:
    """共通台帳の人（ID → 表示名・有効）。**合言葉は読まない。**読めなければ空。"""
    try:
        d = json.loads(SHARED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    u = d.get("users", d)
    out = {}
    for v in (u if isinstance(u, list) else [dict(id=k, **x) for k, x in u.items()]):
        uid = auth.norm_user_id(v.get("id") or v.get("user_id") or "")
        if uid:
            out[uid] = {"name": v.get("name", ""), "active": v.get("active", True) is not False}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--who")
    ap.add_argument("--role", choices=sorted(auth.ROLE_LABEL))
    ap.add_argument("--name", help="表示名。省略時は共通台帳から")
    ap.add_argument("--off", action="store_true", help="停止する（消さない）")
    ap.add_argument("--on", action="store_true", help="停止を解く")
    a = ap.parse_args()

    if a.list:
        if auth.roles_enforced():
            print("※ 共通台帳の利用許可に従っています。入れるかどうかは "
                  "/opt/accounts/roles/newproduct.json が決めます（下の「このアプリ」は写し）")
        people = shared_people()
        local = {u["user_id"]: u for u in auth.users()}
        print(f"  {'ID':12} {'表示名':10} {'このアプリ':10} 共通台帳")
        for uid in sorted(set(people) | set(local)):
            lu = local.get(uid)
            here = (lu["role"] + ("" if lu.get("active", True) else "（停止）")) if lu else "—"
            print(f"  {uid:12} {(lu or people.get(uid, {})).get('name',''):10} {here:10} "
                  f"{'あり' if uid in people else '**無し**'}")
        return 0

    if not a.who:
        ap.error("--who か --list を指定してください")
    if auth.roles_enforced():
        # **何も効かない変更をさせない。**役割は毎回 roles/ から引かれ、足した人には許可が無い
        print(auth.ROLES_MOVED, file=sys.stderr)
        return 2
    uid = auth.norm_user_id(a.who)
    people = shared_people()
    if auth.shared_on() and people and uid not in people:
        print(f"共通台帳（{SHARED}）に {uid!r} が居ません。**先に人を登録してください**"
              "（カレンダーの管理画面）。ここに足しても合言葉の照合ができず入れません", file=sys.stderr)
        return 1
    existing = auth.get(uid)
    if a.off or a.on:
        if not existing:
            print(f"{uid!r} はこのアプリの利用者ではありません", file=sys.stderr); return 1
        auth.update(uid, active=not a.off, actor="tools/add_user.py")
        print(f"{uid}: {'停止しました' if a.off else '再開しました'}"); return 0
    role = a.role or "user"
    if existing:
        if a.role and existing["role"] != a.role:
            auth.update(uid, role=a.role, actor="tools/add_user.py")
            print(f"{uid}: 役割を {existing['role']} → {a.role} に変えました")
        else:
            print(f"{uid}: 既に利用者です（{existing['role']}）。変更なし")
        return 0
    name = (a.name or people.get(uid, {}).get("name") or "").strip()
    if not name:
        print("表示名が分かりません。--name で指定してください", file=sys.stderr); return 1
    # **誰も知らない合言葉。**共通ログインでは照合に使われない（ダイジェストだけが残る）
    throwaway = secrets.token_urlsafe(24) + "Aa1!"
    rec, err = auth.create(uid, name, throwaway, role=role, created_by="tools/add_user.py")
    if err:
        print("追加できませんでした:", err, file=sys.stderr); return 1
    print(f"{uid}（{name}）を {role} として足しました。次のログインから入れます")
    return 0


if __name__ == "__main__":
    sys.exit(main())
