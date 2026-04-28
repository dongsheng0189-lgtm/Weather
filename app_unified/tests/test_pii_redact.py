"""Tests for pii_redact module."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from pii_redact import redact, restore


# ── 身分證 ──────────────────────────────────────────────────────────────
def test_taiwan_id_single():
    text = "客人 A123456789 投訴"
    out, m = redact(text)
    assert out == "客人 [id1] 投訴"
    assert m == {"[id1]": "A123456789"}


def test_taiwan_id_adjacent_chinese():
    """中文字緊鄰身分證號碼（無空白）— Python \\b 在 Unicode 下不行，
    必須用 lookaround 排除 ASCII 英數才能正確匹配。"""
    text = "友人陪同P124464394許先生來"
    out, m = redact(text)
    assert "[id1]" in out
    assert m["[id1]"] == "P124464394"
    assert "P124464394" not in out


def test_mobile_adjacent_chinese():
    """手機號碼前後緊鄰中文"""
    text = "電話0912345678請聯絡"
    out, m = redact(text)
    assert "[tel1]" in out
    assert m["[tel1]"] == "0912345678"


def test_email_adjacent_chinese():
    text = "請寄foo@bar.com確認"
    out, m = redact(text)
    assert "[email1]" in out
    assert m["[email1]"] == "foo@bar.com"


def test_taiwan_id_multiple_distinct():
    text = "A123456789 來辦會員，B234567890 已離開"
    out, m = redact(text)
    assert out == "[id1] 來辦會員，[id2] 已離開"
    assert m["[id1]"] == "A123456789"
    assert m["[id2]"] == "B234567890"


def test_taiwan_id_repeat_same_placeholder():
    text = "A123456789 來，A123456789 又來"
    out, m = redact(text)
    assert out == "[id1] 來，[id1] 又來"
    assert len(m) == 1


def test_taiwan_id_invalid_second_digit_skipped():
    # 第二碼非 1/2 不認（嚴格版）
    text = "A923456789 不是身分證"
    out, m = redact(text)
    assert out == text
    assert m == {}


# ── 手機 ─────────────────────────────────────────────────────────────────
def test_mobile_plain():
    text = "請打 0912345678 給客人"
    out, m = redact(text)
    assert out == "請打 [tel1] 給客人"
    assert m["[tel1]"] == "0912345678"


def test_mobile_with_dash():
    text = "電話 0912-345-678"
    out, m = redact(text)
    assert "[tel1]" in out
    assert m["[tel1]"] == "0912-345-678"


# ── Email ────────────────────────────────────────────────────────────────
def test_email_basic():
    text = "寄信給 john.doe@example.com 確認"
    out, m = redact(text)
    assert out == "寄信給 [email1] 確認"
    assert m["[email1]"] == "john.doe@example.com"


def test_email_with_plus():
    text = "聯絡 user+tag@gmail.com"
    out, m = redact(text)
    assert "[email1]" in out
    assert m["[email1]"] == "user+tag@gmail.com"


# ── 信用卡 ───────────────────────────────────────────────────────────────
def test_credit_card_with_dash():
    text = "卡號 1234-5678-9012-3456 已扣"
    out, m = redact(text)
    assert "[card1]" in out
    assert m["[card1]"] == "1234-5678-9012-3456"


def test_credit_card_no_dash():
    text = "1234567890123456 扣款"
    out, m = redact(text)
    assert "[card1]" in out


# ── 市話 ─────────────────────────────────────────────────────────────────
def test_landline_with_dash():
    text = "公司 02-12345678 找小王"
    out, m = redact(text)
    assert "[ph1]" in out
    assert m["[ph1]"] == "02-12345678"


def test_landline_does_not_match_mobile():
    # 09 開頭不能被市話誤吃
    text = "0912345678 是手機"
    out, m = redact(text)
    assert "[tel1]" in out
    assert "[ph" not in out


# ── 混合 ─────────────────────────────────────────────────────────────────
def test_mixed_pii_one_prompt():
    text = (
        "陳小姐 (A123456789) 投訴\n"
        "另一位 B234567890 換電話 0912345678\n"
        "打 0987654321 給王先生 (C145678901)\n"
        "Email: foo@bar.com，卡號 4111-1111-1111-1111"
    )
    out, m = redact(text)
    # 5 個不同 PII 類別都有編號
    assert "[id1]" in out and "[id2]" in out and "[id3]" in out
    assert "[tel1]" in out and "[tel2]" in out
    assert "[email1]" in out
    assert "[card1]" in out
    # 真值有對應
    assert m["[id1]"] == "A123456789"
    assert m["[card1]"] == "4111-1111-1111-1111"


def test_no_pii_passthrough():
    text = "今天廚房漏水，已通知工程"
    out, m = redact(text)
    assert out == text
    assert m == {}


# ── restore ─────────────────────────────────────────────────────────────
def test_restore_strict():
    mapping = {"[id1]": "A123456789", "[tel1]": "0912345678"}
    summary = "- [id1] 投訴待處理\n- 撥 [tel1] 給客人"
    out = restore(summary, mapping)
    assert out == "- A123456789 投訴待處理\n- 撥 0912345678 給客人"


def test_restore_loose_uppercase():
    """LLM 萬一改大寫 [ID1] 仍能還原"""
    mapping = {"[id1]": "A123456789"}
    summary = "客人 [ID1] 已聯繫"
    out = restore(summary, mapping)
    assert "A123456789" in out


def test_restore_loose_with_spaces():
    """LLM 萬一加空白 [ id1 ] 仍能還原"""
    mapping = {"[id1]": "A123456789"}
    summary = "客人 [ id1 ] 已聯繫"
    out = restore(summary, mapping)
    assert "A123456789" in out


def test_restore_unknown_placeholder_kept():
    """LLM 自編沒在 mapping 的 placeholder → 原樣保留"""
    mapping = {"[id1]": "A123456789"}
    summary = "客人 [id1] 來，[id99] 也來"
    out = restore(summary, mapping)
    assert "A123456789" in out
    assert "[id99]" in out


def test_restore_empty_mapping():
    summary = "沒有 PII 的摘要"
    out = restore(summary, {})
    assert out == summary


# ── roundtrip ────────────────────────────────────────────────────────────
def test_redact_then_restore_idempotent():
    """送出 redact、模擬 LLM 原樣回傳、再 restore → 還原成原文"""
    original = (
        "陳小姐 (A123456789) 投訴\n"
        "請打 0912345678 給她，email: foo@bar.com"
    )
    redacted, mapping = redact(original)
    # 模擬 LLM 把 placeholder 原樣帶回（最理想情境）
    restored = restore(redacted, mapping)
    assert restored == original
