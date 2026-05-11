"""mynotes meeting note local parser — single-note pipeline.

從會議紀錄抽取「店務」「人員」section，每 section 內條列項目，
人員 section 自動依關鍵字分 4 小類（離職/資遣/調整/其他）。
不依賴雲端 LLM、不依賴 sentence-transformers / sklearn。
"""
import re


# 偵測 section header：可有可無【】或 [] 包覆、可有可無冒號、整行只有 header
# 大分類順序（display order）：代理商 → 追蹤部份 → 店務 → 人員
# 追蹤部分（半形「分」）跟 追蹤部份（全形「份」）都接受，內部 normalize 成 追蹤部份
SECTION_HEADER_RE = re.compile(
    r"^\s*[【\[]?\s*(店務|人員|代理商|追蹤部份|追蹤部分)\s*[】\]]?\s*[:：]?\s*$",
    re.MULTILINE,
)

# 顯示順序 + icon
DISPLAY_ORDER = ["代理商", "追蹤部份", "店務", "人員"]
SECTION_ICONS = {
    "代理商": "🤝",
    "追蹤部份": "🔍",
    "店務": "🏪",
    "人員": "👥",
}
SECTION_NORMALIZE = {"追蹤部分": "追蹤部份"}  # 兩種寫法 normalize 同一個 key


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

# v7 (2026-05-11): "延伸議題:" / "延伸:" magic keyword. When seen at item
# level, the line itself doesn't start a new item — it marks that the
# following content + 結論 are an EXTENSION of the current item (a follow-up
# discussion under the same topic). The walker enters extension mode and
# collects extension_issue + extension_conclusion separately; flush() then
# encodes both halves into a single item string using `_EXT_DELIM` as the
# delimiter, and the renderer outputs them on continuation lines under the
# primary `1. xxx → conc` pair.
_TAB_EXTENSION_PREFIX_RE = re.compile(r"^延伸(?:議題)?\s*[:：]\s*(.*)$")
_EXT_DELIM = "\x1fEXT\x1f"

# Sub-category prefix encoding: items belonging to a sub-category get prefixed
# with `[xxx] ` in the items list. The renderer strips this back out and emits
# `### xxx` headings. Encoding chosen so the existing list[str] contract of
# `_split_items` is preserved (Path 1 numbered/bulleted output remains pure
# strings without prefix).
_SUB_CAT_PREFIX_RE = re.compile(r"^\[([^\]]+?)\]\s+(.*)$", re.DOTALL)


