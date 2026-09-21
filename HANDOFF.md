# NEW PRODUCT 引き継ぎ

最終更新: **2026-09-21**

着手前に、このファイルの「宿題」と `REQUIREMENTS.md` の `未着手` を読んでください。

---

## いま動いている状態

| 項目 | 状態（2026-09-21 実測） |
|---|---|
| 本番 | <https://newproduct.fun-create.co.jp> ／ `127.0.0.1:8794` |
| サービス | `newproduct.service` **active (running) / enabled**（uid 988・`/opt/newproduct` 750） |
| Caddy | `/etc/caddy/Caddyfile` に断片を追記済み。`/api/*` は外から 404 |
| DB | `data/newproduct.db`（SQLite・WAL）。migrations 001〜005 適用済み |
| 種データ | 6フロー **178タスク**（`freecut 32 / material 39 / meire 32 / newmodel 14 / readymade 22 / webdeco 39`） |
| 実データ | **案件0件・タスク0件。**まだ誰も業務では使っていない |
| 利用者 | `masateru`（アプリ権限 `admin`）**1名のみ** |
| バックアップ | `/etc/cron.d/newproduct-backup` 04:15。`/var/backups/newproduct` に世代2本 |
| テスト | `selfcheck.py` 80件・失敗0 ／ `tests/test_second_stage.py` 52件 OK ／ `tests/e2e_http.py` 失敗0 ／ `tests/test_upstream.py` 4件 |

### 実装が済んでいる範囲

**第2段（案件・タスク・ゲート・ダッシュボード）まで。**
`REQUIREMENTS.md` で `実装済` 51件・`実装中` 8件・`検証済` 2件。

**`検証済` は2件だけ**（FR-09 ログイン画面の表示 ／ FR-10 そのロゴ表示）。
2026-09-21 にブラウザで本番のログイン画面を開いて目で見たのは、この2つだけです。
**第2段は `selfcheck` と `curl` と自動テストが通っただけで、まだ誰も画面を開いていません。**

### 動かすとき

```bash
ssh -i ~/.ssh/lpscope_vps masateru@162.43.43.186
sudo systemctl status newproduct
sudo journalctl -u newproduct -f

cd /opt/newproduct
sudo -u newproduct python3 selfcheck.py                       # 80件
sudo -u newproduct python3 -m unittest discover -s tests -v   # 上流検知を含む
sudo -u newproduct python3 tests/e2e_http.py                  # サーバを起こして HTTP で叩く
sudo -u newproduct python3 -m app.seed --report               # 種データの件数と不採用の差分
```

---

## 宿題（自分でやる分）

### 1. 第2段を**人が画面で見る**

いちばん先にやること。`検証済` が2件しか無いのは、**画面を開いていないから**です。
2026-09-20、AI-Assistant は HTTP が全部200なのに入力欄が黒く潰れていました。
**数値だけ見て完了にすると、この種の不具合は必ず通り抜けます**（`rules/eod.md`）。

見る順: ダッシュボード → 案件作成 → カルテ（B節「いま欠けているもの」）→
タスク一覧（「期限なし」の件数）→ ゲート盤（記号と語が両方出ているか・`対象外` の表示）。

### 2. `work_item` / `project_variant` の登録フォーム

どちらも表と一覧はあるが、**作る画面が無い**（FR-37・FR-47・FR-48）。
`work_item` が入らないと、実運用台帳397件のうち6フロー外の行（FBA納品・BtoB整備・
旧商品修正・仕組み化）が月次負荷に載らず、**負荷が実態より軽く見えます。**

### 3. ステージ遷移UI

`project.STAGES`（起票→評価済→候補→…→評価完了＋保留・中止）は定義済みだが、
**遷移させる画面が無い**（FR-33）。いまは作成時の `起票` から動かせません。

### 4. `config/departments.json` の配置

`server.departments_sha()` は実装済みだが、ファイルが無いので `/api/health` の
`departments_sha` は **null**（FR-120）。keiei の `docs/keiei/departments.json` を写し、
sha256 で照合する形にする。**5か所目の独自定義を作らない**（N-12）。

### 5. `/var/log/newproduct-backup.log` がまだ無い

`cron.d` は `>> /var/log/newproduct-backup.log` を指定しているが、**ファイルがまだ存在しない**
（初回の 04:15 を迎えていないため）。**04:15 を過ぎたら、世代が今日の日付で増えているかを
目で見て確認すること**（共通ルール §3「日次バックアップは、権限で静かに死にます」）。

```bash
ssh masateru@162.43.43.186 'sudo ls -1 /var/backups/newproduct | tail -3'
```

---

## 人待ち

