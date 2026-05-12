import os
from dotenv import load_dotenv

# dotenv loads `.env` by default; we renamed prod creds to `.env.prod` on
# 2026-05-12 so a naked `python app.py` cannot accidentally hit prod PG.
# Local dev sources `.env.local` explicitly via scripts/run_local.sh.
load_dotenv()


# Hard guard: when FLASK_DEV_MODE=1 the app MUST be on a local sqlite DB.
# Prevents an accidental prod env-var leak from writing to live PostgreSQL.
_dev_mode = os.getenv("FLASK_DEV_MODE") == "1"
_db_url_raw = os.getenv("DATABASE_URL", "sqlite:///./app.db")
if _dev_mode and not _db_url_raw.startswith("sqlite:"):
    raise RuntimeError(
        f"FLASK_DEV_MODE=1 requires a sqlite DATABASE_URL, got {_db_url_raw!r}. "
        "Refusing to boot — pointing dev mode at a non-sqlite DB risks corrupting prod data."
    )


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

    # Database — defaults to SQLite for local dev, set DATABASE_URL for PostgreSQL
    _db_url = _db_url_raw
    # Zeabur PostgreSQL uses postgres:// scheme; SQLAlchemy needs postgresql://
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pool_pre_ping: checkout-time validation catches dead connections (PG
    #   server idle-timeout closed them) before they reach a query.
    # pool_recycle: proactively recycle connections every hour so they don't
    #   age out to where pre_ping has to fail; defense in depth.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 3600}

    # Cookie security (set True in production via env var)
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # API keys
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY", "")

    # Gemini (Google AI, free tier with 1.5 Flash)
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

    # Cloudflare R2 (S3-compatible)
    R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL", "")
    R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
    R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "face-photos")
    R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "")  # e.g. https://pub-xxx.r2.dev

    # Rate limiting storage
    RATELIMIT_STORAGE_URL = os.getenv("RATELIMIT_STORAGE_URL", "memory://")

    # CORS allowed origins (comma-separated)
    ALLOWED_ORIGINS = [
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5000").split(",")
        if o.strip()
    ]
