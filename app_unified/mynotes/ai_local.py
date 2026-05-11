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


def _split_sections(text: str) -> list[tuple[str, str]]:
    """切「店務」「人員」section，保留出現順序與重複。

    回傳 `[(section_name, body), ...]`，**同名 section 多次出現會列在 list
    裡（不合併也不覆蓋）**；caller 自行決定合併策略。`summarize_meeting`
    把同名 section 的 items 串接起來，這樣每個 body 的 sub_cat scope 是本地
    的（第二個 `店務:` 的 items 不會繼承第一個 `店務:` 的 sub_cat 名稱）。

    body 用 `lstrip("\n\r").rstrip()` 而不是 `.strip()`：前者保留首行的
    leading whitespace（給 _split_items indent-mode 用），後者會把第一個
    縮排 item 的 TAB/space 吃掉、害 walker 把它當 preamble 略過。

    沒偵測到的 section 不在 list 裡（不是空字串）。Header 之前的內容
    （preamble）不放進任何 section。

    Contract change 2026-05-11 v3：以前回 dict，重複 header「後者覆蓋前者」
    害 user 把 `店務:` / `人員:` 分散寫在筆記不同段時資料丟一半。改成 list
    後 caller 看順序處理、不會丟資料。
    """
    matches = list(SECTION_HEADER_RE.finditer(text))
    result: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].lstrip("\n\r").rstrip()
        result.append((name, body))
    return result


# Indent-mode (2026-05-11): user's actual writing style has no item marker —
# each indented line is either an item, a sub-category header (ends with `:`),
# or a `結論：` continuation of the previous item. Detected only when
# ITEM_START_RE finds zero matches (so this is a strict fallback, never
# competes with numbered/bulleted format).
_TAB_CONCLUSION_PREFIX_RE = re.compile(r"^結論\s*[:：]\s*(.*)$")

# Sub-category prefix encoding: items belonging to a sub-category get prefixed
# with `[xxx] ` in the items list. The renderer strips this back out and emits
# `### xxx` headings. Encoding chosen so the existing list[str] contract of
# `_split_items` is preserved (Path 1 numbered/bulleted output remains pure
# strings without prefix).
_SUB_CAT_PREFIX_RE = re.compile(r"^\[([^\]]+?)\]\s+(.*)$", re.DOTALL)


