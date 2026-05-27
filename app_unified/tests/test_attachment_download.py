"""Integration tests for GET /notes/api/attachments/<id>/download endpoint."""
import time
import uuid
import urllib.parse
from unittest.mock import MagicMock

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_presigned_url(params, expires_in):
    """Build a realistic-looking presigned URL from generate_presigned_url Params."""
    bucket = params["Bucket"]
    key = params["Key"]
    disposition = urllib.parse.quote(params.get("ResponseContentDisposition", ""), safe="%")
    content_type = urllib.parse.quote(params.get("ResponseContentType", ""), safe="%")
    return (
        f"https://fake-r2.example.com/{bucket}/{key}"
        f"?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        f"&X-Amz-Expires={expires_in}"
        f"&response-content-disposition={disposition}"
        f"&response-content-type={content_type}"
        f"&X-Amz-Signature=fakesig"
    )


def _login(client, user):
    """Bypass Flask-Login auth routes and set session directly."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_last_request_at"] = int(time.time())


def _create_store_user_note_attachment(db_session, store_name, role="user"):
    """Create Store + User + Note + NoteAttachment, return (user, attachment)."""
    from models import User, Note, NoteAttachment
    suffix = uuid.uuid4().hex[:8]

    user = User(username=f"u-{suffix}", store=store_name, role=role)
    user.set_password("pw")
    db_session.add(user)
    db_session.flush()

    note = Note(
        title="test note",
        content="",
        store=store_name,
        category="misc",
        user_id=user.id,
    )
    db_session.add(note)
    db_session.flush()

    attachment = NoteAttachment(
        note_id=note.id,
        user_id=user.id,
        object_key=f"attachments/{note.id}/abc.jpg",
        filename="abc.jpg",
        content_type="image/jpeg",
        file_size=1234,
    )
    db_session.add(attachment)
    db_session.commit()
    return user, attachment


# ── r2 mock fixture ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def r2_configured(app, monkeypatch):
    """Ensure app.config has R2 settings and _get_client returns a realistic mock."""
    import storage as storage_mod

    monkeypatch.setitem(app.config, "R2_ENDPOINT_URL", "https://fake-r2.example.com")
    monkeypatch.setitem(app.config, "R2_ACCESS_KEY_ID", "fake-key-id")
    monkeypatch.setitem(app.config, "R2_SECRET_ACCESS_KEY", "fake-secret")
    monkeypatch.setitem(app.config, "R2_BUCKET_NAME", "test-bucket")

    mock_client = MagicMock()

    def fake_generate_presigned_url(op, Params, ExpiresIn):
        return _make_presigned_url(Params, ExpiresIn)

    mock_client.generate_presigned_url.side_effect = fake_generate_presigned_url
    monkeypatch.setattr(storage_mod, "_get_client", lambda: mock_client)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_note_attachment_download_same_store_user_returns_url(app, client, db_session):
    """Same-store user gets 200 with a presigned URL."""
    store_name = f"ST-{uuid.uuid4().hex[:6]}"
    user, attachment = _create_store_user_note_attachment(db_session, store_name)

    _login(client, user)
    resp = client.get(f"/notes/api/attachments/{attachment.id}/download")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "url" in data
    assert data["url"].startswith("http")
    assert "response-content-disposition=" in data["url"].lower()
    assert data["expires_in"] == 300


def test_note_attachment_download_super_admin_cross_store_returns_url(app, client, db_session):
    """super_admin can download attachments from a different store."""
    from models import User
    # Attachment belongs to store A
    store_name = f"SA-{uuid.uuid4().hex[:6]}"
    _owner, attachment = _create_store_user_note_attachment(db_session, store_name)

    # super_admin belongs to a different store (or no store)
    sa = User(username=f"sa-{uuid.uuid4().hex[:6]}", role="super_admin", store=None)
    sa.set_password("pw")
    db_session.add(sa)
    db_session.commit()

    _login(client, sa)
    resp = client.get(f"/notes/api/attachments/{attachment.id}/download")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "url" in data
    assert data["url"].startswith("http")


def test_note_attachment_download_cross_store_user_returns_403(app, client, db_session):
    """User from a different store gets 403."""
    from models import User
    store_a = f"A-{uuid.uuid4().hex[:6]}"
    store_b = f"B-{uuid.uuid4().hex[:6]}"

    # Attachment in store_a
    _owner, attachment = _create_store_user_note_attachment(db_session, store_a)

    # User in store_b
    other_user = User(username=f"other-{uuid.uuid4().hex[:6]}", store=store_b, role="user")
    other_user.set_password("pw")
    db_session.add(other_user)
    db_session.commit()

    _login(client, other_user)
    resp = client.get(f"/notes/api/attachments/{attachment.id}/download")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "forbidden"


def test_note_attachment_download_unauthenticated_returns_401(app, client, db_session):
    """Unauthenticated request gets 401 or 302 (redirect to login)."""
    store_name = f"UN-{uuid.uuid4().hex[:6]}"
    _owner, attachment = _create_store_user_note_attachment(db_session, store_name)

    # fresh client — no session set
    fresh_client = app.test_client()
    resp = fresh_client.get(f"/notes/api/attachments/{attachment.id}/download")
    assert resp.status_code in (401, 302)


def test_note_attachment_download_nonexistent_id_returns_404(app, client, db_session):
    """Non-existent attachment ID returns 404."""
    from models import User
    user = User(username=f"u404-{uuid.uuid4().hex[:6]}", role="super_admin")
    user.set_password("pw")
    db_session.add(user)
    db_session.commit()

    _login(client, user)
    resp = client.get("/notes/api/attachments/99999999/download")
    assert resp.status_code == 404
