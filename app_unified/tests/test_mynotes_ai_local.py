"""mynotes meeting note local parser — unit tests for pure functions."""
import pytest

from mynotes.ai_local import (
    _classify_personnel,
    _extract_conclusion,
    _split_items,
    _split_sections,
    summarize_meeting,
)


def _section_names(result):
    """Helper: 取出 _split_sections 回傳 list[(name, body)] 裡的 name set。"""
    return {name for name, _ in result}


def _body_for(result, name):
    """Helper: 取第一個 name 對應的 body（test 只要 single-occurrence 情境用）。"""
    for n, body in result:
        if n == name:
            return body
    return ""


def test_split_sections_both_present():
    text = """
店務:
1. 外牆修補
2. 客訴

人員:
1. 小李離職
""".strip()
    result = _split_sections(text)
    assert _section_names(result) == {"店務", "人員"}
    assert "外牆修補" in _body_for(result, "店務")
    assert "客訴" in _body_for(result, "店務")
    assert "小李離職" in _body_for(result, "人員")


def test_split_sections_only_one():
    text = """
店務:
1. 外牆修補
""".strip()
    result = _split_sections(text)
    names = _section_names(result)
    assert "店務" in names
    assert "人員" not in names


def test_split_sections_neither():
    text = "今天會議聊了很多事情。沒有結構化的標頭。"
    result = _split_sections(text)
    assert result == []


def test_split_sections_full_width_colon():
    text = """
店務：
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "店務" in _section_names(result)


def test_split_sections_brackets():
    text = """
【店務】
1. 外牆修補

[人員]
1. 小李離職
""".strip()
    result = _split_sections(text)
    assert _section_names(result) == {"店務", "人員"}


def test_split_sections_bare_word():
    text = """
店務
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "店務" in _section_names(result)


def test_split_sections_preamble_dropped():
    """Header 之前的內容不出現在任何 section body 裡。"""
    text = """
日期：2026/05/07
出席：店長、副店

店務:
1. 外牆修補
""".strip()
    result = _split_sections(text)
    body = _body_for(result, "店務")
    assert "日期" not in body
    assert "出席" not in body


def test_split_sections_duplicate_header_preserves_order_and_both():
    """重複 section header 都列出來、按出現順序（v3 contract，舊版是 last-wins，
    害 user 把 `店務:` / `人員:` 分散寫在筆記不同段時資料丟一半）。"""
    text = """
店務:
1. 第一段店務

店務:
2. 第二段店務
""".strip()
    result = _split_sections(text)
    # 兩筆 entry、都是 "店務"、order 保留
    assert [name for name, _ in result] == ["店務", "店務"]
    bodies = [body for _, body in result]
    assert "第一段店務" in bodies[0]
    assert "第二段店務" in bodies[1]


def test_split_items_arabic_dot():
    section = """
1. 外牆修補
2. 客訴
3. 庫存不足
""".strip()
    items = _split_items(section)
    assert len(items) == 3
    assert items[0] == "外牆修補"
    assert items[1] == "客訴"


def test_split_items_chinese_punct():
    section = """
1、外牆修補
2、客訴
""".strip()
    items = _split_items(section)
    assert len(items) == 2
    assert items[0] == "外牆修補"


def test_split_items_paren():
    section = """
(1) 外牆修補
(2) 客訴
""".strip()
    items = _split_items(section)
    assert len(items) == 2
    assert items[0] == "外牆修補"


def test_split_items_bullets():
    section = """
- 外牆修補
- 客訴
* 庫存不足
● 收銀機卡紙
""".strip()
    items = _split_items(section)
    assert len(items) == 4
    assert items[0] == "外牆修補"
    assert items[1] == "客訴"
    assert items[2] == "庫存不足"
    assert items[3] == "收銀機卡紙"


def test_split_items_multiline_item():
    """每個項目可跨多行直到下個 item bullet。"""
    section = """
1. 外牆修補, 廠商報價 8 萬太貴
   結論：請小張下週前找 3 家報價
2. 客訴
""".strip()
    items = _split_items(section)
    assert len(items) == 2
    assert "結論" in items[0]
    assert "請小張下週前找 3 家報價" in items[0]


def test_split_items_empty_string():
    assert _split_items("") == []


def test_split_items_whitespace_only():
    assert _split_items("   \n  ") == []


