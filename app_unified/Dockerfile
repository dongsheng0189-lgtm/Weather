# App image: only the source code, layered on the pre-baked base image
# (ghcr.io/stvlin1988-alt/weather-base — see app_unified/Dockerfile.base +
# .github/workflows/docker-base.yml). torch / MiniLM model / dlib / system libs /
# SENTENCE_TRANSFORMERS_HOME are all INHERITED from the base.
# Floating tag is safe here: this base is our own (only our CI pushes it), unlike
# the upstream python:3.12-slim rolling tag that caused cold cache busts.
FROM ghcr.io/stvlin1988-alt/weather-base:latest

WORKDIR /app

# 唯一受 .py 改動觸發 rebuild 的層；--chown 直接帶 ownership（appuser 來自 base）
COPY --chown=appuser:appuser app_unified/ .

USER appuser

EXPOSE 8080

ENV PYTHONUNBUFFERED=1
ENV SESSION_COOKIE_SECURE=true

# GHA push 時自動帶 commit SHA 進來；管理頁面顯示前 4 碼供版本比對
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
