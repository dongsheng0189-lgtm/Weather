"""附件影像處理:壓縮原圖 + 產縮圖。純函式,不碰 R2/DB,可獨立測試。"""
import io
import logging
from PIL import Image, ImageOps

_LOG = logging.getLogger(__name__)

MAX_LONG_EDGE = 3200      # 原圖長邊上限
THUMB_LONG_EDGE = 640     # 縮圖長邊
MAIN_QUALITY = 85
THUMB_QUALITY = 78

# 只處理靜態圖;gif(可能動圖)與 video 一律跳過
PROCESSABLE_TYPES = {"image/jpeg", "image/png", "image/webp"}

_PIL_FORMAT = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def thumb_key_for(object_key: str) -> str:
    """由原圖 R2 key 推導縮圖 key:<base>_thumb.jpg(縮圖一律 JPEG)。"""
    base = object_key.rsplit(".", 1)[0] if "." in object_key else object_key
    return f"{base}_thumb.jpg"


def _to_rgb(img):
    """JPEG 不支援 alpha/P;flatten 到白底 RGB。"""
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        conv = img.convert("RGBA")
        bg.paste(conv, mask=conv.split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _encode_main(img, content_type: str) -> bytes:
    fmt = _PIL_FORMAT[content_type]
    buf = io.BytesIO()
    if fmt == "JPEG":
        _to_rgb(img).save(buf, format="JPEG", quality=MAIN_QUALITY, optimize=True)
    elif fmt == "PNG":
        img.save(buf, format="PNG", optimize=True)
    else:  # WEBP
        img.save(buf, format="WEBP", quality=MAIN_QUALITY)
    return buf.getvalue()


def _encode_thumb(img) -> bytes:
    buf = io.BytesIO()
    _to_rgb(img).save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    return buf.getvalue()


def process_upload_image(raw_bytes: bytes, content_type: str):
    """回 (main_bytes, thumb_bytes)。
    - content_type 非可處理型別 → (raw_bytes, None)
    - Pillow 任何錯誤 → (raw_bytes, None)(記 log,不中斷上傳)
    """
    if content_type not in PROCESSABLE_TYPES:
        return raw_bytes, None
    try:
        src = ImageOps.exif_transpose(Image.open(io.BytesIO(raw_bytes)))

        main_img = src.copy()
        if max(main_img.size) > MAX_LONG_EDGE:
            main_img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.LANCZOS)
        main_bytes = _encode_main(main_img, content_type)

        thumb_img = src.copy()
        thumb_img.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), Image.LANCZOS)
        thumb_bytes = _encode_thumb(thumb_img)

        return main_bytes, thumb_bytes
    except Exception as e:
        _LOG.warning("process_upload_image failed (%s): %s", content_type, e)
        return raw_bytes, None
