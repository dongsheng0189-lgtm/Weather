"""R2 失敗必須留下 log —— 2026-09-08 補。

發現經過：查 2026-09-05 起的容器 DNS 事故時，用
`grep "name resolution" /tmp/app.log` 統計，結果 252 筆**全部**來自
`vetting.routes`。一度據此推論「只有 vetting 中招」，但那是假象 ——
`storage.py` 的每個失敗路徑都是 `except Exception: return False/None`，
**一個字都不記**，所以 R2 就算同時在壞也不會出現在 log 裡。

後果：DNS／網路出問題的時段，人臉照與附件的刪除、簽名網址是**靜默失敗**的，
使用者和維運都不會知道。本檔守住「失敗要留痕」。

（主上傳 put_object 沒有被 try 包住，例外會往上拋，不在本檔範圍。）
"""
import logging

import pytest

from tests._r2_fixture import install_r2_mock


class FakeClientError(Exception):
    """conftest 把 botocore.exceptions 整個換成 MagicMock，所以 `storage.ClientError`
    在測試裡不是真的例外類別、`side_effect` 設它不會拋。比照
    test_storage_download.py 的慣例換成真的例外子類。"""


BOOM = FakeClientError("R2 boom")


@pytest.fixture
def r2(app, monkeypatch):
    """安裝 R2 mock，並把 storage.ClientError 換成真例外，讓 except 分支可達。"""
    import storage
    monkeypatch.setattr(storage, "ClientError", FakeClientError)
    yield install_r2_mock(monkeypatch, app)


def test_delete_attachment_logs_on_failure(app, r2, caplog):
    from storage import delete_attachment
    r2.delete_object.side_effect = BOOM
    with app.app_context(), caplog.at_level(logging.WARNING):
        assert delete_attachment("attachments/1/x.jpg") is False
    assert any("attachments/1/x.jpg" in r.getMessage() for r in caplog.records), \
        "R2 刪除失敗沒有留下任何 log —— 靜默失敗"


def test_get_signed_url_logs_on_failure(app, r2, caplog):
    from storage import get_signed_url
    r2.generate_presigned_url.side_effect = BOOM
    with app.app_context(), caplog.at_level(logging.WARNING):
        assert get_signed_url("faces/1/x.jpg") is None
    assert any("faces/1/x.jpg" in r.getMessage() for r in caplog.records), \
        "簽名網址產生失敗沒有留下任何 log"


def test_get_download_url_logs_on_failure(app, r2, caplog):
    from storage import get_download_url
    r2.generate_presigned_url.side_effect = BOOM
    with app.app_context(), caplog.at_level(logging.WARNING):
        assert get_download_url("attachments/1/x.jpg", "a.jpg", "image/jpeg") is None
    assert any("attachments/1/x.jpg" in r.getMessage() for r in caplog.records), \
        "下載網址產生失敗沒有留下任何 log"


def test_thumbnail_upload_failure_logs_but_does_not_block_main_upload(app, r2, caplog):
    """縮圖失敗**刻意**不擋主上傳（前端會 fallback 原圖），但不能無聲無息。"""
    from storage import upload_attachment

    def put(**kw):
        if kw.get("Key", "").endswith("_thumb.jpg") or "thumb" in kw.get("Key", ""):
            raise BOOM
        return {}

    r2.put_object.side_effect = put
    # 要用真的圖，假 bytes 產不出縮圖 → 根本走不到縮圖分支（實際踩過）
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4000, 2000), (12, 200, 90)).save(buf, format="JPEG")
    with app.app_context(), caplog.at_level(logging.WARNING):
        object_key, thumb_key = upload_attachment(
            file_bytes=buf.getvalue(), filename="photo.jpg",
            content_type="image/jpeg", note_id=1)
    assert object_key is not None, "主上傳不該被縮圖失敗擋下"
    assert thumb_key is None
    assert any("thumb" in r.getMessage().lower() for r in caplog.records), \
        "縮圖上傳失敗沒有留下任何 log"


# ─────────────────────────────────────────────────────────────
# weather：例外只回給前端、不寫 log
# ─────────────────────────────────────────────────────────────
def test_weather_api_logs_on_failure(app, client, monkeypatch, caplog):
    """`/weather/api/weather` 失敗時只 `return jsonify({"error": str(e)}), 500`，
    伺服器端不留任何痕跡 —— 事後完全查不到「那天天氣為什麼一直掛」。"""
    import weather.routes as wr

    def boom(*a, **kw):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(wr.requests, "get", boom)
    monkeypatch.setitem(app.config, "OPENWEATHERMAP_API_KEY", "fake")
    with caplog.at_level(logging.WARNING):
        res = client.get("/weather/api/weather?city=Taipei")
    assert res.status_code == 500
    assert any("upstream exploded" in r.getMessage() for r in caplog.records), \
        "天氣查詢失敗沒有留下任何 server 端 log"


def test_air_quality_api_logs_on_failure(app, client, monkeypatch, caplog):
    import weather.routes as wr

    def boom(*a, **kw):
        raise RuntimeError("aqi exploded")

    monkeypatch.setattr(wr.requests, "get", boom)
    monkeypatch.setitem(app.config, "OPENWEATHERMAP_API_KEY", "fake")
    with caplog.at_level(logging.WARNING):
        res = client.get("/weather/api/air_quality?lat=25&lon=121")
    assert res.status_code == 500
    assert any("aqi exploded" in r.getMessage() for r in caplog.records), \
        "空氣品質查詢失敗沒有留下任何 server 端 log"
