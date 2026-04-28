"""PII redaction for outbound LLM prompts.

公開 API：
    redact(text)  -> (redacted_text, mapping)
    restore(text, mapping) -> text

mapping 格式：{placeholder -> original_value}
placeholder 樣式：[id1], [id2], [tel1], [email1], [card1], [ph1] ...
同一個值在文字內出現多次 → 同一個 placeholder。
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple


# 不用 \b：Python 3 Unicode 模式下中文也算 word char，
# 「同P124..」「4許」之間沒有 word boundary 會匹不到。
# 改用 lookaround：前後不能是 ASCII 英數。
_NB = r"(?<![A-Za-z0-9])"   # not-bounded by ASCII alnum (前)
_NA = r"(?![A-Za-z0-9])"    # not-followed by ASCII alnum (後)

# regex 順序很重要：長度最特殊的先匹配，避免被短的搶走
# email 用 @ 識別，不會跟數字撞
_EMAIL_RE = re.compile(rf"{_NB}[A-Za-z0-9._+%-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+{_NA}")
# 信用卡：4-4-4-4 數字（允許短横/空白）
_CARD_RE = re.compile(rf"{_NB}\d{{4}}[- ]?\d{{4}}[- ]?\d{{4}}[- ]?\d{{4}}{_NA}")
# 身分證：1 字母 + 第二碼 1/2 + 8 數字（嚴格版，含 ARC 第二碼 1/2 也適用）
_TWID_RE = re.compile(rf"{_NB}[A-Z][12]\d{{8}}{_NA}")
# 手機：09 開頭 10 位（允許 0912-345-678 / 0912 345 678）
_MOBILE_RE = re.compile(rf"{_NB}09\d{{2}}[- ]?\d{{3}}[- ]?\d{{3}}{_NA}")
# 市話：02-08 區碼 + 7~8 位（排除 09 行動 / 0800 / 01）
_PHONE_RE = re.compile(rf"{_NB}0[2-8]-?\d{{6,8}}{_NA}")


# (key 前綴, regex) — 順序即偵測優先序
_RULES: List[Tuple[str, re.Pattern]] = [
    ("email", _EMAIL_RE),
    ("card", _CARD_RE),
    ("id", _TWID_RE),
    ("tel", _MOBILE_RE),
    ("ph", _PHONE_RE),
]


def redact(text: str) -> Tuple[str, Dict[str, str]]:
    """掃文字、把 PII 換成 placeholder，回 (新文字, mapping)。"""
    if not text:
        return text, {}

    mapping: Dict[str, str] = {}
    # 反向表：原值 -> placeholder（同值只給一個編號）
    seen: Dict[str, str] = {}
    counters: Dict[str, int] = {prefix: 0 for prefix, _ in _RULES}

    out = text
    for prefix, pattern in _RULES:
        def _sub(m: re.Match) -> str:
            val = m.group(0)
            if val in seen:
                return seen[val]
            counters[prefix] += 1
            ph = f"[{prefix}{counters[prefix]}]"
            seen[val] = ph
            mapping[ph] = val
            return ph

        out = pattern.sub(_sub, out)

    return out, mapping


def restore(text: str, mapping: Dict[str, str]) -> str:
    """把 LLM 回的文字裡的 placeholder 換回真值。

    主要走嚴格替換；同時容忍 LLM 改成 `[ id1 ]` / `[ID1]` 等變體。
    """
    if not text or not mapping:
        return text

    out = text
    # 嚴格：直接 dict 替換，按 placeholder 長度從長到短避免子字串吃掉
    for ph in sorted(mapping.keys(), key=len, reverse=True):
        out = out.replace(ph, mapping[ph])

    # 寬鬆 fallback：` [ id1 ] ` 或 `[ID1]` 等變體
    def _loose(m: re.Match) -> str:
        inner = m.group(1).lower()
        ph = f"[{inner}]"
        return mapping.get(ph, m.group(0))

    out = re.sub(r"\[\s*([a-zA-Z]+\d+)\s*\]", _loose, out)
    return out
