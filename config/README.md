# config/ — **人が置くファイルは1つも無い**

ここはアプリが自動で作る場所です。手で置くものはありません。

| ファイル | 誰が作るか | 権限 |
|---|---|---|
| `users.json` | 利用者を登録したとき（Calendar の画面から共通ログインへ割り当て） | 0600 |
| `.sessions.json` | ログインしたとき（sid の SHA-256 だけを持つ） | 0600 |
| `svc_token` | 起動時（サーバ間APIの Bearer） | 0600 |
| `departments.json` | `newproduct-sync-dept`（未実装。写し） | 0644 |

**2026-09-21 時点で `users.json` はありません。**
`/opt/accounts/roles/newproduct.json` の登録がまだなので、**全員がログインを断られます。
これは正しい状態です。**起動ログ（`journalctl -u newproduct`）にもその旨が出ます。

`git` には追跡させないこと（`reset --hard` で消えます）。
