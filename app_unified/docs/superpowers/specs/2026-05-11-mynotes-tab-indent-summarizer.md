# mynotes summarize_meeting: TAB-indent input support

## Problem

The current `mynotes/ai_local.py::summarize_meeting` parser detects items only
via numbered or bulleted prefixes (`1.`, `1、`, `(1)`, `●`, `-`, etc.). The
user's actual writing convention uses **TAB indentation with no item marker** —
one TAB level for everything inside a section, and conclusions written on the
next same-indent line with a `結論：` prefix.

Result: parser finds the `店務:` / `人員:` section headers, but `_split_items`
returns empty for every section (no bullet/number patterns match), both
section blocks are gated behind `if items:`, and output collapses to:

```
# 會議摘要
```

Nothing else. The ⚠️ "未偵測到分類" branch doesn't fire either, because the
headers themselves ARE found.

## Goal

Extend the parser to recognize the user's TAB-indented style. Single-format,
no LLM, no API cost, deterministic. Existing numbered/bulleted format keeps
working (don't regress).

## Input format (target)

```
店務：
\t冷氣維修
\t結論：請廠商來估
\t外牆修補
\t結論：8月發包

人員：
\t阿明
\t結論：調班
\t小華
\t結論：離職、8月底交接
```

Rules:
- Section header line is unchanged — `店務:` or `人員:` alone on a line
  (existing `SECTION_HEADER_RE` already handles)
- Inside a section, **every line that begins with TAB** is treated as either:
  - **An item**, if the line content does NOT start with `結論` (allow optional
    whitespace between TAB and content)
  - **A conclusion for the previous item**, if the content starts with
    `結論` followed by `:` or `：`
- Lines without TAB indentation inside a section are ignored (preamble noise,
  blank lines)
- An item with no following conclusion line just shows as an issue, no `→`
  conclusion line in output (same as today)
- An orphan conclusion line (no preceding item) is silently dropped (edge
  case, don't crash)

## Output format

Unchanged from today (Format C):

```
# 會議摘要

🏪 店務
1. 冷氣維修
   → 請廠商來估
2. 外牆修補
   → 8月發包

👥 人員
### 離職
1. 小華
   → 離職、8月底交接
### 調整
1. 阿明
   → 調班
```

Personnel sub-categorization (離職 / 資遣 / 調整 / 其他) stays — applied per
item, keyword-matched against the item text combined with its conclusion
(so `結論：離職` correctly classifies the item to 離職 bucket).

## Behavior matrix

| Input shape | Today | After |
|---|---|---|
| Numbered/bulleted items | Works | Works (unchanged) |
| TAB-indent items + same-indent `結論：` lines | Empty output (bug) | Works |
| Mixed (some items numbered, some TAB) within same section | Items detected for the numbered ones only | All items detected |
| Section header missing | ⚠️ message | ⚠️ message (unchanged) |
| Section present but body completely empty | `# 會議摘要` only | `# 會議摘要` only (unchanged) |

## Non-goals

- LLM-based summarization (Gemini/Anthropic) — deliberately out of scope per
  user's "簡單做、用 local" directive
- New output format / new section types
- Free-form prose summarization (no bullet, no TAB, no number) — still
  considered unstructured and not handled
- Hybrid mode (parser-then-LLM-fallback) — defer to a future change if
  parser alone proves insufficient

## Implementation sketch

Add a second item-detection mode in `_split_items` (or a sibling helper):

1. Try existing `ITEM_START_RE` first. If it finds ≥1 match, use the current
   path (preserves numbered/bulleted format compatibility).
2. Otherwise, walk lines of `section_body`:
   - Skip blank lines
   - Skip non-TAB-prefixed lines
   - For each TAB-prefixed line: strip the TAB(s), check if content starts
     with `結論` + `:`/`：`. If yes, attach to current item's conclusion field
     (overwrite if previously set — last one wins, mirrors existing
     `_extract_conclusion` behavior). If no, push as new item.
3. Return items as before. To stay compatible with `_extract_conclusion`'s
   existing single-string contract (text containing optional `結論：` keyword
   inline), return TAB-mode items with conclusion appended as
   `<issue>\n結論：<conclusion>` so the existing `_extract_conclusion` parses
   it back out cleanly. This keeps downstream code (`summarize_meeting`)
   identical — no branching by mode in the renderer.

Personnel classification (`_classify_personnel`) already operates on item
text and matches keywords anywhere in it; combining issue + conclusion into
one string for classification already happens via the same call path, so
4-bucket sorting works without change.

## Test plan

New tests in `tests/test_mynotes_ai_local.py` (or wherever existing
`summarize_meeting` tests live):

- TAB-indent input, both sections, both items have conclusion → expected
  full output matching above
- TAB-indent input, one item without conclusion → only issue line, no `→`
- TAB-indent input, mixed bucket personnel (1 離職 + 1 調整) → both buckets
  shown in correct order
- Mixed format input: 店務 has numbered items, 人員 has TAB items → both
  parsed correctly
- Orphan conclusion (TAB-indent `結論：` line with no preceding item) →
  silently dropped, no crash
- Existing numbered/bulleted tests still pass (regression guard)

## Risks

- Ambiguity if user mixes TAB and spaces — spec says TAB only; document it
  so future-self knows
- `結論` keyword detection is substring on prefix — if user writes
  `結論性的討論` as an item it'll be misclassified as conclusion. Accept this
  edge case; user's actual writing convention always uses `結論：` (with
  colon) as the marker. Test asserts the colon variant only.