def _split_items(section_body: str) -> list[str]:
    """切項目 — v4 unified walker。

    舊版有 Path 1（編號 bullet）vs Path 2（indent）兩條互斥路徑。實機踩到
    user 同篇筆記混寫兩種（大部分 indent + 某段「追蹤部分 + 1./2.」編號
    list）→ Path 1 一旦匹配就把前面所有 indent 內容當 preamble 丟掉。改成
    一條 walker 同時吃 bullet 跟 indent，所有 line 都有機會變 item。

    處理規則：
    - **Bullet line**（`1.` / `1、` / `(1)` / `●` / `-` 等）→ item，把
      bullet 前綴拔掉。
    - **Indent line**（leading TAB or space）→ item / 結論 / sub-category。
    - **Preamble line**（column 0 非 bullet 文字，e.g. 「追蹤部分」「//」）
      → 視為 section 內的視覺分隔線：flush 目前 item、reset current_sub
      讓後續 bullet items 不會繼承前面 indent block 的 sub_cat。
    - **Blank** → 跳過。

    Sub-category：indent-kind 且 indent == min_indent_among_non_conclusion_lines
    且 結尾 `:`/`：`。算 min_indent 時排除結論行（結論行的 indent 不代表
    階層、會把 min_indent 算偏）。

    結論：indent or bullet 都吃 `結論：xxx` 前綴。內文空時 peek 後面同 indent
    block 內第一個 strictly-deeper indent 行當結論內文。

    Sub-category 透過 `[xxx] issue` 前綴編碼進回傳字串，renderer 解碼成
    `### xxx` heading。
    """
    if not section_body or not section_body.strip():
        return []

    raw_lines = section_body.splitlines()

    # Pre-pass: classify each line.
    # Cell shape: (kind, indent_or_0, content_or_empty)
    # kinds: "blank", "preamble", "bullet", "indent"
    cells: list[tuple[str, int, str]] = []
    for ln in raw_lines:
        if not ln.strip():
            cells.append(("blank", 0, ""))
            continue
        # Bullet/number prefix? (matches 1. / 1、 / (1) / ● / - / * etc)
        m_item = ITEM_START_RE.match(ln)
        if m_item:
            cells.append(("bullet", 0, ln[m_item.end():]))
            continue
        lead = len(ln) - len(ln.lstrip())
        if lead == 0:
            # Non-bullet column-0 line inside section — visual break (e.g.
            # "追蹤部分" header that isn't a `xxx:` sub-cat, or "//" separator).
            cells.append(("preamble", 0, ln))
            continue
        cells.append(("indent", lead, ln.lstrip()))

    # min_indent: only considering non-conclusion indent-kind lines.
    # Conclusion lines often sit at oddly-shallow indents (user's "   結論:"
    # at indent 3 when items are at 4-9) and would distort the sub-cat anchor.
    indents_for_min = [
        ind for kind, ind, content in cells
        if kind == "indent" and not _TAB_CONCLUSION_PREFIX_RE.match(content)
    ]
    min_indent = min(indents_for_min) if indents_for_min else None

    items: list[str] = []
    current_sub: str | None = None
    current_issue: str | None = None
    current_conclusion: str | None = None
    just_saw_conclusion: bool = False  # 結論 line is end-of-item marker
    # Extension state (v7): triggered by `延伸議題:` line. While true, content
    # lines APPEND to ext_issue and 結論 lines set ext_conclusion.
    in_extension: bool = False
    ext_issue: str | None = None
    ext_conclusion: str | None = None

    def flush() -> None:
        nonlocal current_issue, current_conclusion, just_saw_conclusion
        nonlocal in_extension, ext_issue, ext_conclusion
        if current_issue is None:
            return
        head = f"[{current_sub}] {current_issue}" if current_sub else current_issue
        if current_conclusion is not None:
            head = f"{head}\n結論：{current_conclusion}"
        if in_extension and ext_issue is not None:
            ext_block = ext_issue
            if ext_conclusion is not None:
                ext_block = f"{ext_block}\n結論：{ext_conclusion}"
            head = f"{head}{_EXT_DELIM}{ext_block}"
        items.append(head)
        current_issue = None
        current_conclusion = None
        just_saw_conclusion = False
        in_extension = False
        ext_issue = None
        ext_conclusion = None

    i = 0
    while i < len(cells):
        kind, indent, content = cells[i]
        i += 1

        if kind == "blank":
            # User convention: blank line = topic boundary. Flush whatever
            # we've been accumulating into the current item.
            flush()
            continue

        if kind == "preamble":
            # Visual break — flush current item AND reset sub_cat scope so
            # following bullet/indent items don't inherit a sub_cat from a
            # different conceptual block above.
            flush()
            current_sub = None
            continue

        # bullet or indent kinds carry content.

        # Extension marker (v7) — must come BEFORE conclusion check because
        # the regex is more specific. `延伸議題:` / `延伸:` line: keep the
        # current item alive, enter extension mode so subsequent content
        # + 結論 attach to ext_issue / ext_conclusion instead of replacing.
        m_ext = _TAB_EXTENSION_PREFIX_RE.match(content)
        if m_ext and current_issue is not None:
            in_extension = True
            inline = m_ext.group(1).strip()
            # If the 延伸議題: line has inline content (rare — user might
            # write `延伸: xxx`), seed ext_issue with it.
            if inline:
                ext_issue = inline
            just_saw_conclusion = False
            continue

        # Conclusion line?
        m_conc = _TAB_CONCLUSION_PREFIX_RE.match(content)
        if m_conc:
            conclusion_text = m_conc.group(1).strip()
            if not conclusion_text:
                # `結論：` alone — peek next non-blank cell; if it's
                # indent-kind AND strictly deeper than the 結論 line, take
                # its content as the conclusion text and consume that cell.
                j = i
                while j < len(cells) and cells[j][0] == "blank":
                    j += 1
                if j < len(cells):
                    nk, nind, ncontent = cells[j]
                    if kind == "indent" and nk == "indent" and nind > indent:
                        conclusion_text = ncontent.rstrip()
                        i = j + 1
            if in_extension:
                # Conclusion of the extension half — attach to ext_conclusion.
                if current_issue is not None:
                    ext_conclusion = conclusion_text or None
            else:
                if current_issue is not None:
                    current_conclusion = conclusion_text or None
            just_saw_conclusion = True
            continue

        # Sub-category header: only indent-kind at min_indent ending with
        # `:` / `：` AND followed by a strictly deeper-indented line (proof
        # that real content lives under this header). Without the deeper
        # follow-up the line is just a topic title that happens to end with
        # `:` — render it as a regular item (e.g. user writes "點餐系統:"
        # then `結論:J/JJ` at the same indent, meaning the topic itself
        # IS the item and the conclusion is its outcome).
        stripped = content.rstrip()
        if (kind == "indent"
            and min_indent is not None
            and indent == min_indent
            and (stripped.endswith(":") or stripped.endswith("："))):
            # Peek next non-blank cell; sub-cat only if it's an indent-kind
            # line at strictly deeper indent.
            j = i
            while j < len(cells) and cells[j][0] == "blank":
                j += 1
            has_deeper_content = (
                j < len(cells)
                and cells[j][0] == "indent"
                and cells[j][1] > indent
            )
            if has_deeper_content:
                flush()
                sub_name = stripped.rstrip(":：").strip()
                if sub_name:
                    current_sub = sub_name
                continue
            # else: fall through to regular item handling below

        # Regular item content line.
        #
        # Multi-line rule (user habit 2026-05-11): adjacent indent lines
        # without a blank/結論 break in between are CONTINUATIONS of the
        # same item, not separate items. Bullet-prefixed lines (1./2./●)
        # always flush — bullets are inherently distinct items.
        if kind == "bullet":
            flush()
            current_issue = stripped
        elif in_extension:
            # Content under an active 延伸議題 marker — append to ext_issue.
            if ext_issue is None or just_saw_conclusion:
                # After the extension's 結論, a new content line means a new
                # *primary* item starting — exit extension mode by flushing.
                if just_saw_conclusion:
                    flush()
                    current_issue = stripped
                else:
                    ext_issue = stripped
            else:
                ext_issue = f"{ext_issue}\n{stripped}"
        elif current_issue is None or just_saw_conclusion:
            # New item: previous one (if any) ended at 結論 or doesn't exist.
            flush()
            current_issue = stripped
        else:
            # Continuation of the current multi-line item.
            current_issue = f"{current_issue}\n{stripped}"
        just_saw_conclusion = False

    flush()
    return items


