#!/bin/bash
# Zeabur 容器診斷工具 — slim image 沒 ps/curl 也能用
# 用法（在容器內 shell）：
#   bash /app/scripts/zeabur_diag.sh
# 若 image 還沒 bake 進去，貼這段一次完成：
#   cat > /tmp/d.sh << 'EOF'
#   ...（本檔內容）...
#   EOF
#   bash /tmp/d.sh

set +e
echo "===== HOST / TIME ====="
hostname
date
echo

echo "===== PROCESSES (/proc) ====="
for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    comm=$(cat /proc/$p/comm 2>/dev/null)
    cmd=$(cat /proc/$p/cmdline 2>/dev/null | tr '\0' ' ')
    [ -n "$comm" ] && echo "PID $p [$comm] $cmd"
done
echo

echo "===== LISTENING PORTS (state 0A=LISTEN) ====="
echo "tcp4:"
awk 'NR>1 && $4=="0A" {print $2}' /proc/net/tcp 2>/dev/null
echo "tcp6:"
awk 'NR>1 && $4=="0A" {print $2}' /proc/net/tcp6 2>/dev/null
echo

echo "===== HEALTHZ INTERNAL CHECK ====="
python3 - <<'PY'
import urllib.request, time, socket
url = "http://127.0.0.1:8080/healthz"
t = time.time()
try:
    r = urllib.request.urlopen(url, timeout=10)
    body = r.read(200)
    print(f"OK  HTTP {r.status}  body={body!r}  elapsed={time.time()-t:.2f}s")
except urllib.error.HTTPError as e:
    print(f"HTTP_ERROR {e.code}  elapsed={time.time()-t:.2f}s  body={e.read()[:200]!r}")
except (socket.timeout, urllib.error.URLError, ConnectionRefusedError) as e:
    print(f"CONN_FAIL  elapsed={time.time()-t:.2f}s  err={type(e).__name__}: {e}")
except Exception as e:
    print(f"OTHER  elapsed={time.time()-t:.2f}s  err={type(e).__name__}: {e}")
PY
echo

echo "===== /tmp/app.log (last 200 lines) ====="
if [ -f /tmp/app.log ]; then
    ls -la /tmp/app.log
    echo "--- tail ---"
    tail -200 /tmp/app.log
else
    echo "/tmp/app.log NOT FOUND (新容器剛起，或 RotatingFileHandler 還沒寫進)"
fi
echo

echo "===== ENV (subset) ====="
env | grep -E "^(PORT|GIT_SHA|APP_MODE|DATABASE_URL|SQLALCHEMY|HF_HOME|SENTENCE_TRANSFORMERS_HOME|PYTHONUNBUFFERED)" | sort
echo

echo "===== DONE ====="
