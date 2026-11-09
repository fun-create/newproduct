# 取り決め guide_v1（NEW PRODUCT）― AI ASSISTANT の朝の便に「やること」を載せる

- 2026-11-09 制定（ADR-133・十文字さん「朝の便にも」）。Business Analysis の guide_v1（経営管理リポジトリ `docs/contracts/guide_v1.md`）と**同じ形**。**片側だけで繋がない**
- 認証: 相手ごとの鍵。NEW PRODUCT 側の正本は `/opt/newproduct/config/secretary_guide_token`（0600・newproduct）。AI ASSISTANT 側の置き場は AI ASSISTANT が決める（例 `/opt/secretary/.config/newproduct_guide.token`）。loopback ＋ 転送ヘッダ無し ＋ `Authorization: Bearer`
- 本人: ヘッダ `X-User: <共通ID>`。**NEW PRODUCT の利用者（config/users.json で有効）だけ**。無ければ 403 `code: not_user`
- 出し分け・並べ替えは NEW PRODUCT がする（業務ロール・承認資格に関係するものだけ。社長は全部）。**AI ASSISTANT 側で絞り込みも並べ替えもしない**
- 宛先は `http://127.0.0.1:8794`（NEW PRODUCT のポート・`deploy/newproduct.service` の NEWPRODUCT_PORT。Caddy を通さない）

## GET /api/guide/todo

```json
{"user_id": "chieko", "as_of": "2026-11-09", "count_all": 21, "todo_url": "https://newproduct.fun-create.co.jp/#/todo",
 "items": [{"key": "weekly_2026-11-05", "title": "今週の週次レポート（〜11/5）を取り込む", "state": "todo", "due": "2026-11-11", "group": "いま",
            "url": "https://newproduct.fun-create.co.jp/#/sales",
            "help_url": "https://newproduct.fun-create.co.jp/#/manual?sec=..."}]}
```

- `items` は **期限切れ（late）と今やる（todo）だけ**。今やるは**期限が7日以内のものと、期限の無いもの**（ゲートの判定・年間プランの承認など。Business Analysis との違い: NEW PRODUCT は期限の無い「今やる」が多く、載せないと朝の便に出ない）
- 並び: 期限切れ → 期限の近い順 → 期限なし
- `count_all` はやること一覧の件数（右上の数字と同じ。事前の準備の「未」も含む）
- **金額・点は入っていない**（元から持っていない）。顧客の個人情報も入っていない

## 朝の便での出し方（案）
```
📌 NEW PRODUCT のやること（期限切れ 1・今やる 5）
・【期限切れ】期限切れの案件のタスクが 3 件あります → https://newproduct.fun-create.co.jp/#/tasks?...
・【11/11】今週の週次レポート（〜11/5）を取り込む → https://newproduct.fun-create.co.jp/#/sales
・あなたが判定するゲートが 4 件あります → https://newproduct.fun-create.co.jp/#/gates
一覧: https://newproduct.fun-create.co.jp/#/todo
```
- `items` が空で `count_all` も 0 の日は何も足さない
- 送る相手は、いま朝の便を受け取っている人のうち、この API が 200 を返す人
