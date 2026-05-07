# mynotes Meeting Summary Local Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `mynotes` 會議筆記的 AI 摘要從雲端 Gemini 換成 local Python regex parser；同時修 `get_ai_task` race bug 與編輯器 Tab 鍵行為。

**Architecture:** 新檔 `mynotes/ai_local.py` 提供 4 個 pure parser 函式 + 1 個 `summarize_meeting` 主入口；`mynotes/routes.py` 拆掉 LLM prompt，改透過 `gevent.get_hub().threadpool.apply()` 派 parser 到 real OS thread（與 `notes` 5/7 防 worker recycle 同模式）；`templates/mynotes/editor.html` 攔 content textarea 的 Tab 鍵插 4 空白。

**Tech Stack:** Python 3.12, Flask, SQLAlchemy (Flask-SQLAlchemy), gevent (threadpool), pytest, vanilla JS。

**Spec reference:** `docs/superpowers/specs/2026-05-07-mynotes-meeting-summary-local-design.md`

---

## File Structure

| 檔案 | 動作 | 責任 |
|------|------|------|
| `app_unified/mynotes/ai_local.py` | **Create** | Single-note regex parser：4 個 pure helper + `summarize_meeting` 主入口。完全不依賴 Flask / DB。 |
| `app_unified/mynotes/routes.py` | **Modify** | 改 `summarize` / `_run_summary` / `get_ai_task` 三個 function。拆掉 `call_llm` import 與 prompt 字串。 |
| `app_unified/templates/mynotes/editor.html` | **Modify** | content textarea 加 Tab keydown listener。 |
| `app_unified/tests/test_mynotes_ai_local.py` | **Create** | 純 parser unit tests（不需 Flask app fixture）。 |
| `app_unified/tests/test_mynotes_summarize_route.py` | **Create** | route + threadpool dispatch + DB write 整合測試。 |
| `app_unified/tests/test_mynotes_ai_task_race.py` | **Create** | grace window 行為測試（仿 `tests/test_ai_task_race.py`）。 |

---

## Task 0: Worktree setup

**Files:** N/A (環境準備)

- [ ] **Step 1: Open isolated worktree**

跟 `feedback_branch_workflow` 一致：

```bash
cd /home/hirain0126/projects/webapp
git -C app_unified worktree add /home/hirain0126/projects/webapp/.worktrees/feat-mynotes-summary-local -b feat/mynotes-summary-local main
cd .worktrees/feat-mynotes-summary-local
pwd  # confirm
```

預期輸出：`/home/hirain0126/projects/webapp/.worktrees/feat-mynotes-summary-local`

之後所有檔案編輯、test 執行都在這個目錄裡。

---

## Task 1: `_split_sections` — 切「店務」「人員」section

**Files:**
- Create: `app_unified/mynotes/ai_local.py`
- Create: `app_unified/tests/test_mynotes_ai_local.py`

- [ ] **Step 1: Write failing tests**

Create `app_unified/tests/test_mynotes_ai_local.py`:

```python
"""mynotes meeting note local parser — unit tests for pure functions."""
import pytest

from mynotes.ai_local import _split_sections


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: All 7 tests FAIL with `ModuleNotFoundError: No module named 'mynotes.ai_local'` (or `ImportError`).

- [ ] **Step 3: Create `mynotes/ai_local.py` with `_split_sections`**

Create `app_unified/mynotes/ai_local.py`:

```python
"""mynotes meeting note local parser — single-note pipeline.

從會議紀錄抽取「店務」「人員」section，每 section 內條列項目，
人員 section 自動依關鍵字分 4 小類（離職/資遣/調整/其他）。
不依賴雲端 LLM、不依賴 sentence-transformers / sklearn。
"""
import re
from typing import Optional


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app_unified/mynotes/ai_local.py app_unified/tests/test_mynotes_ai_local.py
git commit -m "feat(mynotes/ai-local): _split_sections regex parser

Detects 店務 and 人員 section headers, supporting :／：／【】／[] variants
and bare-word headers. Section body = text from header end to next header
start (or EOF). Content before any header is dropped (preamble).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `_split_items` — 切 section 內的條列項目

**Files:**
- Modify: `app_unified/mynotes/ai_local.py`
- Modify: `app_unified/tests/test_mynotes_ai_local.py`

- [ ] **Step 1: Append failing tests**

Append to `app_unified/tests/test_mynotes_ai_local.py`:

```python
from mynotes.ai_local import _split_items


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


def test_split_items_empty():
    assert _split_items("") == []
    assert _split_items("   \n  ") == []


def test_split_items_no_bullets():
    """沒 bullet 的內容回空 list（避免誤把整段當一個項目）。"""
    section = "今天討論很多事情但沒列點"
    assert _split_items(section) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: 7 new tests FAIL (`ImportError: cannot import name '_split_items'`).

- [ ] **Step 3: Add `ITEM_START_RE` and `_split_items` to `mynotes/ai_local.py`**

Insert after `SECTION_HEADER_RE` definition:

```python
# 偵測項目起始：
#   數字+. / 數字+、 / 數字+) / 數字+）
#   (1) / （1）
#   bullet: ●, •, ・, ▪, ◆, ◇, ★, ☆, -, *
ITEM_START_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"\d+[.、)）]"
    r"|[(（]\d+[)）]"
    r"|[●•・▪◆◇★☆\-\*]"
    r")"
    r"\s+",
    re.MULTILINE,
)
```

Insert after `_split_sections` function:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: 14 passed (7 from Task 1 + 7 new).

- [ ] **Step 5: Commit**

```bash
git add app_unified/mynotes/ai_local.py app_unified/tests/test_mynotes_ai_local.py
git commit -m "feat(mynotes/ai-local): _split_items handles arabic/chinese/bullet

Recognizes 1./1、/1)/1）/(1)/（1） numbered items and bullet markers
(●•・▪◆◇★☆-*). Multi-line items collected until next bullet appears.
Section bodies without any bullet pattern return empty list (avoid
mis-treating whole prose as one item).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `_extract_conclusion` — 從項目抽結論

**Files:**
- Modify: `app_unified/mynotes/ai_local.py`
- Modify: `app_unified/tests/test_mynotes_ai_local.py`

- [ ] **Step 1: Append failing tests**

Append to `app_unified/tests/test_mynotes_ai_local.py`:

```python
from mynotes.ai_local import _extract_conclusion


def test_extract_conclusion_simple():
    issue, conclusion = _extract_conclusion("外牆修補\n結論：請廠商評估")
    assert issue == "外牆修補"
    assert conclusion == "請廠商評估"


def test_extract_conclusion_full_width_colon():
    issue, conclusion = _extract_conclusion("外牆修補\n結論：請廠商評估")
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
    issue, conclusion = _extract_conclusion("外牆修補\n結論：")
    assert conclusion is None


def test_extract_conclusion_multiple_takes_last():
    """同一項目多個結論詞 → 取最後一個（議論後寫的最定論）。"""
    text = "客訴\n結論：先退費\n後來重新討論\n決議：本週六開檢討會"
    issue, conclusion = _extract_conclusion(text)
    assert conclusion == "本週六開檢討會"


def test_extract_conclusion_keyword_inside_issue_text():
    """議題本身包含『客訴：』這種非關鍵字冒號 → 不應誤判為結論。"""
    text = "客訴：上週六飲料品質有問題, 已退費道歉\n決定：本週六開檢討會"
    issue, conclusion = _extract_conclusion(text)
    assert "客訴：上週六飲料品質有問題" in issue
    assert "已退費道歉" in issue
    assert conclusion == "本週六開檢討會"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: 9 new tests FAIL (`ImportError: cannot import name '_extract_conclusion'`).

- [ ] **Step 3: Add `CONCLUSION_RE` and `_extract_conclusion` to `mynotes/ai_local.py`**

Insert after `ITEM_START_RE`:

```python
# 偵測「結論／決定／決議」+ 冒號 + 內容（直到行尾）
CONCLUSION_RE = re.compile(r"(?:結論|決定|決議)\s*[:：]\s*(.+?)(?=\n|$)")
```

Insert after `_split_items` function:

```python
def _extract_conclusion(item_text: str) -> tuple[str, Optional[str]]:
    """抽結論。

    回傳 (議題文字, 結論文字 or None)。多個結論詞取最後一個（會議紀錄
    通常先討論再寫結論，最後出現的最定論）。空字串結論視為無結論。
    """
    matches = list(CONCLUSION_RE.finditer(item_text))
    if not matches:
        return item_text.strip(), None
    last = matches[-1]
    conclusion = last.group(1).strip()
    if not conclusion:
        return item_text.strip(), None
    # 議題 = 最後一個結論詞之前的全部，去除尾端標點/換行
    issue = item_text[: last.start()].rstrip(" ：:、，,。\n\r\t").strip()
    return issue, conclusion
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: 23 passed (14 prior + 9 new).

- [ ] **Step 5: Commit**