def test_split_items_no_bullets():
    """沒 bullet 的內容回空 list（避免誤把整段當一個項目）。"""
    section = "今天討論很多事情但沒列點"
    assert _split_items(section) == []


def test_extract_conclusion_simple():
    """通常 case：半形冒號無空白，最常見的會議紀錄寫法。"""
    issue, conclusion = _extract_conclusion("外牆修補\n結論:請廠商評估")
    assert issue == "外牆修補"
    assert conclusion == "請廠商評估"


def test_extract_conclusion_full_width_colon():
    """中文標準：全形冒號。"""
    issue, conclusion = _extract_conclusion("外牆修補\n結論：請廠商評估")
    assert issue == "外牆修補"
    assert conclusion == "請廠商評估"


def test_extract_conclusion_half_width_colon():
    issue, conclusion = _extract_conclusion("外牆修補\n結論: 請廠商評估")
    assert conclusion == "請廠商評估"


def test_extract_conclusion_synonym_jueding():
    issue, conclusion = _extract_conclusion("客訴處理\n決定：本週六開檢討會")
    assert conclusion == "本週六開檢討會"


def test_extract_conclusion_synonym_jueyi():
    issue, conclusion = _extract_conclusion("年終獎金\n決議：每人發 2 個月")
    assert conclusion == "每人發 2 個月"


def test_extract_conclusion_none():
    issue, conclusion = _extract_conclusion("庫存不足, 紙杯只剩 2 箱")
    assert issue == "庫存不足, 紙杯只剩 2 箱"
    assert conclusion is None


def test_extract_conclusion_empty_string_treated_as_none():
    """空結論：keyword 仍要從 issue 裡剝掉，不是把『結論：』還回去。"""
    issue, conclusion = _extract_conclusion("外牆修補\n結論：")
    assert issue == "外牆修補"
    assert conclusion is None


def test_extract_conclusion_multiple_takes_last():
    """同一項目多個結論詞 → 取最後一個（議論後寫的最定論）。
    issue 保留中段討論（含先前 keyword 行），不嘗試做語意拆解。
    """
    text = "客訴\n結論：先退費\n後來重新討論\n決議：本週六開檢討會"
    issue, conclusion = _extract_conclusion(text)
    assert conclusion == "本週六開檢討會"
    assert issue == "客訴\n結論：先退費\n後來重新討論"


def test_extract_conclusion_keyword_inside_issue_text():
    """議題本身包含『客訴:』這種非關鍵字冒號 → 不應誤判為結論。"""
    text = "客訴：上週六飲料品質有問題, 已退費道歉\n決定：本週六開檢討會"
    issue, conclusion = _extract_conclusion(text)
    assert issue == "客訴：上週六飲料品質有問題, 已退費道歉"
    assert conclusion == "本週六開檢討會"


def test_classify_personnel_lizhi():
    assert _classify_personnel("小李提離職, 5/15 生效") == "離職"
    assert _classify_personnel("王副店請辭") == "離職"
    assert _classify_personnel("阿美辭職") == "離職"
    assert _classify_personnel("自請離開") == "離職"


def test_classify_personnel_zi_qing_jia_not_lizhi():
    """『自請假/自請病假』不應誤判為離職（「自請」keyword 太寬會中槍）。"""
    assert _classify_personnel("小王自請病假三天") == "其他"
    assert _classify_personnel("店長自請假處理家事") == "其他"


def test_classify_personnel_ziqian():
    assert _classify_personnel("阿華因業績不佳資遣") == "資遣"
    assert _classify_personnel("解雇") == "資遣"
    assert _classify_personnel("公司決定解僱") == "資遣"
    assert _classify_personnel("終止勞動契約") == "資遣"
    assert _classify_personnel("被開除") == "資遣"


def test_classify_personnel_tiaozheng():
    assert _classify_personnel("王經理升上副總") == "調整"
    assert _classify_personnel("小張調下倉庫部") == "調整"
    assert _classify_personnel("升職通知") == "調整"
    assert _classify_personnel("人事異動") == "調整"
    assert _classify_personnel("由 A 班轉上 B 班") == "調整"
    assert _classify_personnel("從正職下來打工") == "調整"
    assert _classify_personnel("升上去店長") == "調整"


def test_classify_personnel_qita():
    """不命中任何關鍵字 → 其他。"""
    assert _classify_personnel("店長 5/12-14 請假") == "其他"
    assert _classify_personnel("小王績效好, 考慮獎金") == "其他"
    assert _classify_personnel("生日禮金發放") == "其他"


