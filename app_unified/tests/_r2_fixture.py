"""Shared R2 mock fixture for attachment storage tests.

Used by test_storage_download.py + test_attachment_download.py + Task 3's
test file for announcement attachment download. Builds a realistic presigned
URL from the actual Params dict so encoding behavior is genuinely exercised.
"""
import urllib.parse
from unittest.mock import MagicMock


def _make_presigned_url(params, expires_in):
    """Build a realistic presigned URL from generate_presigned_url Params.

    safe="%" preserves percent-encoded substrings in disposition / content-type
    so Chinese filenames are encoded once, not double-encoded.
    """
    bucket = params["Bucket"]
    key = params["Key"]
    disposition = urllib.parse.quote(
        params.get("ResponseContentDisposition", ""), safe="%"
    )
    content_type = urllib.parse.quote(
        params.get("ResponseContentType", ""), safe="%"
    )
    return (
        f"https://fake-r2.example.com/{bucket}/{key}"
        f"?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        f"&X-Amz-Expires={expires_in}"
        f"&response-content-disposition={disposition}"
        f"&response-content-type={content_type}"
        f"&X-Amz-Signature=fakesig"
    )


def install_r2_mock(monkeypatch, app):
    """Install fake R2 config + mock boto3 client whose generate_presigned_url
    builds a realistic URL from Params. Call from test or autouse fixture.

    Returns the mock_client so callers can override side_effect for error
    testing (e.g. test_get_download_url_returns_none_on_client_error).
    """
    import storage

    monkeypatch.setitem(app.config, "R2_ENDPOINT_URL", "https://fake-r2.example.com")
    monkeypatch.setitem(app.config, "R2_ACCESS_KEY_ID", "fake-key-id")
    monkeypatch.setitem(app.config, "R2_SECRET_ACCESS_KEY", "fake-secret")
    monkeypatch.setitem(app.config, "R2_BUCKET_NAME", "test-bucket")

    mock_client = MagicMock()
    mock_client.generate_presigned_url.side_effect = (
        lambda op, Params, ExpiresIn: _make_presigned_url(Params, ExpiresIn)
    )
    monkeypatch.setattr(storage, "_get_client", lambda: mock_client)
    return mock_client
