"""crypto.py 本地 roundtrip 測試。
執行：cd app_unified && python -m tests.test_crypto
"""
import os
import sys

# 測試前設好 key
os.environ["DB_ENCRYPTION_KEY"] = ""  # 先清空
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crypto import generate_key, encrypt, decrypt, is_encrypted, _PREFIX  # noqa: E402
import crypto as crypto_mod  # noqa: E402


_PASS = 0
_FAIL = 0


def check(cond, label):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {label}")
    else:
        _FAIL += 1
        print(f"  ✗ {label}")


def reset_key(key_str=None):
    """重設 key（測試 key rotation / wrong key 用）"""
    crypto_mod._KEY_CACHE = None
    os.environ["DB_ENCRYPTION_KEY"] = key_str or generate_key()


def main():
    print("=" * 60)
    print("crypto.py 本地測試")
    print("=" * 60)

    # 0) 未設 key 時應 raise
    print("\n[0] 未設 DB_ENCRYPTION_KEY 應 raise")
    os.environ["DB_ENCRYPTION_KEY"] = ""
    crypto_mod._KEY_CACHE = None
    try:
        encrypt("abc")
        check(False, "應該要 raise")
    except RuntimeError as e:
        check("DB_ENCRYPTION_KEY" in str(e), f"正確 raise：{e}")

    # 1) key 格式錯誤
    print("\n[1] key 格式錯誤")
    os.environ["DB_ENCRYPTION_KEY"] = "not-base64-!!!"
    crypto_mod._KEY_CACHE = None
    try:
        encrypt("abc")
        check(False, "應該要 raise")
    except RuntimeError as e:
        check(True, f"正確 raise：{type(e).__name__}")

    # 2) key 長度錯誤（16 bytes 而不是 32）
    print("\n[2] key 長度不對")
    import base64
    short_key = base64.urlsafe_b64encode(os.urandom(16)).decode()
    os.environ["DB_ENCRYPTION_KEY"] = short_key
    crypto_mod._KEY_CACHE = None
    try:
        encrypt("abc")
        check(False, "應該要 raise")
    except RuntimeError as e:
        check("長度" in str(e), f"正確 raise：{e}")

    # 3) 正常 roundtrip
    print("\n[3] 基本 roundtrip（繁中）")
    reset_key()
    cases = [
        "王小明今天請假",
        "今天王董來談價格，需要準備報價單",
        "Hello World 中英混合 123 !@#",
        "a" * 10000,
        "emoji 測試 🎉✨👍",
        "多行\n第二行\n第三行",
    ]
    for pt in cases:
        ct = encrypt(pt)
        check(ct != pt, f"密文 ≠ 明文 (len={len(pt)})")
        check(ct.startswith(_PREFIX), f"有 {_PREFIX} 前綴")
        check(decrypt(ct) == pt, f"解密還原 (len={len(pt)})")

    # 4) None / 空字串
    print("\n[4] None / 空字串")
    check(encrypt(None) is None, "encrypt(None) == None")
    check(encrypt("") == "", "encrypt('') == ''")
    check(decrypt(None) is None, "decrypt(None) == None")
    check(decrypt("") == "", "decrypt('') == ''")

    # 5) 舊明文相容（沒有 enc: 前綴）
    print("\n[5] 舊明文相容模式")
    check(decrypt("舊資料明文") == "舊資料明文", "未加密字串原樣返回")
    check(decrypt("王小明") == "王小明", "中文明文原樣返回")

    # 6) 避免重複加密
    print("\n[6] 避免重複加密")
    ct1 = encrypt("王董")
    ct2 = encrypt(ct1)   # 餵加密後的值
    check(ct1 == ct2, "對已加密字串再 encrypt 不會加密兩次")
    check(decrypt(ct2) == "王董", "一次解密就還原")

    # 7) 每次 IV 隨機（同明文 → 不同密文）
    print("\n[7] IV 隨機性")
    a = encrypt("王董")
    b = encrypt("王董")
    check(a != b, "同明文兩次加密產生不同密文（IV 隨機）")
    check(decrypt(a) == decrypt(b) == "王董", "兩個密文解密都還原相同明文")

    # 8) 竄改偵測（GCM tag）
    print("\n[8] 竄改偵測")
    ct = encrypt("機密內容")
    # 改最後一個字元
    tampered = ct[:-1] + ("A" if ct[-1] != "A" else "B")
    result = decrypt(tampered)
    check(result.startswith("⚠️"), f"竄改後解密失敗，顯示警告：{result[:30]}")

    # 9) 錯誤 key 解密
    print("\n[9] 用錯誤 key 解密")
    reset_key()
    ct = encrypt("王小明的祕密")
    # 換一把 key
    reset_key()
    result = decrypt(ct)
    check(result.startswith("⚠️"), f"錯 key 解密失敗，顯示警告：{result[:30]}")

    # 10) is_encrypted
    print("\n[10] is_encrypted 判別")
    reset_key()
    check(is_encrypted(encrypt("abc")) is True, "加密字串 is_encrypted=True")
    check(is_encrypted("abc") is False, "明文 is_encrypted=False")
    check(is_encrypted(None) is False, "None is_encrypted=False")
    check(is_encrypted("") is False, "'' is_encrypted=False")

    # 11) EncryptedText TypeDecorator
    print("\n[11] EncryptedText TypeDecorator")
    from crypto import EncryptedText
    t = EncryptedText()
    reset_key()
    bound = t.process_bind_param("王董", None)   # Python → DB
    check(bound.startswith(_PREFIX), f"bind 後是密文")
    back = t.process_result_value(bound, None)   # DB → Python
    check(back == "王董", f"result 後還原明文")
    # None 處理
    check(t.process_bind_param(None, None) is None, "bind None → None")
    check(t.process_result_value(None, None) is None, "result None → None")
    # 舊明文
    check(t.process_result_value("舊明文", None) == "舊明文", "result 未加密明文原樣返回")

    # 12) 大型資料
    print("\n[12] 大型資料 (1MB)")
    big = "長會議紀錄 " * 100000
    ct = encrypt(big)
    check(decrypt(ct) == big, f"1MB roundtrip OK")

    # Summary
    print("\n" + "=" * 60)
    print(f"結果：通過 {_PASS}、失敗 {_FAIL}")
    print("=" * 60)
    sys.exit(0 if _FAIL == 0 else 1)


if __name__ == "__main__":
    main()