def test_classify_personnel_priority_order():
    """順序：離職 → 資遣 → 調整 → 其他。同時命中時取較前面類別。"""
    # 含「離職」與「調整」→ 應為離職
    assert _classify_personnel("小李離職, 由 B 升上接替") == "離職"
    # 含「資遣」與「調整」→ 應為資遣
    assert _classify_personnel("阿華被解雇後降職處置") == "資遣"


def test_classify_personnel_does_not_match_bare_shang_xia():
    """『上週討論』『下個月』不應誤判為調整。"""
    assert _classify_personnel("上週討論的客訴") == "其他"
    assert _classify_personnel("下個月排班檢討") == "其他"
    assert _classify_personnel("上班時間調整") == "調整"  # 「調整」單字仍命中


def test_summarize_meeting_e2e():
    """完整 spec 範例 input → 對齊 expected output。"""
    content = """
店務:
1. 外牆修補, 廠商報價 8 萬太貴, 先問其他家
   結論：請小張下週前找 3 家報價
2. 客訴：上週六飲料品質有問題, 已退費道歉
   決定：本週六開檢討會
3. 庫存不足, 紙杯只剩 2 箱
4. 收銀機卡紙

人員:
1. 小李提離職, 5/15 生效
2. 阿華因業績不佳資遣, 月底前
3. 王經理升上副總, 6/1 生效
4. 店長 5/12-14 請假
""".strip()

    expected = """# 聊聊摘要

🏪 店務
1. 外牆修補, 廠商報價 8 萬太貴, 先問其他家
   → 請小張下週前找 3 家報價
2. 客訴：上週六飲料品質有問題, 已退費道歉
   → 本週六開檢討會
3. 庫存不足, 紙杯只剩 2 箱
4. 收銀機卡紙

👥 人員
### 離職
1. 小李提離職, 5/15 生效

### 資遣
1. 阿華因業績不佳資遣, 月底前

### 調整
1. 王經理升上副總, 6/1 生效

### 其他
1. 店長 5/12-14 請假
"""

    result = summarize_meeting(title="5/7 月會", content=content)
    assert result == expected


def test_summarize_meeting_no_sections_warns():
    content = "今天會議聊了很多但沒分類"
    result = summarize_meeting(title="x", content=content)
    assert "⚠️" in result
    assert "店務" in result and "人員" in result  # 提示文字裡有
    # 提示文字有具體引導動作，不只是含關鍵字
    assert "請在會議紀錄加上" in result


def test_summarize_meeting_no_summary_markers():
    """輸出絕不可包含『摘要開始』『摘要結束』 marker（user 明確要求拿掉）。"""
    content = """
店務:
1. 外牆修補
""".strip()
    result = summarize_meeting(title="x", content=content)
    assert "摘要開始" not in result
    assert "摘要結束" not in result


def test_summarize_meeting_no_conclusion_label_omitted():
    """店務 / 人員 兩者沒結論的項目都不顯示『（無結論）』。"""
    content = """
店務:
1. 庫存不足

人員:
1. 店長請假
""".strip()
    result = summarize_meeting(title="x", content=content)
    assert "無結論" not in result


def test_summarize_meeting_empty_personnel_buckets_omitted():
    """空小類不輸出（只有離職的話，資遣/調整/其他 標頭都不出現）。"""
    content = """
人員:
1. 小李離職
""".strip()
    result = summarize_meeting(title="x", content=content)
    assert "### 離職" in result
    assert "### 資遣" not in result
    assert "### 調整" not in result
    assert "### 其他" not in result


def test_summarize_meeting_section_with_no_items_dropped():
    """有 section header 但沒項目 → 該 section 整個不輸出。"""
    content = """
店務:

人員:
1. 小李離職
""".strip()
    result = summarize_meeting(title="x", content=content)
    assert "🏪 店務" not in result
    assert "👥 人員" in result


def test_summarize_meeting_only_storeops_no_personnel():
    """只有店務沒人員的 mirror case，確認非對稱情境也走得通。"""
    content = """
店務:
1. 庫存不足
2. 收銀機卡紙
""".strip()
    result = summarize_meeting(title="x", content=content)
    assert "🏪 店務" in result
    assert "1. 庫存不足" in result
    assert "2. 收銀機卡紙" in result
    assert "👥 人員" not in result
    assert "###" not in result  # 沒人員小類 header


