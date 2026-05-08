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
    # 「自請離」(3 字) 替代「自請」(2 字)：原本「自請」會誤抓「自請假/自請病假」。
    # 「自請離」能 cover「自請離開」「自請離職」；「自請辭呈」由「請辭」keyword 抓。
    "離職": ["離職", "請辭", "辭職", "自請離"],
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
_CONCLUSION_PREFIX = "   → "  # 3 空白 + 箭頭 + 空白；用於議題下方一行縮排


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
    # 由 PERSONNEL_ORDER[:-1] 推導，避免新增類別時手寫 tuple 漂移。
    for category in PERSONNEL_ORDER[:-1]:
        for kw in PERSONNEL_KEYWORDS[category]:
            if kw in item_text:
                return category
    return "其他"


def summarize_meeting(title: str, content: str) -> str:  # noqa: ARG001
    """主入口：title + content → markdown 摘要字串。

    輸出 Format C：議題一行、結論縮排一行（→ 前綴）；沒結論的項目只列議題。
    人員 section 自動分 4 小類（離職/資遣/調整/其他），空小類不輸出。
    完全找不到 section header 時回 ⚠️ 提示文字。

    `title` 目前未使用（routes.py 仍會傳進來、tests 也用 keyword arg
    傳入），保留 param 名是為了：(1) 不破 caller 與 test 簽名；
    (2) 之後若要在 summary 開頭加 meeting title 時不必動 caller。
    """
    text = (content or "").strip()
    if not text:
        return "# 會議摘要\n\n（無內文）\n"

    sections = _split_sections(text)
    if not sections:
        return (
            "# 會議摘要\n\n"
            "⚠️ 未偵測到分類，請在會議紀錄加上『店務:』『人員:』標題\n"
        )

    lines: list[str] = ["# 會議摘要", ""]

    # ── 店務 section ──────────────────────────────────────────
    if "店務" in sections:
        items = _split_items(sections["店務"])
        if items:
            lines.append("🏪 店務")
            for idx, item in enumerate(items, start=1):
                issue, conclusion = _extract_conclusion(item)
                lines.append(f"{idx}. {issue}")
                if conclusion:
                    lines.append(f"{_CONCLUSION_PREFIX}{conclusion}")
            lines.append("")

    # ── 人員 section + 4 小類分桶 ─────────────────────────────
    if "人員" in sections:
        items = _split_items(sections["人員"])
        if items:
            buckets: dict[str, list[str]] = {cat: [] for cat in PERSONNEL_ORDER}
            for item in items:
                cat = _classify_personnel(item)
                buckets[cat].append(item)
            non_empty = [cat for cat in PERSONNEL_ORDER if buckets[cat]]
            if non_empty:
                lines.append("👥 人員")
                for cat in non_empty:
                    lines.append(f"### {cat}")
                    for idx, item in enumerate(buckets[cat], start=1):
                        issue, conclusion = _extract_conclusion(item)
                        lines.append(f"{idx}. {issue}")
                        if conclusion:
                            lines.append(f"{_CONCLUSION_PREFIX}{conclusion}")
                    lines.append("")

    return "\n".join(lines).rstrip() + "\n"
