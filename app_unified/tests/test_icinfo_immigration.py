"""Test icinfo/immigration.py — HTTP wrapper for 移民署 API."""
from unittest.mock import patch, MagicMock
import pytest

from icinfo.immigration import fetch_captcha, submit_search
from icinfo.errors import UpstreamError


def test_fetch_captcha_returns_jpeg_and_jsessionid():
    """GET /api/captcha 回 JPEG + Set-Cookie，wrapper 應抽出 (bytes, jsessionid)."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b"\xff\xd8\xff\xe0FAKE_JPEG"
    fake_resp.cookies = {"JSESSIONID": "ABC123"}
    fake_resp.raise_for_status = MagicMock()

    with patch("icinfo.immigration.requests.get", return_value=fake_resp) as mock_get:
        jpeg, jsessionid = fetch_captcha()

    assert jpeg.startswith(b"\xff\xd8")
    assert jsessionid == "ABC123"
    args, kwargs = mock_get.call_args
    assert "niaicinfo.immigration.gov.tw" in args[0]
    assert "/api/captcha" in args[0]


def test_fetch_captcha_no_cookie_raises():
    """上游沒回 JSESSIONID 視為 UpstreamError。"""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.content = b"\xff\xd8FAKE"
    fake_resp.cookies = {}
    fake_resp.raise_for_status = MagicMock()

    with patch("icinfo.immigration.requests.get", return_value=fake_resp):
        with pytest.raises(UpstreamError):
            fetch_captcha()


def test_submit_search_success():
    """rcode='SUCCESS' 應回 (matched=True, image=base64, search_time)."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "rcode": "SUCCESS",
        "data": {"searchTime": "2026-05-04 12:00:00", "image": "BASE64IMG"},
    }
    fake_resp.raise_for_status = MagicMock()

    payload = {
        "uino": "AB12345678",
        "issueDate": "2024-01-15",
        "expiryDate": "2026-01-15",
        "serialNo": "1234567890",
        "captcha": "abcd",
    }
    with patch("icinfo.immigration.requests.post", return_value=fake_resp) as mock_post:
        result = submit_search(payload, jsessionid="ABC123")

    assert result["matched"] is True
    assert result["image"] == "BASE64IMG"
    assert result["search_time"] == "2026-05-04 12:00:00"
    _, kwargs = mock_post.call_args
    assert kwargs["cookies"]["JSESSIONID"] == "ABC123"


def test_submit_search_no_match():
    """rcode != SUCCESS 應回 matched=False, image=None."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"rcode": "ERR", "data": {}}
    fake_resp.raise_for_status = MagicMock()

    with patch("icinfo.immigration.requests.post", return_value=fake_resp):
        result = submit_search(
            {"uino": "X", "issueDate": "", "expiryDate": "", "serialNo": "", "captcha": ""},
            jsessionid="ABC",
        )

    assert result["matched"] is False
    assert result["image"] is None


def test_submit_search_upstream_5xx_raises():
    """上游 5xx 應 raise UpstreamError."""
    import requests as req_lib

    fake_resp = MagicMock()
    fake_resp.status_code = 500
    fake_resp.raise_for_status = MagicMock(
        side_effect=req_lib.HTTPError("500 server error")
    )

    with patch("icinfo.immigration.requests.post", return_value=fake_resp):
        with pytest.raises(UpstreamError):
            submit_search(
                {"uino": "X", "issueDate": "", "expiryDate": "", "serialNo": "", "captcha": ""},
                jsessionid="ABC",
            )


def test_submit_search_invalid_json_raises():
    """上游回非 JSON 應 raise UpstreamError."""
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.side_effect = ValueError("not json")
    fake_resp.raise_for_status = MagicMock()

    with patch("icinfo.immigration.requests.post", return_value=fake_resp):
        with pytest.raises(UpstreamError):
            submit_search(
                {"uino": "X", "issueDate": "", "expiryDate": "", "serialNo": "", "captcha": ""},
                jsessionid="ABC",
            )
