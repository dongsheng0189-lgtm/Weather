#!/usr/bin/env bash
# 把 Zeabur 匯出的備份還原進本機的 postgres 容器，並逐步計時。
# 演練與正式切換用同一支，確保切換當天跑的是測過的流程。
#
#   用法：bash restore-from-zeabur.sh <R2 上的備份檔路徑>
#   例：  bash restore-from-zeabur.sh tmp/backup-6af4c94984ee3b8379eaf5b9.zip
#
# 備份檔的真相（2026-11-06 實測）：副檔名寫 .sql 或 .sql.gz，實際是 ZIP，
# 裡面是 data/_manifest.json + data/data.sql；data.sql 是 pg_dumpall 的整叢集 dump，
# 會自己 CREATE ROLE root / CREATE DATABASE zeabur / \connect zeabur。
#
# ⚠️ 會整個清掉現有的 zeabur 資料庫再重灌。任何只存在於這台機器的資料
#    （例如測試期間改過的帳號、核准的裝置）都會消失。
set -euo pipefail
cd "$(dirname "$0")"

KEY="${1:?用法: bash restore-from-zeabur.sh <R2 上的備份檔路徑>}"
WORK="$HOME/restore/run-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$WORK"

START=$(date +%s); LAST=$START
step() {
  local now; now=$(date +%s)
  printf '%-30s %4d 秒\n' "$1" "$((now - LAST))" | tee -a "$WORK/timing.txt"
  LAST=$now
}

set -a; . ./.env; set +a
PSQL=(docker compose exec -T postgres psql -U weather)
case "$DOMAIN" in
  :*) URL="http://localhost/weather/" ;;
  *)  URL="https://${DOMAIN}/weather/" ;;
esac

# ── 1. 下載 ─────────────────────────────────────────────────────────
docker run --rm \
  -e AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  -e AWS_DEFAULT_REGION=auto \
  -v "$WORK:/out" \
  amazon/aws-cli:latest \
  s3 cp "s3://${R2_BUCKET_NAME}/${KEY}" /out/backup.zip \
  --endpoint-url "$R2_ENDPOINT_URL" --only-show-errors
step "1 從 R2 下載備份"

# ── 2. 解壓、確認版本、先算出 dump 裡每張表該有幾筆 ─────────────────
python3 - "$WORK" <<'PY'
import sys, os, json, zipfile
w = sys.argv[1]
zipfile.ZipFile(os.path.join(w, "backup.zip")).extractall(w)
m = json.load(open(os.path.join(w, "data", "_manifest.json")))
print("備份時間(UTC):", m.get("timestamp"), "  來源 PG 版本:", m.get("source_major_version"))
if m.get("source_major_version") != 18:
    sys.exit("來源不是 PostgreSQL 18，與本機容器版本不符，停止")
PY
# COPY 的文字格式一列資料就是一行（換行會被跳脫），所以數行數就是筆數。
# 這份數字來自備份檔本身，不依賴 Zeabur，拿來對還原後的資料庫。
LC_ALL=C awk '
  !inb && /^COPY public\./ { t = $2; sub(/^public\./, "", t); n = 0; inb = 1; next }
  inb && $0 == "\."       { print t "|" n; inb = 0; next }
  inb                      { n++ }
' "$WORK/data/data.sql" | LC_ALL=C sort > "$WORK/expected.txt"
step "2 解壓並檢查"

# ── 3. 停 app：從這裡開始算停機 ─────────────────────────────────────
# app 不能在還原期間跑著：它啟動時會 create_all()，而且連線會擋住 DROP DATABASE。
DOWN0=$(date +%s)
docker compose stop app
step "3 停止 app（停機開始）"

# ── 4. 清掉舊的 ─────────────────────────────────────────────────────
"${PSQL[@]}" -d postgres -v ON_ERROR_STOP=1 -q -c "DROP DATABASE IF EXISTS zeabur WITH (FORCE);"
# dump 會 CREATE ROLE root；先刪掉舊的才不會報「已存在」。刪不掉也沒關係，下面會容許那一個錯誤。
"${PSQL[@]}" -d postgres -q -c "DROP ROLE IF EXISTS root;" || true
step "4 清掉舊資料庫"

# ── 5. 還原 ─────────────────────────────────────────────────────────
"${PSQL[@]}" -d postgres < "$WORK/data/data.sql" > "$WORK/restore.log" 2>&1
ERRORS=$(grep "ERROR" "$WORK/restore.log" | grep -vc 'role "root" already exists' || true)
step "5 還原資料"
if [ "$ERRORS" != "0" ]; then
  echo "還原出現 $ERRORS 個錯誤，app 保持停止。完整紀錄：$WORK/restore.log"
  grep "ERROR" "$WORK/restore.log" | head -10
  exit 1
fi

# ── 6. 換掉 dump 帶進來的密碼、比對筆數 ─────────────────────────────
# dump 內的 root 密碼雜湊是 Zeabur 那組已外洩的，不能留。
"${PSQL[@]}" -d postgres -v ON_ERROR_STOP=1 -q \
  -c "ALTER ROLE root WITH PASSWORD '${POSTGRES_PASSWORD}';"
"${PSQL[@]}" -d zeabur -At -F'|' -c "
  select table_name,
         (xpath('/row/c/text()',
                query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name),
                             false, true, '')))[1]::text::bigint
  from information_schema.tables
  where table_schema = 'public' and table_type = 'BASE TABLE'" \
  | LC_ALL=C sort > "$WORK/actual.txt"
if diff "$WORK/expected.txt" "$WORK/actual.txt" > "$WORK/count-diff.txt"; then
  echo "筆數比對：$(wc -l < "$WORK/actual.txt") 張表全部相符" | tee -a "$WORK/timing.txt"
else
  echo "筆數與備份檔不符，app 保持停止："
  cat "$WORK/count-diff.txt"
  exit 1
fi
step "6 改密碼並比對筆數"

# ── 7. 啟動 app，等到真的能回應頁面 ─────────────────────────────────
docker compose start app
code=000
for _ in $(seq 1 120); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL" || true)
  [ "$code" = "200" ] && break
  sleep 1
done
if [ "$code" != "200" ]; then
  echo "app 在 120 秒內沒有恢復（最後狀態碼 $code），請看 docker compose logs app"
  exit 1
fi
step "7 啟動 app 到可服務"
DOWN=$(( $(date +%s) - DOWN0 ))

# ── 8. 解密檢查：DB_ENCRYPTION_KEY 對不對 ───────────────────────────
docker compose exec -T app python - <<'PY' | tee -a "$WORK/timing.txt"
import os, psycopg2
from crypto import decrypt, is_encrypted
conn = psycopg2.connect(os.environ["DATABASE_URL"]); cur = conn.cursor()
total = bad = 0
for table, col in [("notes", "title"), ("notes", "content"), ("checklist_items", "text"),
                   ("announcements", "content"), ("private_notes", "content")]:
    cur.execute(f"select {col} from {table} where {col} is not null")
    for (v,) in cur:
        if is_encrypted(v):
            total += 1
            if str(decrypt(v)).startswith("⚠️"):
                bad += 1
print(f"解密檢查：加密資料 {total} 筆，解不開 {bad} 筆")
PY
step "8 解密檢查"

{
  echo "──────────────────────────────────────"
  echo "停機時間（停 app → 恢復服務）：${DOWN} 秒"
  echo "全程：$(( $(date +%s) - START )) 秒"
  echo "紀錄資料夾：$WORK"
} | tee -a "$WORK/timing.txt"
