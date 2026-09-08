"""Cloudflare R2 storage helper (S3-compatible via boto3)."""
import os
import io
import uuid
import urllib.parse
from flask import current_app

try:
    import boto3
    from botocore.exceptions import ClientError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


def _get_client():
    cfg = current_app.config
    return boto3.client(
        "s3",
        endpoint_url=cfg["R2_ENDPOINT_URL"],
        aws_access_key_id=cfg["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=cfg["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def upload_face_photo(image_bytes: bytes, user_id: int) -> str | None:
    """
    Upload face photo JPEG bytes to Cloudflare R2 private bucket.
    Returns the object key (not a public URL — use signed URL for access).
    Returns None if R2 is not configured or boto3 unavailable.
    """
    if not BOTO3_AVAILABLE:
        return None

    cfg = current_app.config
    if not cfg.get("R2_ENDPOINT_URL"):
        return None

    bucket = cfg["R2_BUCKET_NAME"]
    filename = f"faces/{user_id}/{uuid.uuid4().hex}.jpg"

    client = _get_client()
    client.put_object(
        Bucket=bucket,
        Key=filename,
        Body=image_bytes,
        ContentType="image/jpeg",
    )
    return filename


ALLOWED_CONTENT_TYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'video/mp4', 'video/quicktime', 'video/webm',
}
# 私人筆記額外接受 PDF（店務筆記不開放）
ALLOWED_CONTENT_TYPES_MYNOTES = ALLOWED_CONTENT_TYPES | {'application/pdf'}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def _offload(fn, *args):
    """CPU-heavy 工作丟 gevent threadpool 跑，避免卡 hub。無 gevent 則直接跑（測試/dev）。"""
    try:
        import gevent
        hub = gevent.get_hub()
    except Exception:
        return fn(*args)
    return hub.threadpool.apply(fn, args)


def upload_attachment(file_bytes: bytes, note_id: int, filename: str, content_type: str, prefix: str = "attachments"):
    """Upload attachment to R2. 回 (object_key, thumb_key)。

    - 靜態圖（jpeg/png/webp）：壓縮原圖（長邊≤3200）+ 產縮圖（640，JPEG），兩顆都上傳。
    - gif/影片/其他：原檔上傳，thumb_key=None。
    - R2 未設定 / boto3 不可用：回 (None, None)。

    prefix 可傳不同前綴以隔離不同模組（例：私人筆記傳 "private/{user_id}"）。
    """
    from image_utils import process_upload_image, thumb_key_for

    if not BOTO3_AVAILABLE:
        return None, None
    cfg = current_app.config
    if not cfg.get("R2_ENDPOINT_URL"):
        return None, None

    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    object_key = f"{prefix}/{note_id}/{uuid.uuid4().hex}.{ext}"

    main_bytes, thumb_bytes = _offload(process_upload_image, file_bytes, content_type)

    client = _get_client()
    client.put_object(
        Bucket=cfg["R2_BUCKET_NAME"],
        Key=object_key,
        Body=main_bytes,
        ContentType=content_type,
    )

    thumb_key = None
    if thumb_bytes is not None:
        thumb_key = thumb_key_for(object_key)
        try:
            client.put_object(
                Bucket=cfg["R2_BUCKET_NAME"],
                Key=thumb_key,
                Body=thumb_bytes,
                ContentType="image/jpeg",
            )
        except Exception:
            # 縮圖失敗刻意不擋主上傳（前端 fallback 原圖），但不可無聲無息 ——
            # 見 tests/test_storage_failure_logging.py 的檔頭說明。
            current_app.logger.warning(
                "R2 thumb upload failed (fallback to original): key=%s",
                thumb_key, exc_info=True)
            thumb_key = None

    return object_key, thumb_key


def delete_attachment(object_key: str) -> bool:
    """Delete an object from R2. Returns True on success."""
    if not BOTO3_AVAILABLE or not object_key:
        return False
    cfg = current_app.config
    if not cfg.get("R2_ENDPOINT_URL"):
        return False

    try:
        client = _get_client()
        client.delete_object(Bucket=cfg["R2_BUCKET_NAME"], Key=object_key)
        return True
    except Exception:
        current_app.logger.warning(
            "R2 delete failed: key=%s", object_key, exc_info=True)
        return False


def get_signed_url(object_key: str, expires_in: int = 3600) -> str | None:
    """
    Generate a pre-signed URL for a private R2 object (valid for expires_in seconds).
    """
    if not BOTO3_AVAILABLE or not object_key:
        return None

    cfg = current_app.config
    if not cfg.get("R2_ENDPOINT_URL"):
        return None

    client = _get_client()
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": cfg["R2_BUCKET_NAME"], "Key": object_key},
            ExpiresIn=expires_in,
        )
        return url
    except ClientError:
        current_app.logger.warning(
            "R2 presigned URL failed: key=%s", object_key, exc_info=True)
        return None


def get_download_url(object_key: str, filename: str, content_type: str,
                     expires_in: int = 300) -> str | None:
    """Generate a presigned URL that forces browser to download the file with
    Content-Disposition: attachment + the user-supplied filename (RFC 5987
    UTF-8 encoded). Used by the new attachment download endpoints.

    Returns None when R2 is not configured, boto3 is unavailable, or
    object_key is empty.
    """
    if not BOTO3_AVAILABLE or not object_key:
        return None

    cfg = current_app.config
    if not cfg.get("R2_ENDPOINT_URL"):
        return None

    if filename:
        safe_filename = filename
    else:
        # Spec: filename 缺失時 fallback `attachment{ext}`，ext 從 content_type 推導
        # (image/jpeg → .jpg, video/mp4 → .mp4, 其他或解不出 → 不加 ext)
        ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "video/mp4": ".mp4",
            "video/quicktime": ".mov",
            "video/webm": ".webm",
            "application/pdf": ".pdf",
        }
        ext = ext_map.get(content_type or "", "")
        safe_filename = f"attachment{ext}"
    encoded = urllib.parse.quote(safe_filename, safe="")
    disposition = f"attachment; filename*=UTF-8''{encoded}"
    resp_content_type = content_type or "application/octet-stream"

    client = _get_client()
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": cfg["R2_BUCKET_NAME"],
                "Key": object_key,
                "ResponseContentDisposition": disposition,
                "ResponseContentType": resp_content_type,
            },
            ExpiresIn=expires_in,
        )
        return url
    except ClientError:
        current_app.logger.warning(
            "R2 presigned URL failed: key=%s", object_key, exc_info=True)
        return None
