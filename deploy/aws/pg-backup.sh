#!/usr/bin/env bash
# 每日備份 PostgreSQL。由 cron 呼叫，見 RUNBOOK「備份」一節。
#
#   本機保留 7 天（/mnt/pgdata/backups，獨立 EBS，機器砍掉也還在）
#   R2  保留 30 天（.env 有填 R2 憑證才會上傳）
#
# 手動執行：~/Weather/deploy/aws/pg-backup.sh
set -euo pipefail

cd "$(dirname "$0")"

BACKUP_DIR=/mnt/pgdata/backups
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NAME="weather-${STAMP}.dump"
OUT="${BACKUP_DIR}/${NAME}"

mkdir -p "$BACKUP_DIR"

# -Fc = custom format，pg_restore 才能選擇性還原、也比純 SQL 小
# 先寫 .part 再改名：中途失敗不會留下一個看起來正常的半截檔
docker compose exec -T postgres \
  pg_dump -U weather -d weather -Fc > "${OUT}.part"
mv "${OUT}.part" "$OUT"

SIZE=$(stat -c %s "$OUT")
if [ "$SIZE" -lt 1024 ]; then
  echo "ERROR: ${NAME} 只有 ${SIZE} bytes，備份不正常" >&2
  exit 1
fi
echo "$(date -u +%FT%TZ) OK ${NAME} ${SIZE} bytes"

# 本機保留 7 天
find "$BACKUP_DIR" -maxdepth 1 -name 'weather-*.dump' -mtime +7 -delete
find "$BACKUP_DIR" -maxdepth 1 -name 'weather-*.dump.part' -mtime +1 -delete

# ── 上傳 R2（選用）──────────────────────────────────────────────
# .env 沒填 R2 憑證就跳過，不當成失敗
set -a; [ -f .env ] && . ./.env; set +a
if [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${R2_ENDPOINT_URL:-}" ]; then
  docker run --rm \
    -e AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
    -e AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
    -e AWS_DEFAULT_REGION=auto \
    -v "${BACKUP_DIR}:/b:ro" \
    amazon/aws-cli:latest \
    s3 cp "/b/${NAME}" "s3://${R2_BUCKET_NAME}/db-backups/${NAME}" \
    --endpoint-url "$R2_ENDPOINT_URL"
  echo "$(date -u +%FT%TZ) OK uploaded to R2"
else
  echo "$(date -u +%FT%TZ) SKIP R2（.env 未設 R2 憑證）"
fi
