NEW PRODUCT へ週次レポートを自動で送る（2026-11-10・ADR-138）
================================================================

何をするか
  週次レポートのフォルダー
    \\TERASTATION\share\目標管理\石原\★売上実績\週次レポート
  にある「週次売上分析_YYYYMMDD-YYYYMMDD.xlsx」を NEW PRODUCT へ送ります。
  NEW PRODUCT がまだ受け取っていないファイルだけを送ります。初回は保存済みの過去分も全部送ります。
  「週次売上明細_…」は送りません。今の weekly_report.ps1（レポート作成・保存・ChatWork 配信）には触りません。

置くもの（同じフォルダーに）
  1. send_weekly_report.ps1   … このスクリプト
  2. np_feed_token.txt        … 鍵（1行）。十文字さんから受け取る。他の人に見せない・メールやチャットに貼らない
  置き場所の例: C:\NEWPRODUCT\

試す（送らずに、送る予定のファイルだけ出す）
  powershell -NoProfile -ExecutionPolicy Bypass -File C:\NEWPRODUCT\send_weekly_report.ps1 -DryRun

タスクスケジューラに登録する（毎週月曜 9:30。weekly_report.ps1 の 9:00 のあと）
  weekly_report.ps1 を動かしているのと同じ Windows ユーザーで（\\TERASTATION が見える人で）登録します。
  schtasks /Create /TN "NEWPRODUCT_週次レポート送信" /SC WEEKLY /D MON /ST 09:30 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\NEWPRODUCT\send_weekly_report.ps1"

  失敗したファイルは、次に動いたときに送り直します。すぐ送り直すときは、タスクスケジューラで「実行する」を押すか、
  上の「試す」の行から -DryRun を外して実行します。

結果の見方
  - Windows 側: 同じフォルダーの send_weekly_report.log
  - NEW PRODUCT 側: 「売上・原価」→「新商品の売上（週次レポート）」の「自動送信」（最終成功日時・失敗と理由）

止めるとき
  schtasks /Delete /TN "NEWPRODUCT_週次レポート送信"

================================================================
もう1つ: 新商品マスタに発売した商品の1行を足す（2026-11-11・ADR-139）
================================================================

何をするか
  NEW PRODUCT で「週次レポートに載せる」を押した商品を、新商品マスタ.xlsx（シート「新商品」）の最後に1行足します。
  足す前に sales.csv で当たる件数と、既存の行との重なり（二重計上のおそれ）を数えます。重なりがあれば足しません。
  足す前に「新商品マスタ_backup_YYYYMMDD_HHMMSS.xlsx」の控えを取ります。商品名そのものは NEW PRODUCT へ送りません。

置くもの（同じフォルダーに）
  sync_newprod_master.ps1 と np_feed_token.txt（週次レポート送信と同じ鍵）
  ※ Excel が入っている PC で動かします（新商品マスタを Excel で開いて書くため）

試す（マスタに書かず、NEW PRODUCT にも返さない）
  powershell -NoProfile -ExecutionPolicy Bypass -File C:\NEWPRODUCT\sync_newprod_master.ps1 -DryRun

タスクスケジューラに登録する（毎週月曜 8:30。weekly_report の 9:00 の前）
  schtasks /Create /TN "NEWPRODUCT_新商品マスタ追加" /SC WEEKLY /D MON /ST 08:30 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\NEWPRODUCT\sync_newprod_master.ps1"

結果の見方
  - Windows 側: 同じフォルダーの sync_newprod_master.log
  - NEW PRODUCT 側: 「売上・原価」→「新商品の売上（週次レポート）」の未紐付けの欄、案件ページの G 節
