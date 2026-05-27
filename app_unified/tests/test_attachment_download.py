"""Integration tests for GET /notes/api/attachments/<id>/download endpoint."""
import time
import uuid

import pytest

from tests._r2_fixture import install_r2_mock


# ── helpers ──────────────────────────────────────────────────────────────────

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
    """Ensure app.config has R2 settings and _get_client returns a realistic mock.

    Yields the mock_client so individual tests can override side_effect when
    needed (e.g. to simulate R2 outage for the 503 test).
    """
    yield install_r2_mock(monkeypatch, app)


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
    """User from a different store gets 403 with project-standard error shape."""
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
    body = resp.get_json()
    assert body.get("status") == "error"
    assert "權限" in body.get("message", "")


def test_note_attachment_download_unauthenticated_returns_401(app, client, db_session):
    """Unauthenticated request gets 401 or 302 (redirect to login)."""
    store_name = f"UN-{uuid.uuid4().hex[:6]}"
    _owner, attachment = _create_store_user_note_attachment(db_session, store_name)

    # fresh client — no session set
    fresh_client = app.test_client()
    resp = fresh_client.get(f"/notes/api/attachments/{attachment.id}/download")
    assert resp.status_code in (401, 302)


def test_note_attachment_download_nonexistent_id_returns_404(app, client, db_session):
    """Non-existent attachment ID returns 404 with project-standard error shape."""
    from models import User
    user = User(username=f"u404-{uuid.uuid4().hex[:6]}", role="super_admin")
    user.set_password("pw")
    db_session.add(user)
    db_session.commit()

    _login(client, user)
    resp = client.get("/notes/api/attachments/99999999/download")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body.get("status") == "error"
    assert body.get("message") == "附件不存在"


def test_note_attachment_download_storage_unavailable_returns_503(
    monkeypatch, app, client, db_session, r2_configured
):
    """get_download_url returning None (R2 misconfigured / outage) → 503."""
    store_name = f"503-{uuid.uuid4().hex[:6]}"
    user, attachment = _create_store_user_note_attachment(db_session, store_name)
    _login(client, user)

    # Blank out R2 endpoint to force get_download_url → None
    monkeypatch.setitem(app.config, "R2_ENDPOINT_URL", "")

    resp = client.get(f"/notes/api/attachments/{attachment.id}/download")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body.get("status") == "error"
    assert "儲存" in body.get("message", "") or "服務" in body.get("message", "")


# ── AnnouncementAttachment download endpoint ───────────────────────────────────

def _create_announcement_attachment(db_session, store=None):
    """Create User + Announcement + AnnouncementAttachment.

    store=None → global announcement (visible to all logged-in users).
    store=<str> → store-scoped announcement.

    Returns (user, attachment) where user belongs to the same store
    (or a fresh store when store=None so the caller can log in as them).
    """
    from models import User, Announcement, AnnouncementAttachment
    suffix = uuid.uuid4().hex[:8]

    # If store is None (global), put the "owner" user in some arbitrary store
    user_store = store if store is not None else f"OWNER-{suffix}"
    user = User(username=f"ann-u-{suffix}", store=user_store, role="admin")
    user.set_password("pw")
    db_session.add(user)
    db_session.flush()

    announcement = Announcement(
        user_id=user.id,
        author_name=user.username,
        title=f"Test announcement {suffix}",
        content="content",
        store=store,
    )
    db_session.add(announcement)
    db_session.flush()

    attachment = AnnouncementAttachment(
        announcement_id=announcement.id,
        user_id=user.id,
        object_key=f"announcements/{announcement.id}/doc.pdf",
        filename="doc.pdf",
        content_type="application/pdf",
        file_size=2048,
    )
    db_session.add(attachment)
    db_session.commit()
    return user, attachment


def test_ann_attachment_download_same_store_user_returns_url(app, client, db_session):
    """Same-store user gets 200 with presigned URL + Content-Disposition + expires_in=300."""
    store_name = f"ANN-ST-{uuid.uuid4().hex[:6]}"
    from models import User
    suffix = uuid.uuid4().hex[:8]
    # Create a regular same-store user (not admin) to download
    owner, attachment = _create_announcement_attachment(db_session, store=store_name)
    viewer = User(username=f"viewer-{suffix}", store=store_name, role="user")
    viewer.set_password("pw")
    db_session.add(viewer)
    db_session.commit()

    _login(client, viewer)
    resp = client.get(f"/notes/announcement/api/attachments/{attachment.id}/download")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "url" in data
    assert data["url"].startswith("http")
    assert "response-content-disposition=" in data["url"].lower()
    assert data["expires_in"] == 300


