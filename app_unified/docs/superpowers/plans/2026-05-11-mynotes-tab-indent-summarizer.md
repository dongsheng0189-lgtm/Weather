# mynotes TAB-indent summarizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `mynotes/ai_local.py::_split_items` to recognize TAB-indented items with same-indent `結論：` lines, so the user's actual note format produces a non-empty summary.

**Architecture:** Two-path item detection in `_split_items` — try existing numbered/bulleted regex first (preserves current behavior); fall through to a TAB-mode line walker that pairs items with following `結論：` lines, emitting them as `<issue>\n結論：<conclusion>` strings. Downstream `_extract_conclusion` + `_classify_personnel` + renderer work unchanged.

**Tech Stack:** Python 3.12, stdlib `re`, pytest. No new dependencies.

**Spec:** `app_unified/docs/superpowers/specs/2026-05-11-mynotes-tab-indent-summarizer.md`

---

## Task 0: Set up worktree + baseline

**Files:** None modified. This is the baseline-and-isolation task.

- [ ] **Step 0.1: Create worktree off main**

```bash
cd /home/hirain0126/projects/webapp
git worktree add .worktrees/fix-mynotes-tab-indent -b fix/mynotes-tab-indent main
cd .worktrees/fix-mynotes-tab-indent/app_unified
```

- [ ] **Step 0.2: Run existing mynotes ai_local tests — confirm baseline green**

Run:
```bash
/home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v
```
Expected: all tests pass (the suite that already exists pre-change).

If any fail before any change: STOP, investigate. Do not proceed.

---

## Task 1: TDD red — write failing tests for TAB-mode happy path

**Files:**
- Modify: `app_unified/tests/test_mynotes_ai_local.py` (append at end)

- [ ] **Step 1.1: Append the new test block**

Paste at the bottom of `tests/test_mynotes_ai_local.py`:

```python
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
    section_body = "\t冷氣維修\n\t結論：請廠商來估\n\t外牆修補\n\t結論：8月發包\n"
    items = _split_items(section_body)
    assert items == [
        "冷氣維修\n結論：請廠商來估",
        "外牆修補\n結論：8月發包",
    ]


def test_split_items_tab_indent_item_without_conclusion():
    """TAB-mode: item alone with no following 結論 line — emit issue only."""
    section_body = "\t冷氣維修\n\t外牆修補\n\t結論：8月發包\n"
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


def test_split_items_tab_indent_blank_lines_ignored():
    """TAB-mode: blank lines between items are skipped, not item boundaries."""
    section_body = "\t冷氣維修\n\n\t結論：請廠商來估\n\n\t外牆修補\n"
    items = _split_items(section_body)
    assert items == ["冷氣維修\n結論：請廠商來估", "外牆修補"]


def test_split_items_tab_indent_non_tab_lines_skipped():
    """TAB-mode: lines without TAB prefix (e.g. preamble noise) are skipped."""
    section_body = "preamble noise\n\t冷氣維修\nmore noise\n\t結論：請廠商來估\n"
    items = _split_items(section_body)
    assert items == ["冷氣維修\n結論：請廠商來估"]


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
```

- [ ] **Step 1.2: Run new tests — verify they all FAIL**

Run:
```bash
/home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v -k "tab_indent or tab_indent_end_to_end" 2>&1 | tail -30
```
Expected: 6 new tests all FAIL (because `_split_items` returns `[]` when ITEM_START_RE finds nothing).

Do NOT proceed to Task 2 until all 6 are failing for the right reason ("empty list returned" / "0 items where N expected"). If a test fails with `ImportError` or syntax error, fix the test first.

---

## Task 2: TDD green — implement TAB-mode in `_split_items`

**Files:**
- Modify: `app_unified/mynotes/ai_local.py:79-95` (the existing `_split_items` function)

- [ ] **Step 2.1: Replace `_split_items` with two-path version**

Find this in `app_unified/mynotes/ai_local.py`:

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

Replace with:

```python
# TAB-mode (2026-05-11): user's actual writing style has no item marker —
# each TAB-indented line is either an item, or a `結論：` continuation of the
# previous item. Detected only when ITEM_START_RE finds zero matches (so this
# is a strict fallback, never competes with numbered/bulleted format).
_TAB_CONCLUSION_PREFIX_RE = re.compile(r"^結論\s*[:：]\s*(.*)$")


def _split_items(section_body: str) -> list[str]:
    """切項目。

    優先用 ITEM_START_RE 抓編號/bullet 項目（原行為）。若一個都沒抓到，
    退化成 TAB-mode：每個 TAB-縮排行視為 item，遇到同階 `結論：` 前綴的
    行就 attach 到上一個 item。Item 與 conclusion 用 `\\n結論：` 串接，
    回傳給 `_extract_conclusion` 後續解析（renderer 不需區分模式）。

    回傳 list of item texts (含結論詞、不含項目編號 / bullet 字元)。
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

    # Path 2: TAB-indent fallback
    items = []
    current_issue: str | None = None
    current_conclusion: str | None = None

    def flush() -> None:
        nonlocal current_issue, current_conclusion
        if current_issue is None:
            return
        if current_conclusion is not None:
            items.append(f"{current_issue}\n結論：{current_conclusion}")
        else:
            items.append(current_issue)
        current_issue = None
        current_conclusion = None

    for line in section_body.splitlines():
        if not line.strip():
            continue  # blank line — not a boundary, just whitespace
        if not line.startswith("\t"):
            continue  # non-TAB line inside section — preamble noise, skip
        content = line.lstrip("\t").strip()
        m = _TAB_CONCLUSION_PREFIX_RE.match(content)
        if m:
            # `結論：xxx` line. Attach to current item if one exists; orphan
            # otherwise (silently dropped — see spec edge case).
            if current_issue is not None:
                current_conclusion = m.group(1).strip()
        else:
            # New item — flush the previous (item + optional conclusion) pair.
            flush()
            current_issue = content

    flush()  # final pending item
    return items
```