def _split_items(section_body: str) -> list[str]:
    """切項目。

    優先用 ITEM_START_RE 抓編號/bullet 項目（原行為）。若一個都沒抓到，
    退化成 indent-mode：

    - 任何有 leading whitespace（TAB 或 space 都吃）的非空行才算 in-section。
    - 一份 section body 內的最小縮排深度 = sub-category level；該層 + 結尾
      帶 `:`/`：` 的行 = sub-category header，會記在 `current_sub`，之後的
      items 都掛在這個 sub-category 下。
    - 更深層的縮排 = 一般 item / 結論。
    - 結論：行 attach 到上一個 item。`結論：` 後面空字、但下一行縮排更深 →
      把那行內容當結論。
    - Item 與 conclusion 用 `\n結論：` 串接，回傳給 `_extract_conclusion` 後續
      解析；sub-category 透過 `[xxx] issue` 前綴編碼進回傳字串裡，renderer
      會解碼並輸出成 `### xxx` heading。
    """
    if not section_body or not section_body.strip():
        return []

    # Path 1: numbered/bulleted (existing behavior — unchanged)
    matches = list(ITEM_START_RE.finditer(section_body))
    if matches:
        items: list[str] = []
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(section_body)
            items.append(section_body[start:end].strip())
        return items

    # Path 2: indent-mode fallback (TAB or space — anything counts as indent)
    raw_lines = section_body.splitlines()

    # Pre-pass: for each raw line, compute (indent_depth, stripped_content) or
    # None for blanks / non-indented preamble noise.
    cells: list[tuple[int, str] | None] = []
    for ln in raw_lines:
        if not ln.strip():
            cells.append(None)
            continue
        lead = len(ln) - len(ln.lstrip())
        if lead == 0:
            cells.append(None)  # non-indented inside section = preamble, skip
            continue
        cells.append((lead, ln.lstrip()))

    indents = [c[0] for c in cells if c is not None]
    if not indents:
        return []
    min_indent = min(indents)

    items: list[str] = []
    current_sub: str | None = None
    current_issue: str | None = None
    current_conclusion: str | None = None

    def flush() -> None:
        nonlocal current_issue, current_conclusion
        if current_issue is None:
            return
        head = f"[{current_sub}] {current_issue}" if current_sub else current_issue
        if current_conclusion is not None:
            items.append(f"{head}\n結論：{current_conclusion}")
        else:
            items.append(head)
        current_issue = None
        current_conclusion = None

    i = 0
    while i < len(cells):
        cell = cells[i]
        i += 1
        if cell is None:
            continue
        indent, content = cell

        # Conclusion line — works at any indent depth, attaches to last item.
        m_conc = _TAB_CONCLUSION_PREFIX_RE.match(content)
        if m_conc:
            conclusion_text = m_conc.group(1).strip()
            if not conclusion_text:
                # `結論：` alone — look ahead for a deeper-indented line and
                # treat that as the conclusion content. Skip blanks while
                # searching; stop at first non-blank cell.
                j = i
                while j < len(cells) and cells[j] is None:
                    j += 1
                if j < len(cells):
                    next_cell = cells[j]
                    if next_cell is not None and next_cell[0] > indent:
                        conclusion_text = next_cell[1].rstrip()
                        i = j + 1  # consume the continuation
            if current_issue is not None:
                current_conclusion = conclusion_text or None
            # orphan conclusion (no current_issue) — silently dropped
            continue

        # Sub-category header: at min_indent AND content ends with `:` / `：`.
        # Items at deeper indent that happen to end with `:` (e.g. "點餐系統:")
        # are treated as items, not sub-categories — they're under the
        # existing sub-category.
        stripped = content.rstrip()
        if indent == min_indent and (stripped.endswith(":") or stripped.endswith("：")):
            flush()
            sub_name = stripped.rstrip(":：").strip()
            if sub_name:
                current_sub = sub_name
            continue

        # Regular item — start new (flush any pending previous item first).
        flush()
        current_issue = stripped

    flush()
    return items


_ISSUE_TRAILING_TRIM = " ：:、，,。\n\r\t"
_CONCLUSION_PREFIX = "   → "  # 3 空白 + 箭頭 + 空白；用於議題下方一行縮排


def _extract_conclusion(item_text: str) -> tuple[str, str | None]:
    """抽結論。

    回傳 (議題文字, 結論文字 or None)。多個結論詞取最後一個（會議紀錄
    通常先討論再寫結論，最後出現的最定論）。空字串結論視為無結論，
    但 keyword（結論／決定／決議：）仍會從 issue text 裡剝掉。

    無結論的 item 也 rstrip(_ISSUE_TRAILING_TRIM)：避免「延伸議題:」這種
    user 拿來當小標題、但因為 indent 不在 min_indent 不能升 sub-category
    的 item 在 output 留下尾巴的 `:`/`：`/、，,。 等視覺噪音。
    """
    matches = list(CONCLUSION_RE.finditer(item_text))
    if not matches:
        return item_text.rstrip(_ISSUE_TRAILING_TRIM).strip(), None
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


def _decode_sub_cat(item: str) -> tuple[str | None, str]:
    """從 `_split_items` 回傳的編碼字串中拆出 sub-category 跟 body。

    沒前綴 → (None, item) 原樣回。 有前綴 → ("代理商", "K單機...") 之類。
    """
    m = _SUB_CAT_PREFIX_RE.match(item)
    if m:
        return m.group(1), m.group(2)
    return None, item