_ISSUE_TRAILING_TRIM = " ：:、，,。\n\r\t"
_CONCLUSION_PREFIX = "   → "  # 3 空白 + 箭頭 + 空白；用於議題下方一行縮排
_ISSUE_CONT_PREFIX = "   "  # 3 空白；多行議題的續行對齊「1. 」之後的字位


def _render_issue_lines(counter: int, issue: str) -> list[str]:
    """把可能含 `\n` 的 issue 字串拆成 `1. 第一行` + `   續行`。"""
    parts = issue.split("\n")
    out = [f"{counter}. {parts[0]}"]
    for cont in parts[1:]:
        if cont.strip():
            out.append(f"{_ISSUE_CONT_PREFIX}{cont}")
    return out


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


def _split_extension(body: str) -> tuple[str, str | None]:
    """從 item body 中拆出 primary 部分跟 extension 部分。

    body 用 `_EXT_DELIM` 分隔 primary 與 extension（v7 encoding）。沒
    delimiter 就 (body, None)。
    """
    if _EXT_DELIM in body:
        primary, ext = body.split(_EXT_DELIM, 1)
        return primary, ext
    return body, None


def _render_item_lines(counter: int, body: str) -> list[str]:
    """單一 item 的渲染（不論 section 走哪條 renderer 都共用）。

    處理：sub-cat decode → extension split → primary `1. issue + → conc` →
    若有 extension 加 `延伸議題：ext_issue + → ext_conc` 縮排在同 item 下。
    """
    primary, ext = _split_extension(body)
    issue, conclusion = _extract_conclusion(primary)
    out = _render_issue_lines(counter, issue)
    if conclusion:
        out.append(f"{_CONCLUSION_PREFIX}{conclusion}")
    if ext is not None:
        ext_issue, ext_conclusion = _extract_conclusion(ext)
        ext_lines = ext_issue.split("\n")
        out.append(f"{_ISSUE_CONT_PREFIX}延伸議題：{ext_lines[0]}")
        for cont in ext_lines[1:]:
            if cont.strip():
                out.append(f"{_ISSUE_CONT_PREFIX}{cont}")
        if ext_conclusion:
            out.append(f"{_CONCLUSION_PREFIX}{ext_conclusion}")
    return out


def _render_section(icon: str, name: str, items: list[str]) -> list[str]:
    """渲染通用 section（代理商/追蹤部份/店務）：依 sub-category 分組、
    每組內部從 1 開始編號。沒 sub-category 的 items 直接平鋪在最上（沒
    ### heading），編號自成一組。

    人員 section 走另一條 `_render_personnel`（有 4 桶 keyword 邏輯）。
    """
    out: list[str] = [f"{icon} {name}"]
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
        out.extend(_render_item_lines(counter, body))
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
            out.extend(_render_item_lines(counter, body))
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
            # _classify_personnel works on the full encoded string; renderer
            # decodes here. Items in buckets are still full encoded strings
            # (no sub-cat prefix in personnel mode, but may have extensions).
            _, body = _decode_sub_cat(item)
            out.extend(_render_item_lines(idx, body))
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

    # Merge across repeated section headers — user habit is to scatter
    # 店務 / 人員 / etc. through the note in multiple chunks. Parse each
    # body block independently (so sub_cat scope is local), then concat
    # items per section name. Rendering sees them as one contiguous stream
    # per section name. Output in DISPLAY_ORDER, skip sections with no items.
    accumulators: dict[str, list[str]] = {name: [] for name in DISPLAY_ORDER}
    for raw_name, body in sections:
        canonical = SECTION_NORMALIZE.get(raw_name, raw_name)
        if canonical not in accumulators:
            continue  # defensive — shouldn't happen if regex matches DISPLAY_ORDER
        items = _split_items(body)
        accumulators[canonical].extend(items)

    for sec_name in DISPLAY_ORDER:
        items = accumulators[sec_name]
        if not items:
            continue
        if sec_name == "人員":
            block = _render_personnel(items)
            if block:
                lines.extend(block)
        else:
            lines.extend(_render_section(SECTION_ICONS[sec_name], sec_name, items))

    return "\n".join(lines).rstrip() + "\n"