- [ ] **Step 2.2: Run all 6 new TAB-mode tests — verify they PASS**

Run:
```bash
/home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v -k "tab_indent or tab_indent_end_to_end" 2>&1 | tail -20
```
Expected: all 6 PASS.

If any fail: do NOT keep editing — read the failure message, find the bug, fix it. One issue at a time.

- [ ] **Step 2.3: Run full mynotes ai_local test file — confirm zero regression**

Run:
```bash
/home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_mynotes_ai_local.py -v 2>&1 | tail -10
```
Expected: all tests pass (existing + 6 new = whatever total). Confirm the line says `N passed` with no `failed` or `error`.

- [ ] **Step 2.4: Commit the TDD red-green cycle**

```bash
cd /home/hirain0126/projects/webapp/.worktrees/fix-mynotes-tab-indent/app_unified
git add mynotes/ai_local.py tests/test_mynotes_ai_local.py
git commit -m "$(cat <<'EOF'
feat(mynotes/ai-local): accept TAB-indent items with same-indent 結論 lines

User's actual writing style uses TAB indentation with no item marker:
sections detected fine, but every section_body produced zero items via
ITEM_START_RE (which requires explicit number/bullet prefix). Output
collapsed to just "# 會議摘要" with no content.

Add a fallback path in _split_items: when ITEM_START_RE finds zero matches,
walk lines and treat each TAB-indented line as either an item (no 結論
prefix) or a conclusion for the previous item (結論: / 結論：prefix).
Item + conclusion are joined as "<issue>\n結論：<conclusion>" so the
existing _extract_conclusion regex parses it back out — renderer and
personnel classification need no changes.

Numbered/bulleted format keeps working unchanged (Path 1 still runs first).

6 new tests cover: happy-path with conclusions, item alone, orphan
conclusion silently dropped, blank lines tolerated, non-TAB lines skipped,
end-to-end with full personnel bucket sort.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Full-suite regression check

**Files:** None modified. Sanity check.

- [ ] **Step 3.1: Run entire app_unified test suite**

Run:
```bash
/home/hirain0126/projects/webapp/venv/bin/python -m pytest 2>&1 | tail -3
```
Expected: 337 passed (previous baseline was 331 passed; +6 new TAB tests = 337). 0 failed, 0 errors.

If anything regresses: STOP. The TAB-mode change should be additive — investigate which test broke and why. Do not "fix" the regression by changing the new code's behavior without re-reading the spec.

- [ ] **Step 3.2: If suite is clean, advance to Task 4. If not, file a comment in this plan documenting the failure and stop.**

---

## Task 4: Hand off for merge + push decision

**Files:** None modified.

- [ ] **Step 4.1: Show user the diff summary**

Run:
```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```

Output should show 1 commit + 2 files changed (`mynotes/ai_local.py` + `tests/test_mynotes_ai_local.py`).

- [ ] **Step 4.2: ASK USER whether to ff-merge + push, or leave on branch**

Stop and ask. Do not auto-merge or auto-push. Per `feedback_push_policy`, the user must explicitly authorize push for each session.

Suggested phrasing:

> "TAB-mode mynotes summarizer 完成、6 new tests + 0 regression。要 ff-merge main + push 嗎？跟 psycogreen / auth-test 那兩次一樣。或先擱著 24h 你實機跑跑看再 merge。"

- [ ] **Step 4.3: If user says merge + push:**

```bash
cd /home/hirain0126/projects/webapp
git merge --ff-only fix/mynotes-tab-indent
git push origin main
```

- [ ] **Step 4.4: If user says leave on branch:**

Branch stays on worktree, no push. User can `git push origin fix/mynotes-tab-indent` later if they want, or test locally first via `bash start_app1.sh` style local run.

---

## Self-Review Notes (kept for traceability)

**Spec coverage check:**
- Section header detection unchanged → no task needed (existing regex covers)
- TAB item detection → Task 1 (tests) + Task 2 (impl) ✓
- Same-indent `結論：` detection → Task 1 (tests) + Task 2 (impl) ✓
- Item without conclusion → Task 1 test ✓
- Orphan conclusion silently dropped → Task 1 test ✓
- Mixed format (numbered + TAB across sections) — covered implicitly: Task 2 keeps Path 1 first, so a section with `1.` items uses numbered path, another section with TAB items uses TAB path. No dedicated test, but Path 1's existing tests + Path 2's new tests together exercise this.
- Personnel 4-bucket classification unchanged → no task; Task 1's end-to-end test covers the rendering order assertion.
- Regression guard → Task 3 ✓

**Placeholder scan:** no "TBD" / "TODO" / "similar to" patterns. Each step has runnable command or full code block.

**Type / identifier consistency:**
- `_TAB_CONCLUSION_PREFIX_RE` defined in Task 2.1; used only inside Task 2.1's `_split_items`. No drift.
- `current_issue` / `current_conclusion` / `flush` defined within `_split_items`'s local scope; not referenced outside.
- `summarize_meeting` and `_extract_conclusion` are NOT modified — verify by searching the implementation block in Task 2.1: only `_split_items` is replaced.