```bash
git add app_unified/mynotes/ai_local.py app_unified/tests/test_mynotes_ai_local.py
git commit -m "feat(mynotes/ai-local): _extract_conclusion 結論/決定/決議

Returns (issue_text, conclusion_text_or_None). Half- and full-width
colons both supported. Multiple keyword matches take the last one
(meeting notes typically discuss then conclude). Empty conclusion
string treated as None. Issue text trimmed of trailing punctuation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `_classify_personnel` — 人員小類分桶

**Files:**
- Modify: `app_unified/mynotes/ai_local.py`
- Modify: `app_unified/tests/test_mynotes_ai_local.py`

- [ ] **Step 1: Append failing tests**

Append to `app_unified/tests/test_mynotes_ai_local.py`:

```python
from mynotes.ai_local import _classify_personnel


def test_classify_personnel_lizhi():
    assert _classify_personnel("小李提離職, 5/15 生效") == "離職"
    assert _classify_personnel("王副店請辭") == "離職"
    assert _classify_personnel("阿美辭職") == "離職"
    assert _classify_personnel("自請離開") == "離職"


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


def test_classify_personnel_does_not_match_bare_shang_xia():
    """『上週討論』『下個月』不應誤判為調整。"""
    assert _classify_personnel("上週討論的客訴") == "其他"
    assert _classify_personnel("下個月排班檢討") == "其他"
    assert _classify_personnel("上班時間調整") == "調整"  # 「調整」單字仍命中
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: 6 new tests FAIL.

- [ ] **Step 3: Add `PERSONNEL_KEYWORDS`, `PERSONNEL_ORDER`, `_classify_personnel` to `mynotes/ai_local.py`**

Insert after `CONCLUSION_RE`:

```python
# 人員小類關鍵字。順序：離職 → 資遣 → 調整 → 其他（其他在 _classify_personnel 內當 fallback）。
# 「調整」不含單字「上」「下」（會誤判「上週」「下個月」），改用 2 字組合。
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
```

Insert after `_extract_conclusion`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: 29 passed.

- [ ] **Step 5: Commit**

```bash
git add app_unified/mynotes/ai_local.py app_unified/tests/test_mynotes_ai_local.py
git commit -m "feat(mynotes/ai-local): _classify_personnel keyword bucket

Returns 離職/資遣/調整/其他 by keyword match in fixed priority order.
調整 keyword list intentionally excludes bare 上/下 single chars (would
mis-fire on 上週/下個月) and uses 2-char compounds (升上, 上去, 下來,
降下, 調下 etc.) to capture promotion/demotion phrasing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `summarize_meeting` — 主入口、E2E 組裝

**Files:**
- Modify: `app_unified/mynotes/ai_local.py`
- Modify: `app_unified/tests/test_mynotes_ai_local.py`

- [ ] **Step 1: Append failing tests**

Append to `app_unified/tests/test_mynotes_ai_local.py`:

```python
from mynotes.ai_local import summarize_meeting


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: 7 new tests FAIL.

- [ ] **Step 3: Add `summarize_meeting` to `mynotes/ai_local.py`**

Append to `mynotes/ai_local.py`:

```python
def summarize_meeting(title: str, content: str) -> str:
    """主入口：title + content → markdown 摘要字串。

    輸出 Format C：議題一行、結論縮排一行（→ 前綴）；沒結論的項目只列議題。
    人員 section 自動分 4 小類（離職/資遣/調整/其他），空小類不輸出。
    完全找不到 section header 時回 ⚠️ 提示文字。
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
                    lines.append(f"   → {conclusion}")
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
                            lines.append(f"   → {conclusion}")
                    lines.append("")

    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v`

Expected: 36 passed.

- [ ] **Step 5: Commit**

```bash
git add app_unified/mynotes/ai_local.py app_unified/tests/test_mynotes_ai_local.py
git commit -m "feat(mynotes/ai-local): summarize_meeting end-to-end pipeline

Composes _split_sections + _split_items + _extract_conclusion +
_classify_personnel into the final markdown summary. Format C: 議題
一行、結論縮排一行 with → prefix; items without explicit conclusion
keyword render as the issue line only (no '（無結論）' tag, no 摘要
開始/結束 markers per spec).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `mynotes/routes.py` — 切掉 LLM、套 threadpool

**Files:**
- Modify: `app_unified/mynotes/routes.py` (lines ~427-505)
- Create: `app_unified/tests/test_mynotes_summarize_route.py`

- [ ] **Step 1: Write failing tests**

Create `app_unified/tests/test_mynotes_summarize_route.py`:

```python
"""mynotes/api/<id>/summarize route + threadpool dispatch + DB write."""
import uuid
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from mynotes import routes as mn_routes
from models import User, PrivateNote
from extensions import db


