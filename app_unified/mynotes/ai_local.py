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


# 偵測項目起始：
#   數字+. / 數字+、 / 數字+) / 數字+）
#   (1) / （1）
#   bullet: ●, •, ・, ▪, ◆, ◇, ★, ☆, -, *
# 結尾 \s* (relaxed from spec's \s+): 中文「1、外牆修補」punct 後不空白為慣例；
# `-` 放字元類最後一個位置以免被當 range delimiter，*  literal 不需 escape。
ITEM_START_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"\d+[.、)）]"
    r"|[(（]\d+[)）]"
    r"|[●•・▪◆◇★☆*-]"
    r")"
    r"\s*",
    re.MULTILINE,
)


# 偵測「結論／決定／決議」+ 冒號 + 內容（直到行尾）。半形 / 全形冒號都吃。
# `.*?` (允許零字內容): 讓「結論：」後面什麼都沒寫的 case 也能 match keyword，
# 才能在 _extract_conclusion 裡把 keyword 從 issue text 裡 strip 掉，
# 而不是落到 no-match 分支把整段含 keyword 的 raw text 還回去。
CONCLUSION_RE = re.compile(r"(?:結論|決定|決議)\s*[:：]\s*(.*?)(?=\n|$)")


# 人員小類關鍵字。順序：離職 → 資遣 → 調整 → 其他（其他在 _classify_personnel
# 內當 fallback）。「調整」不含單字「上」「下」（會誤判「上週」「下個月」），
# 改用 2 字組合捕捉升降轉調語意。
PERSONNEL_KEYWORDS: dict[str, list[str]] = {
    "離職": ["離職", "請辭", "辭職", "自請"],
    "資遣": ["資遣", "解雇", "解僱", "終止勞動契約", "開除"],
    "調整": [
        "異動", "調整", "調職", "調班", "轉部門",
        "升職", "升遷", "升任", "升上", "上去",
        "降職", "降下", "調上", "調下", "下調", "下放", "下來",
        "轉上", "轉下",
    ],
}

PERSONNEL_ORDER: list[str] = ["離職", "資遣", "調整", "其他"]


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


def _split_items(section_body: str) -> list[str]:
    """切項目。

    回傳 list of item texts (含結論詞、不含項目編號 / bullet 字元)。
    每個項目可跨多行，直到下個 bullet pattern 出現。
    """
    if not section_body or not section_body.strip():
        return []
    matches = list(ITEM_START_RE.finditer(section_body))
    if not matches:
        return []
    items: list[str] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section_body)
        items.append(section_body[start:end].strip())
    return items


_ISSUE_TRAILING_TRIM = " ：:、，,。\n\r\t"


def _extract_conclusion(item_text: str) -> tuple[str, str | None]:
    """抽結論。

    回傳 (議題文字, 結論文字 or None)。多個結論詞取最後一個（會議紀錄
    通常先討論再寫結論，最後出現的最定論）。空字串結論視為無結論，
    但 keyword（結論／決定／決議：）仍會從 issue text 裡剝掉。
    """
    matches = list(CONCLUSION_RE.finditer(item_text))
    if not matches:
        return item_text.strip(), None
    last = matches[-1]
    issue = item_text[: last.start()].rstrip(_ISSUE_TRAILING_TRIM).strip()
    conclusion = last.group(1).strip()
    return issue, (conclusion or None)


def _classify_personnel(item_text: str) -> str:
    """判斷人員小類。回傳 '離職' / '資遣' / '調整' / '其他'。

    順序固定：離職 → 資遣 → 調整 → 其他。第一個命中的類別決定分類；
    沒命中歸『其他』。
    """
    for category in ("離職", "資遣", "調整"):
        for kw in PERSONNEL_KEYWORDS[category]:
            if kw in item_text:
                return category
    return "其他"
