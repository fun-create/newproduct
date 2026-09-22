# 設置・更新・戻し方（NEW PRODUCT）

共通の作法は HUB `rules/apps-common.md` が正本。**ここにはこのアプリ固有のことだけ**を書く。

## 更新する

```bash
# 手元（~/Desktop/Claudcode/newproduct）で直す → テスト → commit → push
ssh masateru@162.43.43.186 sudo newproduct-update
```

`--check` を付けると、取り込む変更と変わるファイルを見るだけで止まる。
**サーバー上で直接編集しない。**次の更新で黙って消える。

## 画面の資産（共通意匠）

`ui/assets/` に `app-shell.css` と素材21点。**正本は Auto GROWTH セッション**
（`docs/app-identity/app-shell.css`）。アプリ側で色コードを手打ちしない。

- 帯は `.fca-bar`。**calfc の `<header class="hd">` を写さない**（あちらは独自ヘッダー）
- ロゴは `title-logo.svg` を `.fca-logo` で。**高さで揃え、幅は素材の比に従わせる。切り取らない**
  （2026-09-21 まで8アプリ全部で絵が切れていた。`--fca-logo-w` は廃止済み）
- `#FACC15` は白地で 1.53:1。**白い地にアプリ名や色線を置かない**（帯の上なら 8.5〜9.0）
- 現在地は色だけで示さず `aria-current="page"`

## `/assets/` の Content-Type

**拡張子の許可リスト方式**（`server.py` の `ASSET_TYPES`）。リストに無い拡張子は 404。

> `.css` を `application/octet-stream` で返すと、`nosniff` でブラウザがスタイルシートの
> 適用を拒む。**HTTP は 200 のままなので curl では気づけない。**
> 2026-09-20 に Auto GROWTH と LPSCOPE の両方で起き、ヘッダーが素の文字になった。

`selfcheck.py` が**実際に HTTP で取得して型を検査**する。状態コードだけを見ない。

## 設置し直す（更地から）

1. `useradd --system --home-dir /opt/newproduct --shell /usr/sbin/nologin --user-group newproduct`
   **`-G funcreate-auth` を忘れない。**外すとログイン失敗回数が共有されず、
   20回/30秒の制限がアプリごと独立になる（**エラーは出ない**）
2. `git clone git@github.com:fun-create/newproduct.git /opt/newproduct`
3. `cp deploy/newproduct.service /etc/systemd/system/` → `systemctl enable --now newproduct`
   **`ReadWritePaths` から `config` と `/opt/accounts/state` を外さない**（同上・エラーが出ない）
4. Caddy に `deploy/Caddyfile.fragment` を追記 → `caddy validate` → `systemctl reload caddy`
   **validate を通してから reload する。**Caddyfile は7アプリの共有資産
5. `cp deploy/cron.d-newproduct-backup /etc/cron.d/newproduct-backup`（04:15）
   **`backup.sh` の置き場を 700 にしない。**755 でないと watchdog の glob から見えず、
   **警報も出ないまま監視から外れる**（2026-09-21 実測）
6. `/opt/accounts/roles/newproduct.json` に利用者を登録（Calendar の画面から）

## 戻す

```bash
sudo -u newproduct git -C /opt/newproduct log --oneline -5
sudo -u newproduct git -C /opt/newproduct reset --hard <戻したいSHA>
sudo systemctl restart newproduct && sudo -u newproduct python3 /opt/newproduct/selfcheck.py
```

`data/` は git の外。**戻しても消えない。**バックアップは `/var/backups/newproduct/`（30世代）。