@pytest.fixture
def _user(app, db_session):
    suffix = uuid.uuid4().hex[:6]
    u = User(username=f"u-{suffix}")
    u.set_password("x")
    # 必須有 mynotes feature 才能進 blueprint
    u.set_features(["mynotes"])
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def _meeting_note(app, db_session, _user):
    n = PrivateNote(
        user_id=_user.id,
        title="5/7 月會",
        content="店務:\n1. 外牆修補\n   結論：請廠商評估",
        note_type="meeting",
    )
    db_session.add(n)
    db_session.commit()
    return n


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def test_summarize_route_dispatches_via_threadpool(app, client, _user, _meeting_note):
    """Spec 守則：summarize_meeting 必須走 threadpool.apply。"""
    _login_as(client, _user)
    captured = {"calls": []}

    def fake_apply(fn, args=(), kwds=None):
        captured["calls"].append((fn, args, kwds))
        return fn(*args, **(kwds or {}))

    fake_summarize = MagicMock(return_value="FAKE_SUMMARY")
    fake_summarize.__name__ = "summarize_meeting"

    with patch("mynotes.ai_local.summarize_meeting", fake_summarize), \
         patch("gevent.get_hub") as mock_gh:
        mock_pool = MagicMock(apply=MagicMock(side_effect=fake_apply))
        mock_gh.return_value.threadpool = mock_pool

        r = client.post(f"/mynotes/api/{_meeting_note.id}/summarize")
        assert r.status_code == 200
        task_id = r.get_json()["task_id"]

        # _run_summary 是同步 spawn → 透過 mock 立刻完成
        # 等價 sync 完，輪詢 task 應已 done
        r2 = client.get(f"/mynotes/api/ai/task/{task_id}")
        assert r2.status_code == 200
        assert r2.get_json()["status"] == "done"
        assert r2.get_json()["result"] == "FAKE_SUMMARY"

    assert len(captured["calls"]) == 1
    fn, _args, _kwds = captured["calls"][0]
    assert fn.__name__ == "summarize_meeting"


def test_summarize_route_writes_ai_summary_field(app, client, _user, _meeting_note, db_session):
    _login_as(client, _user)

    def fake_apply(fn, args=(), kwds=None):
        return fn(*args, **(kwds or {}))

    fake_summarize = MagicMock(return_value="WRITTEN_SUMMARY")
    fake_summarize.__name__ = "summarize_meeting"

    with patch("mynotes.ai_local.summarize_meeting", fake_summarize), \
         patch("gevent.get_hub") as mock_gh:
        mock_gh.return_value.threadpool = MagicMock(apply=MagicMock(side_effect=fake_apply))
        client.post(f"/mynotes/api/{_meeting_note.id}/summarize")

    db_session.expire_all()
    n = PrivateNote.query.get(_meeting_note.id)
    assert n.ai_summary == "WRITTEN_SUMMARY"


def test_summarize_route_rejects_non_meeting(app, client, _user, db_session):
    _login_as(client, _user)
    n = PrivateNote(
        user_id=_user.id,
        title="待辦",
        content="x",
        note_type="checklist",
    )
    db_session.add(n)
    db_session.commit()

    r = client.post(f"/mynotes/api/{n.id}/summarize")
    assert r.status_code == 400
    assert "僅會議紀錄可摘要" in r.get_json()["message"]


