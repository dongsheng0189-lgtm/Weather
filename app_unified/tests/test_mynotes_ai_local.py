"""mynotes meeting note local parser — unit tests for pure functions."""
import pytest

from mynotes.ai_local import (
    _classify_personnel,
    _extract_conclusion,
    _split_items,
    _split_sections,
    summarize_meeting,
)


def test_split_sections_both_present():
    text = """
店務:
1. 外牆修補
2. 客訴

人員:
1. 小李離職
""".strip()
    result = _split_sections(text)
    assert set(result.keys()) == {"店務", "人員"}
    assert "外牆修補" in result["店務"]
    assert "客訴" in result["店務"]
    assert "小李離職" in result["人員"]


def test_split_sections_only_one():
    text = """
店務:
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "店務" in result
    assert "人員" not in result


def test_split_sections_neither():
    text = "今天會議聊了很多事情。沒有結構化的標頭。"
    result = _split_sections(text)
    assert result == {}


def test_split_sections_full_width_colon():
    text = """
店務：
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "店務" in result


def test_split_sections_brackets():
    text = """
【店務】
1. 外牆修補

[人員]
1. 小李離職
""".strip()
    result = _split_sections(text)
    assert "店務" in result
    assert "人員" in result


def test_split_sections_bare_word():
    text = """
店務
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "店務" in result


def test_split_sections_preamble_dropped():
    """Header 之前的內容不出現在任何 section body 裡。"""
    text = """
日期：2026/05/07
出席：店長、副店

店務:
1. 外牆修補
""".strip()
    result = _split_sections(text)
    assert "日期" not in result.get("店務", "")
    assert "出席" not in result.get("店務", "")


def test_split_sections_duplicate_header_last_wins():
    """重複 section header → 後出現的覆蓋前面（記錄當前 contract，避免 regression）。"""
    text = """
店務:
1. 第一段店務

店務:
2. 第二段店務
""".strip()
    result = _split_sections(text)
    assert "店務" in result
    assert "第二段店務" in result["店務"]
    assert "第一段店務" not in result["店務"]


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

    expected = """# 會議摘要

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


def test_summarize_meeting_empty_content():
    result = summarize_meeting(title="x", content="")
    assert "（無內文）" in result
