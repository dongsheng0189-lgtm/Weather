"""mynotes meeting note local parser — single-note pipeline.

從會議紀錄抽取「店務」「人員」section，每 section 內條列項目，
人員 section 自動依關鍵字分 4 小類（離職/資遣/調整/其他）。
不依賴雲端 LLM、不依賴 sentence-transformers / sklearn。
"""
import re


# 偵測 section header：可有可無【】或 [] 包覆、可有可無冒號、整行只有 header
SECTION_HEADER_RE = re.compile(
    r"^\s*[【\[]?\s*(店務|人員)\s*[】\]]?\s*[:：]?\s*$",
    re.MULTILINE,
)


def _split_sections(text: str) -> dict[str, str]:
    """切「店務」「人員」section。

    回傳 {section_name: section_body_text}。
    沒偵測到的 section 不在 dict 裡（不是空字串）。
    Header 之前的內容（preamble）不放進任何 section。
    """
    matches = list(SECTION_HEADER_RE.finditer(text))
    result: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        result[name] = body
    return result