def test_summarize_route_handles_pipeline_exception(app, client, _user, _meeting_note):
    _login_as(client, _user)

    def boom(fn, args=(), kwds=None):
        raise RuntimeError("simulated parser failure")

    with patch("gevent.get_hub") as mock_gh:
        mock_gh.return_value.threadpool = MagicMock(apply=MagicMock(side_effect=boom))
        r = client.post(f"/mynotes/api/{_meeting_note.id}/summarize")
        task_id = r.get_json()["task_id"]
        r2 = client.get(f"/mynotes/api/ai/task/{task_id}")

    assert r2.get_json()["status"] == "error"
    assert "simulated parser failure" in r2.get_json()["message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_summarize_route.py -v`

Expected: 4 tests FAIL — `test_summarize_route_dispatches_via_threadpool` will fail because the current route uses `call_llm` directly (not threadpool); others may also error.

- [ ] **Step 3: Modify `mynotes/routes.py` — replace summarize + _run_summary**

In `app_unified/mynotes/routes.py`, replace the entire AI-summary section (lines ~427–505 in current main, between the `# ═══ AI 摘要（僅 meeting）═══` header and the `get_ai_task` route).

Find this block:

```python
# ═══ AI 摘要（僅 meeting）══════════════════════════════

_ai_tasks = {}


@mynotes_bp.route("/api/<int:note_id>/summarize", methods=["POST"])
@login_required
def summarize(note_id):
    from admin.routes import call_llm
    n = _get_my_note(note_id)
    if n.note_type != "meeting":
        return jsonify({"status": "error", "message": "僅會議紀錄可摘要"}), 400

    prompt = f"""# Role
... (整段 LLM prompt) ...
"""
    task_id = uuid.uuid4().hex[:12]
    _ai_tasks[task_id] = {"status": "processing"}
    import gevent
    gevent.spawn(_run_summary, task_id, current_app._get_current_object(), prompt, n.id)
    return jsonify({"status": "accepted", "task_id": task_id})


def _run_summary(task_id, app, prompt, note_id):
    from admin.routes import call_llm
    with app.app_context():
        try:
            result = call_llm(prompt, max_tokens=8192)
            n = PrivateNote.query.get(note_id)
            if n:
                n.ai_summary = result
                n.updated_at = datetime.utcnow()
                db.session.commit()
            _ai_tasks[task_id] = {"status": "done", "result": result}
        except Exception as e:
            _ai_tasks[task_id] = {"status": "error", "message": str(e)}
```

Replace with:

```python
# ═══ AI 摘要（僅 meeting，local Python pipeline）═══════════
# 從雲端 LLM 換到 local regex parser；同模式參考 notes/routes.py 5/7 7a1465f
# CPU 工作走 gevent.threadpool 避免 block reactor → /healthz / pod 不被殺。

_ai_tasks = {}


@mynotes_bp.route("/api/<int:note_id>/summarize", methods=["POST"])
@login_required
def summarize(note_id):
    n = _get_my_note(note_id)
    if n.note_type != "meeting":
        return jsonify({"status": "error", "message": "僅會議紀錄可摘要"}), 400

    task_id = uuid.uuid4().hex[:12]
    _ai_tasks[task_id] = {"status": "processing"}
    import gevent
    gevent.spawn(_run_summary, task_id, current_app._get_current_object(), n.id)
    return jsonify({"status": "accepted", "task_id": task_id})


def _run_summary(task_id, app, note_id):
    """Local Python parser task — 走 gevent.threadpool 不阻塞 reactor。"""
    with app.app_context():
        try:
            n = PrivateNote.query.get(note_id)
            if not n:
                _ai_tasks[task_id] = {"status": "error", "message": "筆記不存在"}
                return
            from mynotes.ai_local import summarize_meeting
            import gevent
            # threadpool.apply 把 fn 派到 real OS thread；目前 greenlet 等結果，
            # 但會 yield hub 給其他 greenlet（含 /healthz、其他 request）。
            result = gevent.get_hub().threadpool.apply(
                summarize_meeting,
                args=(n.title or "", n.content or ""),
            )
            n.ai_summary = result
            n.updated_at = datetime.utcnow()
            db.session.commit()
            _ai_tasks[task_id] = {"status": "done", "result": result}
        except Exception as e:
            import logging as _logging_mod
            _logging_mod.getLogger("mynotes_ai_summary").exception(
                "[mynotes] local pipeline failed"
            )
            _ai_tasks[task_id] = {"status": "error", "message": str(e)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_summarize_route.py -v`

Expected: 4 passed.

Also run full mynotes test sweep to catch regressions:

`cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py tests/test_mynotes_summarize_route.py -v`

Expected: 40 passed.

- [ ] **Step 5: Commit**

```bash
git add app_unified/mynotes/routes.py app_unified/tests/test_mynotes_summarize_route.py
git commit -m "feat(mynotes/ai-summary): replace LLM with local Python parser

summarize endpoint now runs through summarize_meeting (regex parser)
dispatched via gevent.get_hub().threadpool.apply, mirroring notes 5/7
7a1465f to avoid blocking the gevent reactor and triggering Zeabur
readiness probe failures.

- drops 'from admin.routes import call_llm' and the inline prompt
- _run_summary takes (task_id, app, note_id) only — no prompt arg
- DB write to PrivateNote.ai_summary unchanged (front-end display path
  intact)
- exception path routes through structured logger 'mynotes_ai_summary'

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `mynotes/routes.py::get_ai_task` — race grace window

**Files:**
- Modify: `app_unified/mynotes/routes.py`
- Create: `app_unified/tests/test_mynotes_ai_task_race.py`

- [ ] **Step 1: Write failing tests**

Create `app_unified/tests/test_mynotes_ai_task_race.py`:

```python
"""mynotes get_ai_task race-window fix (parallel to notes 0e9dda7)."""
import time
import uuid
import pytest

from mynotes import routes as mn_routes
from models import User
from extensions import db


@pytest.fixture
def _user(app, db_session):
    suffix = uuid.uuid4().hex[:8]
    u = User(username=f"u-{suffix}")
    u.set_password("x")
    u.set_features(["mynotes"])
    db_session.add(u)
    db_session.commit()
    return u


def _login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)