def test_summarize_meeting_empty_content():
    result = summarize_meeting(title="x", content="")
    assert "（無內文）" in result


# ── TAB-indent mode (2026-05-11) ─────────────────────────────────────────────

TAB_INPUT_BOTH_SECTIONS = """店務：
\t冷氣維修
\t結論：請廠商來估
\t外牆修補
\t結論：8月發包

人員：
\t阿明
\t結論：調班
\t小華
\t結論：離職、8月底交接
"""


def test_split_items_tab_indent_with_conclusion():
    """TAB-mode: item line + same-indent 結論：line pair into one entry."""
    section_body = "\t冷氣維修\n\t結論:請廠商來估\n\t外牆修補\n\t結論：8月發包\n"
    items = _split_items(section_body)
    assert items == [
        "冷氣維修\n結論：請廠商來估",
        "外牆修補\n結論：8月發包",
    ]


def test_split_items_tab_indent_item_without_conclusion():
    """Item alone with no following 結論 line — emit issue only.

    Two adjacent lines need a blank line OR a 結論 between them to be
    treated as separate items (user habit 2026-05-11 "空一行 = 不同議題").
    Without the blank, they merge into one multi-line item.
    """
    section_body = "\t冷氣維修\n\n\t外牆修補\n\t結論：8月發包\n"
    items = _split_items(section_body)
    assert items == [
        "冷氣維修",
        "外牆修補\n結論：8月發包",
    ]


def test_split_items_tab_indent_orphan_conclusion_dropped():
    """TAB-mode: 結論 line with no preceding item is silently dropped."""
    section_body = "\t結論：orphan\n\t冷氣維修\n\t結論：請廠商來估\n"
    items = _split_items(section_body)
    assert items == ["冷氣維修\n結論：請廠商來估"]


def test_split_items_tab_indent_blank_line_inside_item_with_pending_conclusion():
    """Blank line flushes any pending item. After a 結論 already attached,
    a blank-flush is no-op for the conclusion (it's already bound)."""
    section_body = "\t冷氣維修\n\t結論：請廠商來估\n\n\t外牆修補\n"
    items = _split_items(section_body)
    assert items == ["冷氣維修\n結論：請廠商來估", "外牆修補"]


def test_split_items_preamble_lines_reset_sub_cat_and_flush():
    """v5: column-0 non-bullet lines inside section are visual breaks. They
    flush the current item AND reset current_sub. Item after a preamble
    starts fresh with no sub_cat encoding.

    `more noise` between 冷氣維修 and 結論 acts as a preamble break, so 冷氣
    flushes WITHOUT its expected 結論 (the 結論 line that follows attaches
    to nothing → orphan → dropped silently).
    """
    section_body = "preamble noise\n\t冷氣維修\nmore noise\n\t結論：請廠商來估\n"
    items = _split_items(section_body)
    assert items == ["冷氣維修"]


def test_summarize_meeting_tab_indent_end_to_end():
    """End-to-end: TAB-style input produces the full Format C summary."""
    out = summarize_meeting(title="2026-05-11 會議", content=TAB_INPUT_BOTH_SECTIONS)
    assert "🏪 店務" in out
    assert "1. 冷氣維修" in out
    assert "   → 請廠商來估" in out
    assert "2. 外牆修補" in out
    assert "   → 8月發包" in out
    assert "👥 人員" in out
    # PERSONNEL_ORDER = [離職, 資遣, 調整, 其他] — 離職 bucket renders before 調整
    assert "### 離職" in out
    assert "### 調整" in out
    li_idx = out.index("### 離職")
    tj_idx = out.index("### 調整")
    assert li_idx < tj_idx, "離職 bucket must render before 調整 per PERSONNEL_ORDER"
    # 阿明's conclusion text contains "調班" → classified to 調整
    assert "1. 阿明" in out
    assert "   → 調班" in out
    # 小華's conclusion text contains "離職" → classified to 離職
    assert "1. 小華" in out
    assert "   → 離職、8月底交接" in out


# ── Sub-category + space-indent + multi-line conclusion (2026-05-11 v2) ──────