| 待っているもの | 相手 | いつから | 止まっていること |
|---|---|---|---|
| **業務ロール `prod`（生産部）の割り当て** | 十文字さん | **2026-09-21 から** | **G4（生産可否確定）が誰にも通せない** |
| **予備時間を採用するか** | 商品開発部（十文字さん経由） | 2026-09-21 から | FR-46。工数の内訳が出せない |
| ⑦ページリニューアルの標準タスク定義 | 商品開発部 | 2026-09-20 から | FR-49。計画の38%を占めるフローでタスク一覧が空になる |
| ⑤資材リニューアルの工数ポイント係数 | 商品開発部 | 2026-09-20 から | FR-87。G2 の「工数ポイント」が自動で埋まらない |
| **git の置き場と `newproduct-update`** | 十文字さん | 2026-09-21 から | 下記「本番を直接編集している」 |
| 原材料コードが seisan の `cost_code` と同じ体系か | 外部開発者（十文字さん経由） | 2026-09-20 から | 第3段（FR-99・FR-103）に入れない |
| 商品コード×月×販路のエクスポート | 外部開発者（十文字さん経由） | **未依頼** | 第4段（FR-107・FR-108） |
| calfc `service_tokens.json` への追加 | calfc セッション | **未依頼** | FR-41（営業日の自動割付）・FR-121 |
| keiei `keiei-ingest-newproduct` | keiei セッション | **未依頼** | FR-124（第4段） |
| `ai_budget.json` への `newproduct-*: 5.0` | AutoGrowth | **未依頼** | FR-146（第5段） |
| FCTR の受け口（API か引き渡しファイルか） | AutoGrowth セッション | **未依頼** | FR-135（第5段） |
| `/opt/accounts/roles/newproduct.json`（利用者の登録） | 十文字さん（Calendar 画面から） | 2026-09-20 から | いま使えるのは `masateru` 1名だけ |

---

## 落とすと困ること（具体）

### 業務ロールは `masateru` に `president` と `admin` だけ

```
role_member:
  president  masateru  (tools/grant_role.py  2026-09-21 19:34:07)
  admin      masateru  (tools/grant_role.py  2026-09-21 19:34:07)
```

**`prod`（生産部）が誰にも割り当たっていません。**
ゲートの承認資格は**業務ロール**で決まる（ADR-005）ため、
**G4（生産可否確定）は現状どのユーザーでも通せません。**
`admin` を持っていても通せません（それがこの設計の要点です）。

**設定画面がまだ無いので、割り当ては CLI から:**

```bash
cd /opt/newproduct
sudo -u newproduct python3 tools/grant_role.py --list
sudo -u newproduct python3 tools/grant_role.py --who <user_id> --role prod
```

### 予備時間の扱いが未決

F-5-7 は「予備時間（フローごとに17〜20h）を工数の内訳として明示的に持つ」と要求している。

- **採用した正本 178行（`03_…AI適用一覧.tsv`）に、予備時間の行が1つも無い**
- **古い 190行（`01_…テンプレート.tsv`）にはある**

値は `app/seed.py` の `old_reserve_hours()` が拾って `--report` に出していますが、
**`task_template` には入れていません**（古い表が正かどうかの判断は商品開発部）。
このままだと、**標準工数は「予備時間を含まない実作業h」**です。画面にもそう書いてあります。
**採否が決まるまで、合計工数を「これが全部です」と言わないこと。**

### タスクの期限は人が入れる

営業日マスタは calfc が正本で、連携が未依頼のため**自動割付をしていません**（ADR-012）。
**推測で日付を入れないでください。**入れると実運用の「期限空欄88件」が見えなくなります。
タスク画面の「期限なし」は常設ボタンで、件数が見えるようにしてあります。

### git と update スクリプトが無く、本番を直接編集している

**共通ルール §3 の例外状態です。**`registry/apps.json` の `new-product` にも
`gap` として記録されています:

> リポジトリそのものが無い。2026-09-21 に `/opt/newproduct` を VPS 上で直接作成。
> 置き場と `newproduct-update` は十文字さんの判断待ち

つまり**いまは `/opt/newproduct` が唯一の正本**です。次の2点に注意してください。

1. **バックアップが唯一の退避先です。**`/var/backups/newproduct` の世代が
   今日の日付で増えていることを、目で見て確認する
2. **git が入った日に、本番の変更が消える危険があります。**
   他アプリでは実際に起きかけています（2026-09-17 autogrowth・2026-09-20 `/opt/secretary`・
   2026-09-21 `/opt/keiei`）。**git 化のときは、先に `/opt/newproduct` を丸ごと取る**

`deploy/` は正本ではありません。systemd ユニットの正本は `/etc/systemd/system/`、
cron の正本は `/etc/cron.d/`。配る前に必ず `diff -u` で突き合わせてください。

### 設定画面が無いので、ロール割り当ては `tools/grant_role.py`

`#/settings` はまだ枠だけです（第2段では監査の記録だけ取っています）。
マスタ・利用者・データの出どころ・監査ログの画面は未着手。
それまでロールの付け外しは `tools/grant_role.py` から行ってください。

---

## 次にやる3件

1. **第2段を本番の画面で目視する**（`実装済` 51件のうち、見たものを `検証済` へ上げる）
2. `work_item` / `project_variant` の登録フォームと、ステージ遷移UI
3. 第1段（アイデア台帳・採点v2・機会カレンダー・年間プランの枠）へ着手
