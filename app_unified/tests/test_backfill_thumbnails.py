import io
from PIL import Image


def _jpeg(w=3000, h=1500):
    img = Image.new("RGB", (w, h), (5, 5, 5))
    buf = io.BytesIO(); img.save(buf, format="JPEG"); return buf.getvalue()


def test_backfill_one_image_produces_thumb():
    from scripts.backfill_thumbnails import backfill_one
    puts = {}
    tk = backfill_one("image/jpeg", "attachments/1/a.jpg",
                      get_bytes=lambda k: _jpeg(),
                      put_bytes=lambda k, b: puts.__setitem__(k, b))
    assert tk == "attachments/1/a_thumb.jpg"
    assert tk in puts
    assert Image.open(io.BytesIO(puts[tk])).format == "JPEG"


def test_backfill_one_video_skipped():
    from scripts.backfill_thumbnails import backfill_one
    puts = {}
    tk = backfill_one("video/mp4", "attachments/1/v.mp4",
                      get_bytes=lambda k: b"x", put_bytes=lambda k, b: puts.__setitem__(k, b))
    assert tk is None and not puts