# User's actual writing style sample (transcribed from screenshot 2026-05-11):
# - SPACE indent (4 / 9 / 14 spaces), NOT tabs
# - Outer-most indent (4 spaces here) = sub-category header ending with `:`
# - Deeper indent = items + 結論 lines under that sub-category
# - Some items themselves end with `:` (e.g. "點餐系統:") but are NOT
#   sub-category headers because they're at the inner indent level
# - 結論: with empty inline + a yet-deeper indented line = multi-line conclusion
SPACE_INDENT_SAMPLE = """店務:
    代理商:
         K單機的勇闖金字塔, 重開機錶底會不見問題,
         結論:換成新遊戲驗證, 爆爆礦坑
         點餐系統:
         結論:J/JJ/Q/S/KDF先裝
         延伸議題:
         是否需要收集客人點餐的資訊, 提供更好的服務
         結論:
              可以, 但是需要有好的流程, 讓員工點餐時可以直接快速紀錄是誰點
         S監視系統有漏畫面
          結論: 代理商先看回放, 確認問題

         Q賓百系統, 是否可以優化, 一鍵全部修改, 而不是每一台去處理
         結論: 會請山上確認更新修改
"""


def test_split_items_space_indent_with_sub_category():
    """Space-indent input: 代理商 = sub-category, items under it carry the
    `[代理商]` encoding prefix."""
    body = "    代理商:\n         K單機\n         結論:換成新\n"
    items = _split_items(body)
    assert items == ["[代理商] K單機\n結論：換成新"]


def test_split_items_space_indent_item_ending_with_colon_is_item_not_subcat():
    """At inner indent level, an item ending with `:` stays an item — only
    lines at the minimum indent depth can become sub-category headers."""
    body = "    代理商:\n         點餐系統:\n         結論:J/JJ先裝\n"
    items = _split_items(body)
    # "點餐系統:" is at indent 9, sub-cat is at indent 4. So "點餐系統:" stays
    # as a regular item with the trailing `:` kept; the renderer strips it.
    assert items == ["[代理商] 點餐系統:\n結論：J/JJ先裝"]


def test_split_items_empty_conclusion_takes_deeper_indented_continuation():
    """`結論:` alone followed by a deeper-indented line uses that line as the
    conclusion content."""
    body = "    代理商:\n         是否要做X\n         結論:\n              可以, 但要評估\n"
    items = _split_items(body)
    assert items == ["[代理商] 是否要做X\n結論：可以, 但要評估"]


def test_split_items_no_sub_category_no_prefix():
    """When no `xxx:` line at min_indent exists, items have no prefix."""
    body = "    冷氣維修\n    結論:請廠商\n    外牆修補\n    結論:8月發包\n"
    items = _split_items(body)
    assert items == [
        "冷氣維修\n結論：請廠商",
        "外牆修補\n結論：8月發包",
    ]


def test_split_items_mixed_tab_and_space_indent_both_accepted():
    """TAB and space both count as indent. v5 rule: adjacent indented lines
    with no blank/結論 between them = one multi-line item. The original
    TAB-only spec test expected 2 items, but per user habit
    2026-05-11 ("空一行 = 不同議題"), they merge unless blank-separated."""
    body = "\t冷氣\n    外牆\n"  # one TAB-indented + one space-indented, no blank
    items = _split_items(body)
    assert items == ["冷氣\n外牆"]


def test_split_items_two_items_separated_by_blank_line():
    """Explicit blank separates 2 items even when both are indented."""
    body = "\t冷氣\n\n    外牆\n"
    items = _split_items(body)
    assert items == ["冷氣", "外牆"]


def test_summarize_meeting_extension_keeps_extension_inline_under_topic():
    """v7 (2026-05-11): `延伸議題:` after an item's 結論 keeps the extension
    under the SAME item — extension issue + conclusion render inline on
    continuation lines, not as a separate numbered item."""
    text = """店務:
    代理商:
         點餐系統:
         結論:J/JJ/Q/S/KDF先裝
         延伸議題:
         是否需要收集客人點餐的資訊, 提供更好的服務
         結論:
              可以, 但是需要有好的流程, 讓員工點餐時可以直接快速紀錄是誰點
         S監視系統有漏畫面
         結論: 代理商先看回放
"""
    out = summarize_meeting(title="x", content=text)
    # 代理商 promoted to top-level section
    assert "🤝 代理商" in out
    # Item 1 (= 點餐系統) gets both the primary conclusion AND the extension
    # block, then item 2 (= S監視系統) is the NEXT distinct topic.
    assert "1. 點餐系統" in out
    assert "   → J/JJ/Q/S/KDF先裝" in out
    assert "   延伸議題：是否需要收集客人點餐的資訊, 提供更好的服務" in out
    assert "   → 可以, 但是需要有好的流程, 讓員工點餐時可以直接快速紀錄是誰點" in out
    assert "2. S監視系統有漏畫面" in out
    assert "   → 代理商先看回放" in out


