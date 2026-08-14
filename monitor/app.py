"""Monitor service — 獨立於 app-unified 的取證服務。

刻意保持極簡：純 sync Flask，不用 gevent、不 monkey patch。
app-unified 出問題時它必須還活著，所以它跟 app-unified 共用的東西越少越好。
"""
import hmac
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from models import db, Event

# 白名單：只收診斷事件。擴大範圍前必須先補欄位加密（見 models.py 說明）。
ALLOWED_KINDS = {"hub_block"}


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_url()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())
    db.init_app(app)

    with app.app_context():
        db.create_all()

    _register_routes(app)

    if os.environ.get("MONITOR_DISABLE_PROBER") != "1":
        from prober import start_prober
        start_prober(app)

    return app


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL 未設定")
    # Zeabur 的 ${VAR} 引用只在「該 service 作用域內有那個變數」時才會展開。
    # 把 app-unified 的連線字串整串複製過來時，裡面的引用不會被解析，會原封
    # 不動變成字面值 —— SQLAlchemy 只會丟出 "invalid literal for int()" 這種
    # 看不出原因的錯（2026-08-14 建 monitor 時實際踩到）。這裡先擋下來講清楚。
    if "${" in url:
        raise RuntimeError(
            f"DATABASE_URL 含有未展開的變數引用：{url!r}\n"
            "請到 Postgres service 複製「已展開」的完整連線字串"
            "（host/port 是實際值、沒有 ${...}），而不是從別的 service 複製。")
    # Zeabur 給的是 postgres://，SQLAlchemy 2.x 只認 postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _token_ok() -> bool:
    expected = os.environ.get("MONITOR_INGEST_TOKEN", "")
    header = request.headers.get("Authorization", "")
    if not expected or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:], expected)


def _register_routes(app: Flask) -> None:
    @app.route("/healthz")
    def healthz():
        return jsonify(status="ok"), 200

    @app.route("/ingest", methods=["POST"])
    def ingest():
        if not _token_ok():
            return jsonify(error="unauthorized"), 401

        data = request.get_json(silent=True) or {}
        kind = data.get("kind")
        summary = data.get("summary")
        if kind not in ALLOWED_KINDS:
            return jsonify(error=f"kind not allowed: {kind!r}"), 400
        if not summary:
            return jsonify(error="summary required"), 400

        db.session.add(Event(
            ts=datetime.now(timezone.utc),
            source=data.get("source", "app-unified"),
            kind=kind,
            severity=data.get("severity", "warning"),
            summary=str(summary)[:500],
            detail=data.get("detail"),
        ))
        db.session.commit()
        return jsonify(status="stored"), 201

    from views import register_views
    register_views(app)
