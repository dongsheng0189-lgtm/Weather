"""
pytest fixtures for app_unified tests.

Key mocks:
  - face_recognition: returns a fixed 128-dim vector (no dlib needed)
  - anthropic client: returns fixed text (no API cost)
  - R2 storage: no-op upload
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


@pytest.fixture
def logged_in_client(client, test_user):
    """Client with test_user logged in (has face encoding, must provide face_image)."""
    fake_b64 = 'data:image/jpeg;base64,/9j/4AAQSkZJRg=='
    res = client.post('/auth/login', json={
        'username': test_user.username, 'pin': '1234', 'face_image': fake_b64
    })
    assert res.status_code == 200, f"Login failed: {res.status_code} {res.get_data(as_text=True)}"
    assert res.get_json()['status'] == 'ok', f"Login not ok: {res.get_json()}"
    return client


@pytest.fixture
def logged_in_admin_client(client, admin_user):
    """Client with admin_user logged in (admin has no face → need_face_enroll status)."""
    res = client.post('/auth/login', json={'username': admin_user.username, 'pin': '0000'})
    assert res.status_code == 200, f"Admin login failed: {res.get_data(as_text=True)}"
    data = res.get_json()
    # Admin has no face encoding → server still logs them in and returns need_face_enroll
    assert data['status'] in ('ok', 'need_face_enroll'), f"Unexpected: {data}"
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