def test_split_items_extension_encoded_with_ext_delim():
    """_split_items returns an item string with `_EXT_DELIM` separating
    primary half from extension half — verifies encoding directly."""
    from mynotes.ai_local import _EXT_DELIM
    # All lines at same indent (no sub-cat triggered) — the previous version
    # had 點餐系統: at shallower indent which caused it to be detected as
    # sub-cat (because there was deeper content under it). Keep all 5 lines
    # at indent 9 to test extension encoding without sub-cat noise.
    body = (
        "         點餐系統:\n         結論:J/JJ\n"
        "         延伸議題:\n         是否要X\n         結論:可以\n"
    )
    items = _split_items(body)
    assert len(items) == 1, f"expected 1 item, got {items!r}"
    assert _EXT_DELIM in items[0], f"missing _EXT_DELIM in {items[0]!r}"
    primary, ext = items[0].split(_EXT_DELIM, 1)
    # Primary: trailing `:` from "點餐系統:" stays (it's mid-string in the
    # joined primary text). _extract_conclusion trims it during render.
    assert "點餐系統" in primary
    assert "結論：J/JJ" in primary
    # Extension half: ext_issue + 結論
    assert "是否要X" in ext
    assert "結論：可以" in ext


def test_summarize_meeting_user_sample_promotes_dailishang_to_top_section():
    """v6 (2026-05-11): 代理商 promoted to a top-level section header,
    not a sub-cat under 店務. SPACE_INDENT_SAMPLE has 店務: at line 1,
    then "    代理商:" at line 2 — that "    代理商:" line now matches
    SECTION_HEADER_RE (regex allows leading whitespace) and starts a new
    section. 店務 contributes no items (body before 代理商 is empty);
    代理商 owns all the items.
    """
    out = summarize_meeting(title="2026-05-11 會議", content=SPACE_INDENT_SAMPLE)
    # 代理商 is now a top-level section, with its own icon
    assert "🤝 代理商" in out
    # 店務 had only the 代理商 header in its body → no items → no heading
    assert "🏪 店務" not in out
    # Items previously under "### 代理商" are now numbered directly under 🤝 代理商
    assert "1. K單機的勇闖金字塔, 重開機錶底會不見問題" in out
    assert "   → 換成新遊戲驗證, 爆爆礦坑" in out
    # "點餐系統:" still an item (not sub-cat — only min_indent counts)
    assert "點餐系統" in out
    assert "   → J/JJ/Q/S/KDF先裝" in out
    # multi-line conclusion captured
    assert "   → 可以, 但是需要有好的流程, 讓員工點餐時可以直接快速紀錄是誰點" in out
    # S監視系統, Q賓百系統 still in output (under 代理商 section now)
    assert "S監視系統有漏畫面" in out
    assert "Q賓百系統" in out


def test_summarize_meeting_personnel_with_sub_category_overrides_buckets():
    """If 人員 section has user-defined sub-categories, render those instead
    of the keyword-based 4 buckets."""
    text = """人員:
    新人:
         阿明
         結論: 試用期通過
    資深:
         小華
         結論: 加薪 5%
"""
    out = summarize_meeting(title="x", content=text)
    assert "👥 人員" in out
    assert "### 新人" in out
    assert "### 資深" in out
    # No 4-bucket headers
    assert "### 離職" not in out
    assert "### 調整" not in out
    assert "1. 阿明" in out
    assert "   → 試用期通過" in out
    assert "1. 小華" in out
    assert "   → 加薪 5%" in out


def test_summarize_meeting_personnel_without_sub_category_still_buckets():
    """Backwards-compat: 人員 with no sub-category still uses 4-bucket
    keyword classification (existing behavior preserved)."""
    text = """人員:
    阿明
    結論: 調班
    小華
    結論: 離職
"""
    out = summarize_meeting(title="x", content=text)
    assert "👥 人員" in out
    assert "### 離職" in out
    assert "### 調整" in out
    li = out.index("### 離職")
    tj = out.index("### 調整")
    assert li < tj, "離職 must render before 調整 per PERSONNEL_ORDER"


