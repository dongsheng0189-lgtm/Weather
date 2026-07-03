"""上傳圖 → 回傳 (object_key, thumb_key) 且 thumb 被上傳；非圖 → thumb_key None。"""
import io
from unittest.mock import patch
from PIL import Image


def _jpeg(w=4000, h=2000):
    img = Image.new("RGB", (w, h), (12, 200, 90))
    buf = io.BytesIO(); img.save(buf, format="JPEG"); return buf.getvalue()


def test_upload_attachment_returns_object_and_thumb_key(app, monkeypatch):
    from storage import upload_attachment
    puts = {}

    class FakeClient:
        def put_object(self, Bucket, Key, Body, ContentType):
            puts[Key] = (len(Body), ContentType)

    with app.app_context():
        # app 是 session-scoped fixture；直接寫 app.config 會漏到其他測試檔，
        # 改用 monkeypatch.setitem 讓變更在本測試結束後自動還原。
        monkeypatch.setitem(app.config, "R2_ENDPOINT_URL", "https://fake.r2")
        monkeypatch.setitem(app.config, "R2_BUCKET_NAME", "b")
        with patch("storage._get_client", return_value=FakeClient()):
            obj, thumb = upload_attachment(_jpeg(), 7, "photo.jpg", "image/jpeg")
    assert obj and obj.endswith(".jpg")
    assert thumb == obj.rsplit(".", 1)[0] + "_thumb.jpg"
    assert obj in puts and thumb in puts           # 兩顆都上傳了
    assert puts[thumb][1] == "image/jpeg"          # 縮圖 content-type


def test_upload_video_no_thumb(app, monkeypatch):
    from storage import upload_attachment

    class FakeClient:
        def put_object(self, **kw):
            pass

    with app.app_context():
        monkeypatch.setitem(app.config, "R2_ENDPOINT_URL", "https://fake.r2")
        monkeypatch.setitem(app.config, "R2_BUCKET_NAME", "b")
        with patch("storage._get_client", return_value=FakeClient()):
            obj, thumb = upload_attachment(b"fake-mp4", 7, "v.mp4", "video/mp4")
    assert obj and thumb is None