def test_ann_attachment_download_global_store_visible_to_all(app, client, db_session):
    """Announcement with store=NULL is visible to any logged-in user."""
    from models import User
    suffix = uuid.uuid4().hex[:8]
    _owner, attachment = _create_announcement_attachment(db_session, store=None)

    # A user from some completely unrelated store
    other_user = User(username=f"other-{suffix}", store=f"OTHER-{suffix}", role="user")
    other_user.set_password("pw")
    db_session.add(other_user)
    db_session.commit()

    _login(client, other_user)
    resp = client.get(f"/notes/announcement/api/attachments/{attachment.id}/download")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "url" in data
    assert data["url"].startswith("http")


def test_ann_attachment_download_super_admin_cross_store_returns_url(app, client, db_session):
    """super_admin can download attachments from a different store."""
    from models import User
    store_name = f"ANN-SA-{uuid.uuid4().hex[:6]}"
    _owner, attachment = _create_announcement_attachment(db_session, store=store_name)

    sa = User(username=f"sa-ann-{uuid.uuid4().hex[:6]}", role="super_admin", store=None)
    sa.set_password("pw")
    db_session.add(sa)
    db_session.commit()

    _login(client, sa)
    resp = client.get(f"/notes/announcement/api/attachments/{attachment.id}/download")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "url" in data
    assert data["url"].startswith("http")


def test_ann_attachment_download_cross_store_non_admin_returns_403(app, client, db_session):
    """Non-super_admin user from a different store gets 403."""
    from models import User
    store_x = f"X-{uuid.uuid4().hex[:6]}"
    store_y = f"Y-{uuid.uuid4().hex[:6]}"

    _owner, attachment = _create_announcement_attachment(db_session, store=store_x)

    other_user = User(username=f"cross-{uuid.uuid4().hex[:6]}", store=store_y, role="user")
    other_user.set_password("pw")
    db_session.add(other_user)
    db_session.commit()

    _login(client, other_user)
    resp = client.get(f"/notes/announcement/api/attachments/{attachment.id}/download")
    assert resp.status_code == 403
    body = resp.get_json()
    assert body.get("status") == "error"
    assert "權限" in body.get("message", "")


def test_ann_attachment_download_unauthenticated_returns_401(app, client, db_session):
    """Unauthenticated request gets 401 or 302 (redirect to login)."""
    store_name = f"ANN-UN-{uuid.uuid4().hex[:6]}"
    _owner, attachment = _create_announcement_attachment(db_session, store=store_name)

    fresh_client = app.test_client()
    resp = fresh_client.get(f"/notes/announcement/api/attachments/{attachment.id}/download")
    assert resp.status_code in (401, 302)


def test_ann_attachment_download_nonexistent_id_returns_404(app, client, db_session):
    """Non-existent attachment ID returns 404 with project-standard error shape."""
    from models import User
    user = User(username=f"ann-u404-{uuid.uuid4().hex[:6]}", role="super_admin")
    user.set_password("pw")
    db_session.add(user)
    db_session.commit()

    _login(client, user)
    resp = client.get("/notes/announcement/api/attachments/99999999/download")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body.get("status") == "error"
    assert body.get("message") == "附件不存在"


# ── Markup smoke tests ───────────────────────────────────────────────────────

import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent  # app_unified/


def test_announcement_attachments_js_has_download_overlay():
    """JS source for announcement attachments includes the download icon
    overlay markup + call into window.downloadAttachment with the
    /notes/announcement/api/attachments/ endpoint prefix."""
    src = (_REPO_ROOT / 'static' / 'js' / 'announcement_attachments.js').read_text(encoding='utf-8')
    assert 'att-download-btn' in src
    assert 'att-thumb-wrap' in src
    assert 'downloadAttachment' in src
    assert '/notes/announcement/api/attachments/' in src


def test_fullscreen_viewer_js_exports_downloadAttachment():
    """fullscreen_viewer.js exposes the shared helper + has the viewer
    download button element."""
    src = (_REPO_ROOT / 'static' / 'js' / 'fullscreen_viewer.js').read_text(encoding='utf-8')
    assert 'window.downloadAttachment' in src
    assert 'fs-download' in src


def test_notes_editor_template_passes_downloadHref():
    """notes editor template renders attachment thumbnails with a download
    icon overlay and the openFullscreen calls now carry downloadHref."""
    src = (_REPO_ROOT / 'templates' / 'notes' / 'editor.html').read_text(encoding='utf-8')
    assert 'downloadHref' in src
    assert '/notes/api/attachments/' in src
    assert 'att-download-btn' in src