# ── Empty/dangling sub-cat heading (2026-05-27) ──────────────────────────────


def test_summarize_meeting_empty_subcat_at_end_does_not_leak_to_prev():
    """User writes `其他：` heading with no items beneath. Must NOT be rendered
    as a continuation/item under the previous sub-cat (### 調整)."""
    text = """人員:
    調整:
         員工A調班
         結論: 已生效
    其他:
"""
    out = summarize_meeting(title="x", content=text)
    assert "### 調整" in out
    assert "1. 員工A調班" in out
    assert "   → 已生效" in out
    # The dangling `其他:` must not appear anywhere — not as a continuation
    # under 員工A調班, not as a numbered item, and not as a heading either.
    assert "其他" not in out, f"empty 其他 heading must not leak: {out!r}"


def test_summarize_meeting_empty_subcat_before_blank_then_next_section():
    """`其他：` heading followed by blank then a different top-level section
    (`店務:`) — `其他:` is dangling at end of personnel and must drop cleanly."""
    text = """人員:
    調整:
         員工B升職
    其他:

店務:
    宣傳:
         週末活動
"""
    out = summarize_meeting(title="x", content=text)
    assert "### 調整" in out
    assert "1. 員工B升職" in out
    assert "### 宣傳" in out
    assert "1. 週末活動" in out
    # 其他 must NOT appear inside the personnel section output
    personnel_idx = out.index("👥 人員")
    storeops_idx = out.index("🏪 店務")
    personnel_block = out[personnel_idx:storeops_idx]
    assert "其他" not in personnel_block, (
        f"empty 其他 must not appear in personnel block: {personnel_block!r}"
    )


def test_summarize_meeting_multiple_empty_subcats_at_end():
    """Defensive: user writes two empty trailing sub-cats. Neither should
    leak into the previous filled sub-cat."""
    text = """人員:
    調整:
         員工C調班
    離職:
    其他:
"""
    out = summarize_meeting(title="x", content=text)
    assert "### 調整" in out
    assert "1. 員工C調班" in out
    # The trailing empty 離職 / 其他 headings must not appear as items either.
    # (They CAN appear as 4-bucket headings if items got classified into them
    # — but here there are no such items, so they shouldn't appear at all.)
    assert "其他" not in out
    # 離職 keyword: trickier because user-written 員工C調班 may NOT contain
    # 離職 keyword. We only need to assert it's not bleeding as a continuation
    # under 員工C — check the line right after 員工C is the conclusion line or
    # next sub-cat heading, not a `   離職：` continuation.
    調整_line_idx = out.index("1. 員工C調班")
    後續 = out[調整_line_idx:]
    # No continuation line that looks like "   離職：" or "   其他："
    assert "\n   離職" not in 後續, f"離職 must not leak as continuation: {後續!r}"
    assert "\n   其他" not in 後續, f"其他 must not leak as continuation: {後續!r}"


# ── Duplicate section headers merge (2026-05-11 v3) ──────────────────────────


def test_summarize_meeting_duplicate_storeops_sections_merge_in_order():
    """User habit: write 店務 / 人員 in multiple chunks scattered through the
    note. Each chunk's items should accumulate; sub_cat scope is local to each
    chunk so the second 店務's sub_cat doesn't inherit the first's.

    Uses 宣傳 / 促銷 as sub-cats (not 代理商, which is now its own section).
    """
    text = """店務:
    宣傳:
         週末活動
         結論:加倍折扣

人員:
    阿明
    結論:調班

店務:
    促銷:
         月底特賣
         結論:DM印好

人員:
    小華
    結論:離職
"""
    out = summarize_meeting(title="x", content=text)

    # Both 店務 sections' sub-cats appear; document order respected
    assert "### 宣傳" in out
    assert "### 促銷" in out
    assert out.index("### 宣傳") < out.index("### 促銷"), (
        "first 店務 block's sub-cat must render before second 店務 block's"
    )

    # Items from both 店務 blocks present
    assert "1. 週末活動" in out
    assert "   → 加倍折扣" in out
    assert "1. 月底特賣" in out
    assert "   → DM印好" in out

    # Both 人員 blocks merged. 阿明's conclusion=調班 → 調整 bucket;
    # 小華's conclusion=離職 → 離職 bucket. Both buckets emit headings.
    assert "👥 人員" in out
    assert "### 離職" in out
    assert "### 調整" in out
    assert "1. 阿明" in out
    assert "   → 調班" in out
    assert "1. 小華" in out
    assert "   → 離職" in out


