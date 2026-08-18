"""
pytest fixtures for app_unified tests.

Key mocks:
  - face_recognition: returns a fixed 128-dim vector (no dlib needed)
  - anthropic client: returns fixed text (no API cost)
  - R2 storage: no-op upload
  - sentence_transformers + sklearn: heavy ML deps not in local venv (mocked at sys.modules level)
"""
import sys
import os
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

# Add app_unified to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ── Mock face_recognition before app import ──────────────────────────────────
FAKE_ENCODING = np.random.default_rng(42).random(128).astype(np.float64)

mock_face_recognition = MagicMock()
mock_face_recognition.load_image_file.return_value = np.zeros((480, 640, 3), dtype=np.uint8)
mock_face_recognition.face_encodings.return_value = [FAKE_ENCODING]
mock_face_recognition.face_distance.return_value = np.array([0.2])
mock_face_recognition.compare_faces.return_value = [True]
sys.modules['face_recognition'] = mock_face_recognition

# ── Mock boto3 / botocore ─────────────────────────────────────────────────────
mock_boto3 = MagicMock()
mock_s3_client = MagicMock()
mock_s3_client.put_object.return_value = {}
mock_boto3.client.return_value = mock_s3_client
sys.modules['boto3'] = mock_boto3
sys.modules['botocore'] = MagicMock()
sys.modules['botocore.exceptions'] = MagicMock()

# ── Mock sentence_transformers + sklearn (heavy ML deps, only present in
# prod Docker image; local venv intentionally doesn't install them) ────────
mock_st = MagicMock()
mock_sentence_model = MagicMock()
mock_sentence_model.encode.side_effect = lambda texts, **kw: np.zeros(
    (len(texts) if hasattr(texts, '__len__') else 1, 384), dtype=np.float32
)
mock_st.SentenceTransformer.return_value = mock_sentence_model
sys.modules['sentence_transformers'] = mock_st

mock_sklearn = MagicMock()
mock_sklearn_cluster = MagicMock()
# DBSCAN.fit(...).labels_ — 預設回 single-cluster label 0；單元測試會 override
mock_sklearn_cluster.DBSCAN.return_value.fit.return_value.labels_ = np.array([0])
mock_sklearn.cluster = mock_sklearn_cluster
sys.modules['sklearn'] = mock_sklearn
sys.modules['sklearn.cluster'] = mock_sklearn_cluster


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_ratelimit_forensics_reset: 退出 _reset_ratelimit_forensics autouse "
        "fixture，供真的要測試 monitor_ship 寄送行為的測試檔使用。",
    )


@pytest.fixture(autouse=True)
def _reset_ratelimit_forensics(request, monkeypatch):
    """限流取證彙總器（ratelimit_forensics._default）是 process 全域單例，且
    429 handler 命中時會 spawn 一個沒被 drain 的 greenlet 去送真實網路請求。

    沒有這個 fixture，`tests/test_ratelimit_client_ip_logging.py` 裡沒有手動
    patch 掉寄送函式的測試（例如單純測 log 內容那個）就會動到這個全域單例，
    而那個 greenlet 是在**執行當下**才讀 MONITOR_INGEST_URL / TOKEN —— 今天
    CI 沒設那兩個 env 才剛好是 no-op；開發者在有 `.env.local` 或 prod-ish env
    的 shell 裡跑測試，會真的把事件送到線上 monitor。

    這裡把 `_default` 重設為 None（下次呼叫 default_aggregator() 會拿到全新
    實例），並把 monitor_ship 的兩個寄送函式 stub 掉。個別測試仍可用自己的
    monkeypatch 覆寫（monkeypatch 後蓋前，同一個 fixture 實例疊加沒問題）。

    tests/test_monitor_ship_from_request.py 就是要測「真正的寄送行為」（用假
    的本機 capture server），必須不套用這層 stub —— 該檔案標了
    `pytestmark = pytest.mark.no_ratelimit_forensics_reset` 退出這個 fixture。
    """
    if "no_ratelimit_forensics_reset" in request.keywords:
        yield
        return

    import ratelimit_forensics
    monkeypatch.setattr(ratelimit_forensics, "_default", None)

    import monitor_ship

    def _noop_bool(*a, **kw):
        return False

    def _noop_none(*a, **kw):
        return None

    monkeypatch.setattr(monitor_ship, "ship_event_from_request", _noop_bool)
    monkeypatch.setattr(monitor_ship, "ship_event_from_request_async", _noop_none)

    # app.py 用 `from monitor_ship import ship_event_from_request_async` 拷貝了
    # 一份獨立綁定，patch monitor_ship 模組本身不會影響它，得另外 patch。只在
    # `app` 模組已經被 import 過才動它 —— 不強迫在這裡觸發 import app（那要求
    # DB_ENCRYPTION_KEY 等 env 已就緒，屬於 `app` fixture 的職責，不該由這個
    # 與其無關的 autouse fixture 提前觸發）。
    if "app" in sys.modules:
        monkeypatch.setattr(sys.modules["app"], "ship_event_from_request_async", _noop_none)

    yield


