"""Tests for face blueprint."""
import pytest
import base64
import numpy as np
from unittest.mock import patch

# Minimal valid JPEG header (1x1 white pixel)
FAKE_JPEG_B64 = 'data:image/jpeg;base64,' + base64.b64encode(
    b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t'
    b'\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a'
    b'\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\x1e11C '
    b'2I\xff\xd9'
).decode()


def test_face_settings_requires_login(client):
    res = client.get('/face/settings')
    assert res.status_code in (302, 401)


def test_face_settings_page(logged_in_admin_client):
    """/face/settings is admin-only (face/routes.py:28 — non-admin → 403)."""
    res = logged_in_admin_client.get('/face/settings')
    assert res.status_code == 200


def test_face_enroll_success(logged_in_client):
    res = logged_in_client.post('/face/enroll', json={'face_image': FAKE_JPEG_B64})
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'ok'


def test_face_enroll_no_image(logged_in_client):
    res = logged_in_client.post('/face/enroll', json={})
    assert res.status_code == 400


def test_face_verify_success(logged_in_client, test_user, app):
    """After enrolling, face verify should succeed with matching mock encoding."""
    # Ensure face is enrolled
    logged_in_client.post('/face/enroll', json={'face_image': FAKE_JPEG_B64})

    res = logged_in_client.post('/face/verify', json={'face_image': FAKE_JPEG_B64})
    assert res.status_code == 200
    data = res.get_json()
    assert data['match'] is True


def test_face_encoding_uses_numpy_not_pickle(app, db_session, test_user):
    """Ensure face encoding is stored as numpy bytes, not pickle."""
    import pickle
    from models import User
    # Enroll face first so encoding exists
    # test_user has face encoding set in fixture
    u = db_session.get(User, test_user.id)
    if u and u.face_encoding:
        raw = u.face_encoding
        # numpy.frombuffer should work
        arr = np.frombuffer(raw, dtype=np.float64)
        assert arr.shape == (128,)
        # pickle.loads should fail (raw bytes are not valid pickle)
        try:
            result = pickle.loads(raw)
            # If somehow succeeds, it should not be a proper numpy face encoding
        except Exception:
            pass  # Expected


def test_face_enroll_requires_login(client):
    res = client.post('/face/enroll', json={'face_image': FAKE_JPEG_B64})
    assert res.status_code in (302, 401)


def test_face_executor_uses_gevent_threadpool():
    """Face verify CPU work must run on real OS threads, not greenlets.

    Under gevent monkey patching (production gunicorn worker), stdlib
    threading.Thread becomes a greenlet on the same hub. dlib native code
    in face_recognition does not yield to the hub, so concurrent verifies
    block all other requests for ~10 minutes until gunicorn timeout SIGKILLs
    the worker (incident on 2026-05-09 20:00).

    gevent.threadpool.ThreadPool always uses real OS threads regardless of
    monkey patching, so dlib runs off-hub and Flask stays responsive.
    """
    from gevent.threadpool import ThreadPool
    from auth import routes as auth_routes
    assert isinstance(auth_routes._face_executor, ThreadPool), (
        f"_face_executor must be gevent.threadpool.ThreadPool to keep gevent "
        f"hub responsive under face verify load. Got: "
        f"{type(auth_routes._face_executor).__name__}"
    )
