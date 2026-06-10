FROM python:3.12-slim

LABEL "language"="python"
LABEL "framework"="flask"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libopenblas0 \
    liblapack3 \
    && rm -rf /var/lib/apt/lists/*

# 提前建 user，後面 chown / --chown 都靠它
RUN useradd -m -u 1000 appuser

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Pre-built wheels — no compilation, no git needed
COPY app_unified/wheels/ /tmp/wheels/
RUN pip install --no-cache-dir --no-deps /tmp/wheels/*.whl && rm -rf /tmp/wheels

# Build context is repo root — must use app_unified/ prefix
COPY app_unified/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# chown site-packages 拆獨立一層 — cached forever 直到 wheels/requirements 變動
RUN chown -R appuser:appuser /usr/local/lib/python3.12/site-packages

# Pre-load MiniLM model into image (saves 5-15s on first AI summary after redeploy)
# chown /opt/st_cache 跟 model 預載合併同層，cache 一起
ENV SENTENCE_TRANSFORMERS_HOME=/opt/st_cache
ENV HF_HUB_ETAG_TIMEOUT=30
# 重試 + 指數退避：HuggingFace 偶爾對 CI runner IP 回 HTTP 429（rate limit），
# 之前讓整個 build 掛掉、image 沒更新、部署默默沒發生（incident 2026-06-10）。
# 任一次成功即 break（model 進 /opt/st_cache cache）；6 次全失敗才讓 build fail。
RUN set -e; \
    for i in 1 2 3 4 5 6; do \
      echo "Model preload attempt $i/6..."; \
      if python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"; then \
        ok=1; break; \
      fi; \
      echo "attempt $i failed (likely HuggingFace HTTP 429); backing off $((i*15))s"; \
      sleep $((i*15)); \
    done; \
    if [ -z "$ok" ]; then echo "ERROR: model preload failed after 6 attempts"; exit 1; fi; \
    chown -R appuser:appuser /opt/st_cache

# 驗證 wheels 已安裝（face_recognition 需要 pkg_resources shim，在 wsgi.py 提供）
RUN python -c "import dlib; print('dlib OK')"

# 用 --chown 直接帶 ownership，不再需要 COPY 後再 chown
# 這是唯一受 .py 改動觸發 rebuild 的層
COPY --chown=appuser:appuser app_unified/ .

USER appuser

EXPOSE 8080

ENV PYTHONUNBUFFERED=1
ENV SESSION_COOKIE_SECURE=true

# GHA push 時自動帶 commit SHA 進來；管理頁面顯示前 4 碼供版本比對
# 放在最後不影響上面 layer 的 cache（每次 commit 一定會變）
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
