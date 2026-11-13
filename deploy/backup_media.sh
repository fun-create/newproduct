#!/bin/sh
# NEW PRODUCT — 素材（WEBチームへ渡す写真・図）の毎日の控え（ADR-144・FR-306）
#
#   25 4 * * * newproduct /opt/newproduct/deploy/backup_media.sh >> /var/log/newproduct-backup.log 2>&1
#
# 2026-11-12 21:04 十文字さん: **画像だけ毎日差分で控える**（30世代の DB の控え backup.sh には入れない。
# 入れると同じ画像を30回ぶん持つ）。rsync --link-dest で、変わっていないファイルは前の日の控えと
# ハードリンクを共有する（場所を食わない）。日ごとのフォルダはどれも「その日の全部」に見える。
#
# **newproduct 利用者で動かす**（backup.sh と同じ理由）。置き場は /var/backups/newproduct/media/（newproduct 持ち）。
# backup.sh の古い控えの削除は newproduct-*.tar.gz だけを見るので、この下には触れない。
# **このファイルの実行権限を落とすと毎晩静かに失敗する**: chmod 755 /opt/newproduct/deploy/backup_media.sh
set -eu
APP="${NEWPRODUCT_HOME:-/opt/newproduct}"
SRC="${NEWPRODUCT_MEDIA:-$APP/data/media}"
DEST="${NEWPRODUCT_MEDIA_BACKUP_DIR:-/var/backups/newproduct/media}"
KEEP=30

if [ ! -d "$SRC" ]; then
  # **黙って何もしないことはしない。**まだ1つも上げていない時期はここに来る
  echo "$(date '+%F %T') media: $SRC がまだありません（素材が1つも無い）。控えは作りません"
  exit 0
fi
mkdir -p "$DEST"
chmod 755 "$DEST"
STAMP=$(date +%Y%m%d-%H%M%S)
PREV=$(ls -1d "$DEST"/20* 2>/dev/null | tail -n 1 || true)
if [ -n "$PREV" ]; then
  rsync -a --link-dest="$PREV" "$SRC"/ "$DEST/$STAMP"/
else
  rsync -a "$SRC"/ "$DEST/$STAMP"/
fi
chmod 700 "$DEST/$STAMP"
N=$(find "$DEST/$STAMP" -type f | wc -l)
echo "$(date '+%F %T') media: $DEST/$STAMP（ファイル $N 個・前の日 ${PREV:-なし}）"
ls -1d "$DEST"/20* | head -n -$KEEP | xargs -r rm -rf