def _seed_task(task_id, payload):
    mn_routes._ai_tasks[task_id] = payload


def test_done_task_repeatable_within_grace_window(app, client, _user):
    _login_as(client, _user)
    tid = "tg-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "done", "result": "summary text"})

    r1 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r1.status_code == 200
    assert r1.get_json() == {"status": "done", "result": "summary text"}

    r2 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r2.status_code == 200
    assert r2.get_json() == {"status": "done", "result": "summary text"}


def test_error_task_repeatable_within_grace_window(app, client, _user):
    _login_as(client, _user)
    tid = "te-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "error", "message": "boom"})

    r1 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r1.status_code == 200
    r2 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r2.status_code == 200
    assert r2.get_json() == {"status": "error", "message": "boom"}


def test_done_task_swept_after_grace(app, client, _user, monkeypatch):
    _login_as(client, _user)
    tid = "ts-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "done", "result": "x"})

    base = time.time()
    monkeypatch.setattr(mn_routes.time, "time", lambda: base)
    r1 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r1.status_code == 200

    monkeypatch.setattr(mn_routes.time, "time", lambda: base + 31)
    r2 = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r2.status_code == 404
    assert tid not in mn_routes._ai_tasks


def test_processing_task_never_marked_for_deletion(app, client, _user):
    _login_as(client, _user)
    tid = "tp-" + uuid.uuid4().hex[:8]
    _seed_task(tid, {"status": "processing"})

    r = client.get(f"/mynotes/api/ai/task/{tid}")
    assert r.status_code == 200
    assert r.get_json() == {"status": "processing"}
    assert "delete_at" not in mn_routes._ai_tasks[tid]