def test_summarize_meeting_duplicate_sections_sub_cat_scope_does_not_leak():
    """Sub-category from one 店務 block must NOT bleed into the next 店務
    block's items (each body parsed independently).

    Uses 宣傳 as sub-cat (not 代理商, which is now its own section).
    """
    text = """店務:
    宣傳:
         週末活動
         結論:加倍折扣

店務:
    yyy without sub-cat
    結論:no sub-cat for me
"""
    out = summarize_meeting(title="x", content=text)
    # First block: 週末活動 under 宣傳
    assert "### 宣傳" in out
    assert "1. 週末活動" in out

    # Second block: yyy should NOT carry "[宣傳]" encoding — it's its own
    # block with no sub_cat. Renderer sees: ... 宣傳 items ..., then yyy with
    # current_sub=None. current_sub changes from "宣傳" → None, no new
    # heading, counter resets.
    assert "yyy without sub-cat" in out
    assert "   → no sub-cat for me" in out
    # No spurious "宣傳" tag inside yyy item text
    assert "[宣傳] yyy" not in out


# ── 補充 marker (parallel to 延伸議題, 2026-05-14) ─────────────────────

def test_summarize_meeting_supplement_keeps_block_inline_under_topic():
    """`補充:` 跟 延伸議題 同一條路徑：保持當前 item 不 flush、後續內容
    + 結論 進 supplement 半邊、渲染成 `補充：xxx` 縮排在同 item 下。"""
    text = """店務:
    代理商:
         點餐系統:
         結論:J/JJ/Q/S/KDF先裝
         補充:
         山上提供了一份報表的範例
         結論:會比照優化
"""
    out = summarize_meeting(title="x", content=text)
    assert "1. 點餐系統" in out
    assert "   → J/JJ/Q/S/KDF先裝" in out
    assert "   補充：山上提供了一份報表的範例" in out
    assert "   → 會比照優化" in out
    # 補充 不該變成獨立的 item「2. xxx」
    assert "2. 山上" not in out


def test_split_items_supplement_encoded_with_sup_delim():
    """`補充:` 用獨立的 _SUP_DELIM 分隔、跟 _EXT_DELIM 不互搶（兩種 marker
    走平行 path、不會誤判）。"""
    from mynotes.ai_local import _SUP_DELIM, _EXT_DELIM
    body = (
        "         點餐系統:\n         結論:J/JJ\n"
        "         補充:\n         山上補資料\n         結論:OK\n"
    )
    items = _split_items(body)
    assert len(items) == 1
    assert _SUP_DELIM in items[0]
    assert _EXT_DELIM not in items[0]
    primary, ext = items[0].split(_SUP_DELIM, 1)
    assert "點餐系統" in primary
    assert "結論：J/JJ" in primary
    assert "山上補資料" in ext
    assert "結論：OK" in ext


def test_summarize_meeting_supplement_without_conclusion():
    """補充半邊沒結論 → 只出現 `補充：xxx` 一行、不加箭頭結論。"""
    text = """店務:
    代理商:
         議題A
         結論:先暫緩
         補充:
         對方還想加碼
"""
    out = summarize_meeting(title="x", content=text)
    assert "1. 議題A" in out
    assert "   → 先暫緩" in out
    assert "   補充：對方還想加碼" in out
    # 沒結論就不該再產生第二個 `→`
    arrow_count = out.count("→")
    assert arrow_count == 1, f"expected 1 → (primary only), got {arrow_count}"


def test_summarize_meeting_extension_and_supplement_dont_collide():
    """延伸議題 跟 補充 在不同 item 各自獨立、不會互相覆蓋。"""
    text = """店務:
    代理商:
         議題A
         結論:Ya
         延伸議題:
         延伸a
         結論:Za

         議題B
         結論:Yb
         補充:
         補充b
         結論:Zb
"""
    out = summarize_meeting(title="x", content=text)
    # 議題A: 延伸議題, 議題B: 補充
    assert "1. 議題A" in out
    assert "   延伸議題：延伸a" in out
    assert "   → Za" in out
    assert "2. 議題B" in out
    assert "   補充：補充b" in out
    assert "   → Zb" in out
