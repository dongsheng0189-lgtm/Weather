"""image_utils: Pillow 縮圖/壓縮純函式測試。"""
import io
from PIL import Image


def _png_bytes(w, h, color=(200, 30, 30)):
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(w, h):
    img = Image.new("RGB", (w, h), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _dims(b):
    return Image.open(io.BytesIO(b)).size


def test_large_image_main_downscaled_to_3200():
    from image_utils import process_upload_image, MAX_LONG_EDGE
    raw = _jpeg_bytes(6000, 3000)
    main, thumb = process_upload_image(raw, "image/jpeg")
    w, h = _dims(main)
    assert max(w, h) == MAX_LONG_EDGE   # 6000 → 3200
    assert (w, h) == (3200, 1600)


def test_small_image_main_not_upscaled():
    from image_utils import process_upload_image
    raw = _jpeg_bytes(800, 600)
    main, thumb = process_upload_image(raw, "image/jpeg")
    assert _dims(main) == (800, 600)    # 不放大


def test_thumb_long_edge_640_and_is_jpeg():
    from image_utils import process_upload_image, THUMB_LONG_EDGE
    raw = _jpeg_bytes(4000, 2000)
    main, thumb = process_upload_image(raw, "image/jpeg")
    assert thumb is not None
    tw, th = _dims(thumb)
    assert max(tw, th) == THUMB_LONG_EDGE   # 4000 → 640
    assert Image.open(io.BytesIO(thumb)).format == "JPEG"


def test_png_with_alpha_thumb_flattened_to_jpeg():
    from image_utils import process_upload_image
    img = Image.new("RGBA", (1000, 800), (0, 0, 0, 0))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    main, thumb = process_upload_image(buf.getvalue(), "image/png")
    assert Image.open(io.BytesIO(thumb)).mode == "RGB"   # alpha 被 flatten


def test_gif_not_processed():
    from image_utils import process_upload_image
    img = Image.new("P", (500, 500))
    buf = io.BytesIO(); img.save(buf, format="GIF")
    raw = buf.getvalue()
    main, thumb = process_upload_image(raw, "image/gif")
    assert main == raw and thumb is None


def test_video_not_processed():
    from image_utils import process_upload_image
    raw = b"\x00\x00\x00\x18ftypmp42fake-video-bytes"
    main, thumb = process_upload_image(raw, "video/mp4")
    assert main == raw and thumb is None


def test_corrupt_bytes_returns_raw_no_raise():
    from image_utils import process_upload_image
    raw = b"not-an-image-at-all"
    main, thumb = process_upload_image(raw, "image/jpeg")
    assert main == raw and thumb is None   # 不 raise、fallback


def test_thumb_key_for():
    from image_utils import thumb_key_for
    assert thumb_key_for("attachments/12/abcd.jpg") == "attachments/12/abcd_thumb.jpg"
    assert thumb_key_for("private/3/xy.png") == "private/3/xy_thumb.jpg"
