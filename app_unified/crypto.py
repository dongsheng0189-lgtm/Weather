"""
欄位級加密：AES-256-GCM
格式：enc:v1:<base64(iv(12) || ciphertext || tag(16))>
相容模式：解密時若沒有 enc: 前綴 → 視為舊明文原樣返回（遷移期用）
"""
import base64
import os
import logging

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.types import TypeDecorator, Text

_LOG = logging.getLogger(__name__)

_PREFIX = "enc:v1:"
_KEY_CACHE = None


def _load_key() -> bytes:
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE
    raw = os.environ.get("DB_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise RuntimeError(
            "DB_ENCRYPTION_KEY 未設定。請用 `python -c \"from crypto import generate_key; print(generate_key())\"` 產生並設到 env。"
        )
    try:
        key = base64.urlsafe_b64decode(raw)
    except Exception as e:
        raise RuntimeError(f"DB_ENCRYPTION_KEY 格式錯誤（應為 urlsafe base64）: {e}")
    if len(key) != 32:
        raise RuntimeError(f"DB_ENCRYPTION_KEY 長度錯誤（需 32 bytes，實際 {len(key)}）")
    _KEY_CACHE = key
    return key


def generate_key() -> str:
    """產生新的 AES-256 key（urlsafe base64 編碼）。"""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def encrypt(plaintext):
    """字串 → 加密字串；None / 空字串維持不變（方便 DB NULL/'' 語意）。"""
    if plaintext is None:
        return None
    if plaintext == "":
        return ""
    if isinstance(plaintext, str) and plaintext.startswith(_PREFIX):
        # 已經是加密格式，避免重複加密
        return plaintext
    key = _load_key()
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    blob = iv + ct  # ct 已包含 tag
    return _PREFIX + base64.urlsafe_b64encode(blob).decode()


def decrypt(ciphertext):
    """加密字串 → 明文；None / 空字串維持不變；非加密格式視為舊明文原樣返回。"""
    if ciphertext is None:
        return None
    if ciphertext == "":
        return ""
    if not isinstance(ciphertext, str):
        return ciphertext
    if not ciphertext.startswith(_PREFIX):
        # 遷移期相容：未加密的舊資料直接返回
        return ciphertext
    try:
        blob = base64.urlsafe_b64decode(ciphertext[len(_PREFIX):])
        iv, ct = blob[:12], blob[12:]
        key = _load_key()
        aesgcm = AESGCM(key)
        pt = aesgcm.decrypt(iv, ct, None)
        return pt.decode("utf-8")
    except Exception as e:
        # 絕不印密文 / key / plaintext；只記 error 類型
        _LOG.error("field decrypt failed: %s", type(e).__name__)
        return "⚠️ 無法解密（請聯絡管理員）"


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


class EncryptedText(TypeDecorator):
    """對上層透明：DB 存密文，Python 端拿到明文。"""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt(value)

    def process_result_value(self, value, dialect):
        return decrypt(value)