def test_unknown_task_still_404(app, client, _user):
    _login_as(client, _user)
    r = client.get("/mynotes/api/ai/task/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["status"] == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_task_race.py -v`

Expected: `test_done_task_repeatable_within_grace_window` and `test_error_task_repeatable_within_grace_window` FAIL — current code does `del _ai_tasks[task_id]` on first done/error read.

Also `test_done_task_swept_after_grace` FAIL — needs `mn_routes.time.time` to be importable but current `mynotes/routes.py` doesn't import `time` module yet.

- [ ] **Step 3: Modify `mynotes/routes.py::get_ai_task`**

First add `time` to top-of-file imports if not already there. Find:

```python
import uuid
```

Replace with:

```python
import time
import uuid
```

Then find the existing `get_ai_task`:

```python
@mynotes_bp.route("/api/ai/task/<task_id>", methods=["GET"])
@login_required
def get_ai_task(task_id):
    task = _ai_tasks.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "任務不存在"}), 404
    if task["status"] == "done":
        result = task["result"]
        del _ai_tasks[task_id]
        return jsonify({"status": "done", "result": result})
    if task["status"] == "error":
        msg = task["message"]
        del _ai_tasks[task_id]
        return jsonify({"status": "error", "message": msg})
    return jsonify({"status": "processing"})
```

Replace with:

```python
_AI_TASK_GRACE_SEC = 30


@mynotes_bp.route("/api/ai/task/<task_id>", methods=["GET"])
@login_required
def get_ai_task(task_id):
    """輪詢 AI 任務狀態。

    Done/error 結果保留 30 秒 grace window 後才 GC，避免 frontend setInterval
    重疊 polling 在第一次 done 讀取後立即拿到「任務不存在」（race bug，
    跟 notes/routes.py 5/7 0e9dda7 同模式）。
    """
    now = time.time()
    expired = [tid for tid, t in _ai_tasks.items() if t.get("delete_at", float("inf")) < now]
    for tid in expired:
        _ai_tasks.pop(tid, None)

    task = _ai_tasks.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "任務不存在"}), 404

    # 第一次讀到 done/error 時設定刪除時間；之後 grace_sec 內仍可重讀同結果
    if task["status"] in ("done", "error") and "delete_at" not in task:
        task["delete_at"] = now + _AI_TASK_GRACE_SEC

    if task["status"] == "done":
        return jsonify({"status": "done", "result": task["result"]})
    if task["status"] == "error":
        return jsonify({"status": "error", "message": task["message"]})
    return jsonify({"status": "processing"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_task_race.py -v`

Expected: 5 passed.

Also re-run all mynotes test files to catch regression:

`cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py tests/test_mynotes_summarize_route.py tests/test_mynotes_ai_task_race.py -v`

Expected: 45 passed.

- [ ] **Step 5: Commit**

```bash
git add app_unified/mynotes/routes.py app_unified/tests/test_mynotes_ai_task_race.py
git commit -m "fix(mynotes/ai-task): 30s grace window for done/error polls

Mirror of notes/routes.py 0e9dda7 race fix. Frontend setInterval can
issue overlapping polls; the second one shouldn't get '任務不存在' just
because the first read already del'd the entry. Lazy-GC entries past
grace_sec in every poll.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 編輯器 Tab → 4 空白

**Files:**
- Modify: `app_unified/templates/mynotes/editor.html`

(No automated test — needs jsdom/selenium which the repo doesn't run. Manual verify in Task 9.)

- [ ] **Step 1: Locate the content textarea**

Run: `cd app_unified && grep -n 'name="content"\|id="content"\|<textarea' templates/mynotes/editor.html`

Note the line number of the **content** textarea (the meeting body). Title and checklist textareas / inputs are NOT this one — leave them alone.

- [ ] **Step 2: Add Tab keydown handler**

Find the closing `</script>` of the editor's main script block (or the existing `addEventListener` chain near the bottom of the file). Add this listener block — adapt the selector to whatever `id` / `data-` attribute the content textarea has (most likely `id="content"` or `name="content"`):

```javascript
// Tab 鍵插 4 空白（不跳焦），方便會議紀錄縮排對齊店務/人員小類
(function setupContentTabHandler() {
  const ta = document.querySelector('textarea[name="content"]')
            || document.querySelector('textarea#content');
  if (!ta) return;
  ta.addEventListener('keydown', function (e) {
    if (e.key !== 'Tab' || e.shiftKey) return;
    e.preventDefault();
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const value = ta.value;
    ta.value = value.slice(0, start) + '    ' + value.slice(end);
    ta.selectionStart = ta.selectionEnd = start + 4;
    // 觸發 input 事件讓 auto-save 之類的 listener 收到
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  });
})();
```

Make sure the snippet ends up **inside** a `<script>` tag (use the page's existing one if possible). If the page has a Vue / Alpine binding on the textarea, the IIFE pattern above still works because we manually dispatch `input` after assigning `value`.

- [ ] **Step 3: Sanity-check no other Tab handlers broken**

Run: `cd app_unified && grep -n "key === 'Tab'\|keydown" templates/mynotes/editor.html`

Confirm the new handler is the only Tab handler bound to the content textarea (or coordinates with existing ones — if there's an existing Tab handler we'd see it; current code: none expected).

- [ ] **Step 4: Commit**

```bash
git add app_unified/templates/mynotes/editor.html
git commit -m "feat(mynotes/editor): Tab key inserts 4 spaces for outline alignment

Content textarea only (title / checklist 不變). Captures Tab keydown
(without shift) and inserts 4 half-width spaces at cursor position
instead of jumping focus, so users can outline-align 店務/人員 sub-
categories. Dispatches 'input' event so existing auto-save listeners
still fire.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Deploy + manual verify

**Files:** N/A (使用者操作 + 觀察)

- [ ] **Step 1: Push branch**

```bash
cd /home/hirain0126/projects/webapp/.worktrees/feat-mynotes-summary-local
git push -u origin feat/mynotes-summary-local
```

(Note: per `feedback_push_policy`, **wait for explicit user `push` instruction** — don't push autonomously even though auto mode is on. push is a side-effecting deploy gate.)

- [ ] **Step 2: Deploy decision (re-confirm with user)**

Re-ask user the same A/B decision as for the AI-summary worker-recycle fix:

- A. workflow_dispatch → branch tag → Zeabur 改 image tag → 驗證 → merge main
- B. Direct merge to main → push → GHA build latest → Zeabur Redeploy

User picked B last time. Confirm same again or take A this time.

- [ ] **Step 3: Wait for GHA build & Zeabur redeploy**

After push (or merge+push), the GHA workflow `Build and Push Docker Image` runs ~5-7 min (`.py` change only triggers COPY layer rebuild; chown / model preload cached).

Monitor at: https://github.com/stvlin1988-alt/Weather/actions

Once green-checked, user goes to Zeabur → 服務頁 → 重新部署 button.

- [ ] **Step 4: Verify deployed version in container**

User opens Zeabur 「指令」shell and runs:

```bash
echo $GIT_SHA
grep -n "summarize_meeting" /app/mynotes/ai_local.py
grep -n "_AI_TASK_GRACE_SEC" /app/mynotes/routes.py
```

Expected:
- `$GIT_SHA` = the merged commit's full SHA
- First `grep` shows the `summarize_meeting` definition + import line
- Second `grep` shows `_AI_TASK_GRACE_SEC = 30` and its usage in `get_ai_task`

Also: admin dashboard footer should show first 4 chars of the new SHA (added by 5/7 `7d86996`).

- [ ] **Step 5: User functional testing**

In the running app:

1. Create a new mynotes 會議 note with the spec-canonical sample input (or a real meeting transcript).
2. Press AI 摘要 button.
3. Wait for the summary; verify Format C output:
   - 「# 會議摘要」 header line
   - 🏪 店務 + numbered items, 「→ 結論」 only on items with explicit 結論 keyword
   - 👥 人員 + 4 sub-categories in fixed order, empty buckets dropped
   - **No** 【摘要開始】/【摘要結束】 markers
   - **No** 「（無結論）」 tag anywhere
4. Press AI 摘要 a second time on the same note (or different meeting). Confirm no 「任務不存在」.
5. Repeat 3-5 times to stress the race-window fix.
6. In editor: focus content textarea, press Tab → 4 spaces should insert at cursor; focus should NOT jump out of textarea.

- [ ] **Step 6: Merge fix branch back to main (if test path was branch-based — option A)**

If user picked option A in Step 2:

```bash
cd /home/hirain0126/projects/webapp/app_unified
git checkout main
git merge --ff-only feat/mynotes-summary-local
git push origin main
```

Then ask user before continuing — Zeabur redeploy may auto-trigger again on main push.

- [ ] **Step 7: Cleanup worktree + branches**

After verification passes:

```bash
cd /home/hirain0126/projects/webapp/app_unified
git worktree remove /home/hirain0126/projects/webapp/.worktrees/feat-mynotes-summary-local
git branch -d feat/mynotes-summary-local
git push origin --delete feat/mynotes-summary-local  # only after merge
```

- [ ] **Step 8: Update memory**

Append a new dated section to `/home/hirain0126/.claude/projects/-home-hirain0126/memory/project_webapp.md` recording:

- Commit SHAs deployed
- That mynotes summary path is now local Python (no LLM)
- That mynotes get_ai_task race fix is deployed (paired with notes 0e9dda7)
- What did/didn't work in user verification (for future tuning)

Update the frontmatter `description:` field to surface the new state for next session quick-recall.

---

## Self-Review Checklist Results

**1. Spec coverage:**
- ✅ `_split_sections` — Task 1
- ✅ `_split_items` — Task 2
- ✅ `_extract_conclusion` — Task 3
- ✅ `_classify_personnel` — Task 4
- ✅ `summarize_meeting` E2E — Task 5
- ✅ `summarize` route refactor + threadpool — Task 6
- ✅ `_run_summary` rewrite — Task 6
- ✅ `get_ai_task` race fix — Task 7
- ✅ Editor Tab 鍵 → 4 空白 — Task 8
- ✅ Module isolation enforced (new file `mynotes/ai_local.py`, no import from `notes/`) — Task 1
- ✅ Test files match spec list — Tasks 1-7
- ✅ Branch workflow (worktree + branch + verify-then-merge) — Tasks 0, 9
- ✅ No 摘要開始/結束 markers — Task 5 + explicit assert
- ✅ No 「（無結論）」 — Task 5 + explicit assert
- ✅ 「上去」「下來」 in 調整 keywords — Task 4

**2. Placeholder scan:** none. Every step contains executable code or commands.

**3. Type consistency:**
- `_split_sections` returns `dict[str, str]` — used as `dict.get` / `in` in `summarize_meeting` ✓
- `_split_items` returns `list[str]` — iterated in `summarize_meeting` ✓
- `_extract_conclusion` returns `tuple[str, Optional[str]]` — destructured as `(issue, conclusion)` ✓
- `_classify_personnel` returns `str` — used as dict key into `buckets` ✓
- `summarize_meeting(title: str, content: str) -> str` — passed via threadpool `args=(n.title or "", n.content or "")` ✓
- `_AI_TASK_GRACE_SEC = 30` (int seconds) — added to `time.time()` (float) — implicit conversion fine ✓

No inconsistencies found.
