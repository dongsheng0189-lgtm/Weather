import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["MONITOR_PASSWORD"] = "test-pw"
    os.environ["MONITOR_INGEST_TOKEN"] = "test-token"
    os.environ["MONITOR_DISABLE_PROBER"] = "1"   # 測試不要背景執行緒亂跑
    from app import create_app
    application = create_app()
    with application.app_context():
        from models import db
        db.create_all()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()