def _render_storeops(items: list[str]) -> list[str]:
    """渲染店務 section：依 sub-category 分組、每組內部從 1 開始編號。

    沒 sub-category 的 items 直接平鋪在最上（沒 ### heading），編號自成一組。
    """
    out: list[str] = ["🏪 店務"]
    current_sub: str | None = None
    counter = 0
    for item in items:
        sub, body = _decode_sub_cat(item)
        if sub != current_sub:
            # New sub-category boundary — emit heading and reset counter.
            current_sub = sub
            counter = 0
            if sub:
                out.append(f"### {sub}")
        counter += 1
        issue, conclusion = _extract_conclusion(body)
        out.append(f"{counter}. {issue}")
        if conclusion:
            out.append(f"{_CONCLUSION_PREFIX}{conclusion}")
    out.append("")
    return out


def _render_personnel(items: list[str]) -> list[str]:
    """渲染人員 section。

    若 items 含 sub-category prefix → 沿用 sub-cat 分組（user 自己分類）。
    否則 → 用 PERSONNEL_KEYWORDS 跑 4 桶（離職/資遣/調整/其他）。
    """
    has_sub_cat = any(_SUB_CAT_PREFIX_RE.match(it) for it in items)
    out: list[str] = ["👥 人員"]

    if has_sub_cat:
        current_sub: str | None = None
        counter = 0
        for item in items:
            sub, body = _decode_sub_cat(item)
            if sub != current_sub:
                current_sub = sub
                counter = 0
                if sub:
                    out.append(f"### {sub}")
            counter += 1
            issue, conclusion = _extract_conclusion(body)
            out.append(f"{counter}. {issue}")
            if conclusion:
                out.append(f"{_CONCLUSION_PREFIX}{conclusion}")
        out.append("")
        return out

    # No sub-cat → existing 4-bucket personnel keyword classification.
    buckets: dict[str, list[str]] = {cat: [] for cat in PERSONNEL_ORDER}
    for item in items:
        cat = _classify_personnel(item)
        buckets[cat].append(item)
    non_empty = [cat for cat in PERSONNEL_ORDER if buckets[cat]]
    if not non_empty:
        return []
    for cat in non_empty:
        out.append(f"### {cat}")
        for idx, item in enumerate(buckets[cat], start=1):
            issue, conclusion = _extract_conclusion(item)
            out.append(f"{idx}. {issue}")
            if conclusion:
                out.append(f"{_CONCLUSION_PREFIX}{conclusion}")
        out.append("")
    return out


def summarize_meeting(title: str, content: str) -> str:  # noqa: ARG001
    """主入口：title + content → markdown 摘要字串。

    輸出 Format C：議題一行、結論縮排一行（→ 前綴）；沒結論的項目只列議題。
    人員 section 自動分 4 小類（離職/資遣/調整/其他）；若 user 自己用 sub-cat
    分類，改沿用 user 的 sub-cat。完全找不到 section header 時回 ⚠️ 提示文字。

    `title` 目前未使用（routes.py 仍會傳進來、tests 也用 keyword arg
    傳入），保留 param 名是為了：(1) 不破 caller 與 test 簽名；
    (2) 之後若要在 summary 開頭加 meeting title 時不必動 caller。
    """
    text = (content or "").strip()
    if not text:
        return "# 會議摘要\n\n（無內文）\n"

    sections = _split_sections(text)  # list[(name, body)] — ordered, allows duplicates
    if not sections:
        return (
            "# 會議摘要\n\n"
            "⚠️ 未偵測到分類，請在會議紀錄加上『店務:』『人員:』標題\n"
        )

    lines: list[str] = ["# 會議摘要", ""]

    # Merge across repeated section headers — user habit is to write
    # 店務 / 人員 in multiple chunks scattered in the note. We parse each
    # body block independently (so sub_cat scope is local), then concat
    # the resulting items in document order. Rendering sees them as one
    # contiguous stream per section name.
    storeops_items: list[str] = []
    personnel_items: list[str] = []
    for name, body in sections:
        items = _split_items(body)
        if name == "店務":
            storeops_items.extend(items)
        elif name == "人員":
            personnel_items.extend(items)

    if storeops_items:
        lines.extend(_render_storeops(storeops_items))

    if personnel_items:
        block = _render_personnel(personnel_items)
        if block:
            lines.extend(block)

    return "\n".join(lines).rstrip() + "\n"
