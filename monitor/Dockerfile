# build context = monitor/（不是 repo root）。monitor 必須自給自足，
# 不得引用 app_unified 的任何檔案 —— 兩個 container 誰崩誰活互不波及。
FROM python:3.12-slim

# dig：probe_core 用它問指定 resolver，才分得出是不是 DNS 問題
RUN apt-get update && apt-get install -y --no-install-recommends \
    dnsutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
ENV PYTHONUNBUFFERED=1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
