"""Tests for storage.get_download_url — presigned URL with attachment disposition."""
import urllib.parse

import pytest

from tests._r2_fixture import install_r2_mock


@pytest.fixture(autouse=True)
def r2_configured(app, monkeypatch):
    """Ensure app.config has R2 settings and _get_client returns a realistic mock.

    Yields the mock_client so individual tests can override side_effect (e.g.
    for ClientError simulation).
    """
    yield install_r2_mock(monkeypatch, app)


def test_get_download_url_returns_presigned_with_attachment_disposition(app):
    from storage import get_download_url
    with app.app_context():
        url = get_download_url(
            object_key="attachments/1/abc.jpg",
            filename="現場照片.jpg",
            content_type="image/jpeg",
        )
        assert url is not None
        # presigned URL contains the content-disposition override
        assert "response-content-disposition=" in url.lower()
        # Disposition is attachment + RFC 5987 filename* with utf-8 encoding
        decoded = urllib.parse.unquote_plus(url)
        assert "attachment" in decoded
        assert "filename*=UTF-8''" in decoded
        # Chinese filename percent-encoded
        encoded_filename = urllib.parse.quote("現場照片.jpg", safe="")
        assert encoded_filename in url
        # ResponseContentType is forwarded correctly
        assert "response-content-type=image%2fjpeg" in url.lower()


def test_get_download_url_default_expires_in_300(app):
    from storage import get_download_url
    with app.app_context():
        url = get_download_url(
            object_key="attachments/1/abc.jpg",
            filename="x.jpg",
            content_type="image/jpeg",
        )
        # presigned URL contains X-Amz-Expires=300
        assert "x-amz-expires=300" in url.lower()


def test_get_download_url_custom_expires_in(app):
    from storage import get_download_url
    with app.app_context():
        url = get_download_url(
            object_key="attachments/1/abc.jpg",
            filename="x.jpg",
            content_type="image/jpeg",
            expires_in=600,
        )
        assert "x-amz-expires=600" in url.lower()


def test_get_download_url_returns_none_when_r2_not_configured(monkeypatch, app):
    from storage import get_download_url
    with app.app_context():
        monkeypatch.setitem(app.config, "R2_ENDPOINT_URL", "")
        result = get_download_url(
            object_key="attachments/1/abc.jpg",
            filename="x.jpg",
            content_type="image/jpeg",
        )
        assert result is None


def test_get_download_url_returns_none_for_empty_object_key(app):
    from storage import get_download_url
    with app.app_context():
        assert get_download_url(
            object_key="",
            filename="x.jpg",
            content_type="image/jpeg",
        ) is None


def test_get_download_url_returns_none_on_client_error(monkeypatch, app, r2_configured):
    """If boto3 raises ClientError (e.g. R2 outage / bad creds), helper
    must swallow and return None — caller will surface storage_unavailable
    to the user."""
    import storage

    # conftest mocks botocore.exceptions as a plain MagicMock, so
    # storage.ClientError is a MagicMock instance rather than a real exception
    # class.  Replace it with a real exception subclass for the duration of
    # this test so the except-branch in get_download_url is actually reachable.
    class FakeClientError(Exception):
        pass

    monkeypatch.setattr(storage, "ClientError", FakeClientError)

    with app.app_context():
        r2_configured.generate_presigned_url.side_effect = FakeClientError
        result = storage.get_download_url(
            object_key="attachments/1/abc.jpg",
            filename="x.jpg",
            content_type="image/jpeg",
        )
        assert result is None
