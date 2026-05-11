"""Tests for auth blueprint."""
import pytest
import json


def test_register_requires_admin(client):
    """Unauthenticated users cannot register."""
    res = client.post('/auth/register', json={'username': 'newuser_reg', 'pin': '5678'})
    assert res.status_code in (401, 302, 403)


def test_register_non_admin_forbidden(logged_in_client):
    """Regular (non-admin) users get 403 on register."""
    res = logged_in_client.post('/auth/register', json={'username': 'newuser_reg', 'pin': '5678'})
    assert res.status_code == 403


def test_register_success(logged_in_admin_client):
    res = logged_in_admin_client.post('/auth/register', json={'username': 'newuser_reg', 'pin': '5678'})
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'ok'
    assert 'user_id' in data


def test_register_duplicate(logged_in_admin_client):
    logged_in_admin_client.post('/auth/register', json={'username': 'dupuser', 'pin': '1111'})
    res = logged_in_admin_client.post('/auth/register', json={'username': 'dupuser', 'pin': '2222'})
    assert res.status_code == 409


def test_register_missing_fields(logged_in_admin_client):
    res = logged_in_admin_client.post('/auth/register', json={'username': '', 'pin': ''})
    assert res.status_code == 400


def test_login_page_get_redirects_to_weather(client):
    """4/15 revert: /auth/login is now a stub 302 redirect to weather.

    The real login flow is /auth/verify (face modal + PIN, no username field).
    Pre-revert behavior (200 + login form) is gone. Tests for the old POST
    behavior (face_required / need_face_enroll / wrong_pin / etc.) were
    deleted because that logic moved to /auth/verify, which has its own
    coverage in test_verify_* below.
    """
    res = client.get('/auth/login', follow_redirects=False)
    assert res.status_code == 302
    assert '/weather' in res.headers['Location']


def test_verify_success(client, test_user):
    """Face + PIN verify — server sets session."""
    fake_b64 = 'data:image/jpeg;base64,/9j/4AAQSkZJRg=='  # minimal JPEG header
    res = client.post('/auth/verify', json={'pin': '1234', 'face_image': fake_b64})
    data = res.get_json()
    assert data['status'] == 'ok'


def test_verify_wrong_pin(client, test_user):
    fake_b64 = 'data:image/jpeg;base64,/9j/4AAQSkZJRg=='
    res = client.post('/auth/verify', json={'pin': '9999', 'face_image': fake_b64})
    data = res.get_json()
    assert data['status'] == 'wrong_password'


def test_verify_no_face_image(client, test_user):
    res = client.post('/auth/verify', json={'pin': '1234'})
    data = res.get_json()
    assert data['status'] == 'face_mismatch'


def test_silent_redirect_requires_login(client):
    res = client.get('/auth/s/r')
    assert res.status_code in (302, 401)


def test_logout(logged_in_client):
    res = logged_in_client.get('/auth/logout', follow_redirects=False)
    assert res.status_code == 302


# ── Admin create user endpoint ────────────────────────────────────────────────

@pytest.fixture
def store_a(db_session):
    """A valid Store record — needed because /admin/users/create requires
    role=user/admin to pick a store from existing Store rows."""
    from models import Store
    s = Store(name='store_a', login_enabled=True)
    db_session.add(s)
    db_session.commit()
    yield s
    try:
        db_session.delete(s)
        db_session.commit()
    except Exception:
        db_session.rollback()


def test_admin_create_user_success(logged_in_admin_client, store_a):
    fake_b64 = 'data:image/jpeg;base64,/9j/4AAQSkZJRg=='
    res = logged_in_admin_client.post('/admin/users/create', json={
        'username': 'employee01', 'pin': '2468', 'face_image': fake_b64,
        'store': store_a.name,
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'ok'
    assert data['username'] == 'employee01'
    assert 'user_id' in data


def test_admin_create_user_missing_fields(logged_in_admin_client):
    res = logged_in_admin_client.post('/admin/users/create', json={'username': '', 'pin': ''})
    assert res.status_code == 400


def test_admin_create_user_duplicate(logged_in_admin_client, store_a):
    logged_in_admin_client.post('/admin/users/create', json={
        'username': 'emp_dup', 'pin': '1111', 'store': store_a.name,
    })
    res = logged_in_admin_client.post('/admin/users/create', json={
        'username': 'emp_dup', 'pin': '2222', 'store': store_a.name,
    })
    assert res.status_code == 409


def test_admin_create_user_requires_admin(logged_in_client):
    res = logged_in_client.post('/admin/users/create', json={'username': 'emp_x', 'pin': '1234'})
    assert res.status_code == 403


def test_app_mode_notes_redirect(app):
    """APP_MODE=notes → root redirects to /auth/login."""
    import os
    os.environ['APP_MODE'] = 'notes'
    try:
        with app.test_client() as c:
            res = c.get('/', follow_redirects=False)
            assert res.status_code == 302
            assert '/auth/login' in res.headers['Location']
    finally:
        del os.environ['APP_MODE']


def test_app_mode_default_weather_redirect(app):
    """No APP_MODE → root redirects to weather."""
    import os
    os.environ.pop('APP_MODE', None)
    with app.test_client() as c:
        res = c.get('/', follow_redirects=False)
        assert res.status_code == 302
        assert '/weather' in res.headers['Location']
