#!/bin/sh
# NEW PRODUCT（FUN-CREATE 新商品開発管理）— 日次バックアップ
# 雛形: /opt/calfc/deploy/backup.sh
#
#   15 4 * * * root /opt/newproduct/deploy/backup.sh >> /var/log/newproduct-backup.log 2>&1
#
# **04:15。**他と重ねない（lpscope 3:15 / calfc 3:30 / autogrowth・seisan 3:45 /
# keiei 4:00）。既存タイマーは 06:42〜11:04 に集中しているのでそこも避ける。
#
# **SQLite は tar で固めるだけでは不整合になりうる。**
# 書き込み中のファイルをコピーすると復元できないことがあるため、
# 先に VACUUM INTO で静止点のスナップショットを作る。
#
# **このファイルの実行権限を落とすと毎晩静かに失敗する**（seisan が実際に10日間止まった）。
#   chmod 755 /opt/newproduct/deploy/backup.sh
set -eu
APP="${NEWPRODUCT_HOME:-/opt/newproduct}"
DEST="${NEWPRODUCT_BACKUP_DIR:-/var/backups/newproduct}"
KEEP=30

mkdir -p "$DEST"
# **700 にしない。**置き場は一覧できる必要がある。
# AutoGrowth の watchdog が `/var/backups/<app>/` を glob で走査して
# 「3日途切れ」を報せるが、**glob は読めないディレクトリで例外を出さず空を返す**。
# 700 にすると「まだバックアップを持たないアプリ」と区別がつかず、
# **警報も出ないまま監視から外れる**（2026-09-21 実測。他5アプリは 755）。
# 中のファイルは `-rw------- root:root` なので、755 でも中身は読めない。
# 「置き場は誰でも一覧でき、中身は読めない」が watchdog の前提。
chmod 755 "$DEST"
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$DEST/newproduct-$STAMP.tar.gz"
DB="$APP/data/newproduct.db"
SNAP="$APP/data/_snapshot.db"

# **config も入れる。**users.json（利用者と役割）と svc_token / csrf_salt がある。
# svc_token を失うと、呼ぶ側（keiei / calfc）の設定も同時に入れ替えになる。
SET="config"

if [ -f "$DB" ]; then
  rm -f "$SNAP"
  sqlite3 "$DB" "VACUUM INTO '$SNAP'"
  tar -czf "$OUT" -C "$APP" $SET --transform 's,^,newproduct/,' \
      -C "$APP/data" "$(basename "$SNAP")"
  rm -f "$SNAP"
else
  # **DB がまだ無い時期でも黙って何もしないことはしない。**
  # 「バックアップが0バイト」と「動いていない」は見分けがつかない
  echo "note: $DB がまだありません。config だけを退避します"
  tar -czf "$OUT" -C "$APP" $SET --transform 's,^,newproduct/,'
fi

chmod 600 "$OUT"
ls -1t "$DEST"/newproduct-*.tar.gz | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "backup: $OUT ($(du -h "$OUT" | cut -f1))"