@pytest.fixture(scope='session')
def app():
    """Create test Flask app with in-memory SQLite.

    IMPORTANT: We do NOT keep a persistent app_context() pushed here.
    Flask's `g` is app-context scoped; if we keep an outer context alive,
    all requests share the same `g`, causing Flask-Login state to leak
    between tests. Instead, each fixture/test pushes its own short-lived
    app context, and each test client request pushes its own fresh one.
    """
    os.environ.setdefault('SECRET_KEY', 'test-secret')
    os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
    # DB_ENCRYPTION_KEY must be set before models are imported; use a fixed test key
    # so encrypted columns (Note.title, Note.content, etc.) can be written in tests.
    import base64
    os.environ.setdefault(
        'DB_ENCRYPTION_KEY',
        base64.urlsafe_b64encode(b'test-key-32-bytes-for-unit-tests').decode(),
    )

    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    application.config['WTF_CSRF_ENABLED'] = False
    application.config['RATELIMIT_ENABLED'] = False

    # StaticPool: all connections (across contexts) share the same in-memory DB.
    from sqlalchemy.pool import StaticPool
    application.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    application.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {'check_same_thread': False},
        'poolclass': StaticPool,
    }

    # Create schema once, then pop the context immediately
    from extensions import db
    with application.app_context():
        db.drop_all()
        db.create_all()

    yield application

    # Teardown: drop all tables
    with application.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db_session(app):
    """Yield the SQLAlchemy session within a fresh, short-lived app context."""
    from extensions import db
    ctx = app.app_context()
    ctx.push()
    try:
        yield db.session
    finally:
        db.session.remove()
        ctx.pop()


@pytest.fixture(autouse=True)
def _clean_vetting_whitelist(app):
    """白名單表沒有 per-test rollback 隔離（app 為 session-scoped、db_session 不回滾），
    且白名單測試用固定號碼會跨檔互撞 → 每個 test 前後清空此表。"""
    from extensions import db
    from models import VettingWhitelistPhone
    with app.app_context():
        VettingWhitelistPhone.query.delete()
        db.session.commit()
    yield
    with app.app_context():
        VettingWhitelistPhone.query.delete()
        db.session.commit()


@pytest.fixture
def test_user(app, db_session):
    """Create and return a test user (not admin)."""
    from models import User, Note
    # Use unique username to avoid conflicts across tests
    import uuid
    uname = f'testuser_{uuid.uuid4().hex[:6]}'
    u = User(username=uname)
    u.set_password('1234')
    u.set_face_encoding(FAKE_ENCODING)
    db_session.add(u)
    db_session.commit()
    yield u
    # Clean up related records first (FK constraint)
    try:
        Note.query.filter_by(user_id=u.id).delete()
        db_session.commit()
        db_session.delete(u)
        db_session.commit()
    except Exception:
        db_session.rollback()


@pytest.fixture
def admin_user(app, db_session):
    """Create and return an admin user."""
    from models import User
    import uuid
    uname = f'admin_{uuid.uuid4().hex[:6]}'
    u = User(username=uname, role='admin')
    u.set_password('0000')
    db_session.add(u)
    db_session.commit()
    yield u
    try:
        db_session.delete(u)
        db_session.commit()
    except Exception:
        db_session.rollback()


def _login_via_session(client, user):
    """Bypass auth routes by setting Flask-Login session keys directly.

    Background: 4/15 commit 9a6b3ef reverted /auth/login back to a 302-stub;
    the real flow runs through /auth/verify (face modal + PIN). Pre-revert
    fixtures POSTed to /auth/login expecting JSON 200 — that path is gone.
    These fixtures bypass the route entirely so tests of *protected* endpoints
    (notes/face/admin) don't depend on auth-route plumbing.

    Sets _last_request_at so _enforce_session_idle doesn't kick the user out
    on the next request (the real route does the same).
    """
    import time
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True
        sess['_last_request_at'] = int(time.time())


@pytest.fixture
def logged_in_client(client, test_user):
    """Client with test_user logged in via Flask-Login session bypass."""
    _login_via_session(client, test_user)
    return client


@pytest.fixture
def logged_in_admin_client(client, admin_user):
    """Client with admin_user logged in via Flask-Login session bypass."""
    _login_via_session(client, admin_user)
    return client


@pytest.fixture
def mock_anthropic():
    """Mock Anthropic client to avoid API calls."""
    mock = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='Mock AI response')]
    mock.messages.create.return_value = mock_msg

    with patch.dict('sys.modules', {'anthropic': MagicMock(Anthropic=lambda **kw: mock)}):
        yield mock
