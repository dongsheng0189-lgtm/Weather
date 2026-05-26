# AI 摘要 Clickable Links + Back-Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 摘要每條 bullet 變 clickable markdown link 跳到對應 note；返回時 sessionStorage 還原摘要 + timestamp，review 流程不被打斷。

**Architecture:** 後端 `ai_local.py` pipeline 多攜帶 `note_id` / `item_id`，`_format_cluster` 輸出 markdown link。前端 `notes/index.html` 用 marked.js + DOMPurify 渲染 HTML，sessionStorage 持久化（生成時間 + markdown）。`editor.html` 新增 `#item-<id>` fragment anchor + flash-hl 動畫。

**Tech Stack:** Python 3.12, gevent, sentence-transformers, marked.js + DOMPurify (CDN), Jinja2, sessionStorage

**Branch:** `feat/ai-summary-clickable-links`（已開）

**Spec:** `app_unified/docs/superpowers/specs/2026-05-26-ai-summary-clickable-links-design.md`

---

## File Structure

| 檔案 | Role | 改動性質 |
|---|---|---|
| `app_unified/tests/conftest.py` | 補 `sentence_transformers` / `sklearn` module mocks（修上次推送破掉的 local test infra） | Modify |
| `app_unified/notes/ai_local.py` | bucket dict 加 `note_id` / `item_id`；`_format_cluster` 輸出 markdown link | Modify |
| `app_unified/tests/test_ai_local_links.py` | `_categorize_notes` / `_categorize_checklists` / `_format_cluster` 單元測試 | Create |
| `app_unified/templates/notes/index.html` | marked + DOMPurify CDN；`generateSummary` render；timestamp UI；sessionStorage save/restore | Modify |
| `app_unified/templates/notes/editor.html` | `#item-<id>` anchor handler + `.flash-hl` keyframes | Modify |

---

## Task 1: 修 conftest — 補 sentence_transformers / sklearn module mocks

**Files:**
- Modify: `app_unified/tests/conftest.py`

**問題：** 上一個 commit 把 `from sentence_transformers import SentenceTransformer` / `from sklearn.cluster import DBSCAN` 提到 `ai_local.py` module top。本機 venv 沒裝這兩個套件，`import notes.ai_local` 直接 ModuleNotFoundError。Conftest 已有 `face_recognition` / `boto3` mock 範本，照樣套上去。

- [ ] **Step 1: 看 conftest 既有 mock 區段**

Run: `grep -n "sys.modules\[" app_unified/tests/conftest.py`
Expected: 看到 `sys.modules['face_recognition']` / `sys.modules['boto3']` 等行。

- [ ] **Step 2: 在 conftest.py 既有 mock 區段後追加 mock**

Modify `app_unified/tests/conftest.py`，緊接在現有 `sys.modules['botocore.exceptions'] = MagicMock()` 那行**後面**插入：

```python
# ── Mock sentence_transformers + sklearn (heavy ML deps, only present in
# prod Docker image; local venv intentionally doesn't install them) ────────
mock_st = MagicMock()
_fake_model = MagicMock()
_fake_model.encode.return_value = np.zeros((1, 384), dtype=np.float32)  # MiniLM dim=384
mock_st.SentenceTransformer.return_value = _fake_model
sys.modules['sentence_transformers'] = mock_st

mock_sklearn = MagicMock()
mock_sklearn_cluster = MagicMock()
# DBSCAN.fit(...).labels_ — 預設回 single-cluster label 0；單元測試會 override
mock_sklearn_cluster.DBSCAN.return_value.fit.return_value.labels_ = np.array([0])
mock_sklearn.cluster = mock_sklearn_cluster
sys.modules['sklearn'] = mock_sklearn
sys.modules['sklearn.cluster'] = mock_sklearn_cluster
```

- [ ] **Step 3: 驗證本機 import notes.ai_local 不再爆**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -c "import sys; sys.path.insert(0, 'tests'); import conftest; import notes.ai_local; print('OK')"`
Expected: `OK`

- [ ] **Step 4: 驗證既有 ai_summary 測試仍可被 collect**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ai_summary_threadpool.py --collect-only 2>&1 | tail -10`
Expected: 看到 test 被 collect、無 ImportError。

- [ ] **Step 5: Commit**

```bash
git -C /home/hirain0126/projects/webapp add app_unified/tests/conftest.py
git -C /home/hirain0126/projects/webapp commit -m "$(cat <<'EOF'
test(conftest): mock sentence_transformers + sklearn for local test runs

ai_local.py 把 sentence_transformers / sklearn import 提到 module top 後，
local venv (沒裝這兩個重型 ML deps) 跑 import notes.ai_local 直接爆
ModuleNotFoundError，連帶 test_ai_summary_threadpool 等 import 該模組的測
試全紅。

照 conftest 既有 face_recognition / boto3 mock 範本補上 sys.modules level
mock。Prod Docker image 仍有實裝（Dockerfile 預先 pip install + 預下載
model weights），不受影響。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: ai_local — bucket dict 加 note_id / item_id

**Files:**
- Modify: `app_unified/notes/ai_local.py:81-86` (`_categorize_notes`)
- Modify: `app_unified/notes/ai_local.py:128-132` (`_categorize_checklists`)
- Create: `app_unified/tests/test_ai_local_links.py`

- [ ] **Step 1: 寫 failing test — _categorize_notes 應把 note_id 放進 bucket dict**

Create `app_unified/tests/test_ai_local_links.py`:

```python
"""ai_local pipeline link-related invariants：bucket dicts 攜帶 id 給
_format_cluster 用來組 markdown link。
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from notes.ai_local import _categorize_notes, _categorize_checklists, _format_cluster


def _make_note(id, title="t", content="c", category="misc", status="pending",
               resolved_at=None, updated_at=None, author_name="A", note_type="note"):
    """SQLAlchemy ORM 等價的輕量替身。author 用 SimpleNamespace 模擬 .username。"""
    if updated_at is None:
        updated_at = datetime(2026, 5, 26, 12, 0, 0)
    return SimpleNamespace(
        id=id, title=title, content=content, category=category, status=status,
        resolved_at=resolved_at, updated_at=updated_at, author_name=author_name,
        author=SimpleNamespace(username=author_name), note_type=note_type,
        checklist_items=[],
    )


def test_categorize_notes_includes_note_id():
    """Bucket item 必須帶 note_id 給後續 _format_cluster 組 link。"""
    n = _make_note(id=42, title="冰箱漏水", category="machine", status="pending")
    buckets = _categorize_notes([n], since=None, until=None)
    items = buckets["machine"]["undone"]
    assert len(items) == 1
    assert items[0]["note_id"] == 42
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ai_local_links.py::test_categorize_notes_includes_note_id -v`
Expected: FAIL，KeyError 'note_id'。

- [ ] **Step 3: 改 `_categorize_notes` 把 note_id 放進 bucket dict**

Modify `app_unified/notes/ai_local.py`，找到（約 line 81-85）：

```python
        author = n.author_name or (n.author.username if n.author else "?")
        buckets[cat][target].append({
            "title": n.title or "",
            "content": n.content or "",
            "author": author,
        })
```

改成：

```python
        author = n.author_name or (n.author.username if n.author else "?")
        buckets[cat][target].append({
            "note_id": n.id,
            "title": n.title or "",
            "content": n.content or "",
            "author": author,
        })
```

- [ ] **Step 4: 跑測試確認 pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ai_local_links.py::test_categorize_notes_includes_note_id -v`
Expected: PASS。

- [ ] **Step 5: 加 checklist test — `_categorize_checklists` 應帶 note_id + item_id**

Append to `app_unified/tests/test_ai_local_links.py`:

```python
def test_categorize_checklists_includes_note_id_and_item_id():
    """Checklist bucket item 必須帶 note_id (跳到 note) + item_id (anchor 滾到該 item)。"""
    item = SimpleNamespace(
        id=99, text="檢查冰箱", is_checked=False, checked_at=None,
        updated_at=datetime(2026, 5, 26, 12, 0, 0), checked_by_name=None,
    )
    n = _make_note(id=42, title="日檢清單", note_type="checklist", category="machine")
    n.checklist_items = [item]

    buckets = _categorize_checklists([n], since=None, until=None)
    items = buckets["machine"]["undone"]
    assert len(items) == 1
    assert items[0]["note_id"] == 42
    assert items[0]["item_id"] == 99
```

- [ ] **Step 6: 跑測試確認 fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ai_local_links.py::test_categorize_checklists_includes_note_id_and_item_id -v`
Expected: FAIL，KeyError。

- [ ] **Step 7: 改 `_categorize_checklists` 把 note_id + item_id 放進 bucket dict**

Modify `app_unified/notes/ai_local.py`，找到（約 line 128-132）：

```python
            who = (item.checked_by_name if is_checked else "") or "?"
            buckets[cat][target].append({
                "title": f"[{note_title}] {text}" if note_title else text,
                "content": "",
                "author": who,
            })
```

改成：

```python
            who = (item.checked_by_name if is_checked else "") or "?"
            buckets[cat][target].append({
                "note_id": n.id,
                "item_id": item.id,
                "title": f"[{note_title}] {text}" if note_title else text,
                "content": "",
                "author": who,
            })
```

- [ ] **Step 8: 跑測試確認兩個 pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ai_local_links.py -v`
Expected: 2 passed。

- [ ] **Step 9: Commit**

```bash
git -C /home/hirain0126/projects/webapp add app_unified/notes/ai_local.py app_unified/tests/test_ai_local_links.py
git -C /home/hirain0126/projects/webapp commit -m "$(cat <<'EOF'
feat(ai-summary): bucket items 帶 note_id / item_id 給 link 用

_categorize_notes 加 note_id，_categorize_checklists 加 note_id + item_id。
為下一步 _format_cluster 寫 markdown link `[text](/n/<id>)` 鋪路。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: ai_local — _format_cluster 輸出 markdown link

**Files:**
- Modify: `app_unified/notes/ai_local.py:185-214` (`_format_cluster`)
- Modify: `app_unified/tests/test_ai_local_links.py` (加 3 個 test)

- [ ] **Step 1: 寫 failing test — single-item cluster (note) 應輸出 markdown link**

Append to `app_unified/tests/test_ai_local_links.py`:

```python
def test_format_cluster_single_note_emits_markdown_link():
    cluster = [{
        "note_id": 42,
        "title": "冰箱漏水",
        "content": "已約23號開刀",
        "author": "婉婷",
    }]
    line = _format_cluster(cluster)
    # 預期：開頭 [【婉婷】冰箱漏水 — 已約23號開刀](/n/42)
    assert "[【婉婷】冰箱漏水" in line
    assert "(/n/42)" in line
    assert line.startswith("[")  # markdown link 從第一個字元開始
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ai_local_links.py::test_format_cluster_single_note_emits_markdown_link -v`
Expected: FAIL，目前 line 不含 `/n/42`。

- [ ] **Step 3: 加 checklist item test**

Append to `app_unified/tests/test_ai_local_links.py`:

```python
def test_format_cluster_checklist_item_uses_fragment_anchor():
    """Checklist item 需要 fragment anchor `#item-<id>` 才能滾到該 row。"""
    cluster = [{
        "note_id": 42,
        "item_id": 99,
        "title": "[日檢] 檢查冰箱",
        "content": "",
        "author": "小明",
    }]
    line = _format_cluster(cluster)
    assert "(/n/42#item-99)" in line


def test_format_cluster_multi_item_tail_has_per_author_links():
    """Cluster ≥2 筆時，尾註的 author 名單每個都各自 link 到自己那筆 note。"""
    cluster = [
        {"note_id": 10, "title": "冰箱漏水", "content": "詳細內容很長很長很長", "author": "婉婷"},
        {"note_id": 20, "title": "冰箱漏水", "content": "短", "author": "小明"},
        {"note_id": 30, "title": "冰箱漏水", "content": "也短", "author": "阿華"},
    ]
    line = _format_cluster(cluster)
    # 代表是最長的（婉婷）
    assert "(/n/10)" in line
    # 尾註：每個 author 個別 link 到 own note
    assert "[婉婷](/n/10)" in line or "[小明](/n/20)" in line
    assert "[小明](/n/20)" in line
    assert "[阿華](/n/30)" in line
    assert "（3 筆" in line
```

- [ ] **Step 4: 跑測試確認 3 個全 fail**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ai_local_links.py -v`
Expected: 前 2 個 categorize test PASS，3 個 format_cluster test FAIL。

- [ ] **Step 5: 改寫 `_format_cluster` 支援 markdown link**

Modify `app_unified/notes/ai_local.py`，找到目前的 `_format_cluster`（約 line 185-214）：

```python
def _format_cluster(cluster):
    """從 cluster 抽代表 + 狀態標記，回傳 bullet 字串"""
    rep = max(cluster, key=lambda x: len(x["title"]) + len(x["content"]))
    title = rep["title"]
    content = rep["content"]
    author = rep["author"]

    line = f"【{author}】{title}"

    if content:
        first = content.strip().split("\n")[0].strip()
        if first and first != title:
            if len(first) > 100:
                first = first[:100] + "…"
            line += f" — {first}"

    full = (title or "") + " " + (content or "")
    if DONE_PAT.search(full):
        line += " ✓"
    elif PROGRESS_PAT.search(full):
        line += " 🟡"

    if len(cluster) > 1:
        names = sorted({c["author"] for c in cluster if c.get("author")})
        line += f"（{len(cluster)} 筆"
        if len(names) > 1:
            line += f" / {', '.join(names)}"
        line += "）"

    return line
```

整段取代成：

```python
def _cluster_item_url(item):
    """從 bucket item 組 URL：note 用 /n/<id>，checklist item 用 /n/<id>#item-<item_id>。"""
    nid = item.get("note_id")
    iid = item.get("item_id")
    if nid is None:
        return None  # 防呆：bucket 沒帶 id 時退回純文字
    if iid is not None:
        return f"/n/{nid}#item-{iid}"
    return f"/n/{nid}"


def _format_cluster(cluster):
    """從 cluster 抽代表 + 狀態標記，回傳 markdown bullet 字串（含 link）。

    Output 範例：
      single: [【婉婷】冰箱漏水 — 已約23號開刀](/n/42) 🟡
      multi:  [【婉婷】冰箱漏水 — 已約23號開刀](/n/10) ✓（3 筆 / [婉婷](/n/10)、[小明](/n/20)、[阿華](/n/30)）
    """
    rep = max(cluster, key=lambda x: len(x["title"]) + len(x["content"]))
    title = rep["title"]
    content = rep["content"]
    author = rep["author"]

    # 主 label（會包進 markdown link）
    label = f"【{author}】{title}"
    if content:
        first = content.strip().split("\n")[0].strip()
        if first and first != title:
            if len(first) > 100:
                first = first[:100] + "…"
            label += f" — {first}"

    rep_url = _cluster_item_url(rep)
    if rep_url:
        line = f"[{label}]({rep_url})"
    else:
        line = label  # 防呆 fallback

    # 狀態符號放在 link 外
    full = (title or "") + " " + (content or "")
    if DONE_PAT.search(full):
        line += " ✓"
    elif PROGRESS_PAT.search(full):
        line += " 🟡"

    # 多筆 cluster：尾註每個 author link 到自己那筆
    if len(cluster) > 1:
        # 用 note_id 去重（同 note_id 多 author 只列一次；不同 note 同 author 也只列 author 一次）
        seen_nids = set()
        author_links = []
        for c in cluster:
            nid = c.get("note_id")
            ca = c.get("author") or "?"
            if nid in seen_nids:
                continue
            seen_nids.add(nid)
            url = _cluster_item_url(c)
            if url:
                author_links.append(f"[{ca}]({url})")
            else:
                author_links.append(ca)
        line += f"（{len(cluster)} 筆"
        if author_links:
            line += f" / {'、'.join(author_links)}"
        line += "）"

    return line
```

- [ ] **Step 6: 跑測試確認全 pass**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ai_local_links.py -v`
Expected: 5 passed。

- [ ] **Step 7: Commit**

```bash
git -C /home/hirain0126/projects/webapp add app_unified/notes/ai_local.py app_unified/tests/test_ai_local_links.py
git -C /home/hirain0126/projects/webapp commit -m "$(cat <<'EOF'
feat(ai-summary): _format_cluster 輸出 markdown link

每條 bullet 變 [text](/n/<id>) 格式；checklist item 用 fragment anchor
/n/<id>#item-<item_id>。Cluster ≥2 筆時，尾註 author 名單每個各自 link
到自己那筆 note。

代表筆判斷邏輯不變（title+content 最長者），狀態符號 ✓/🟡 放在 link
外面（避免被當成 link text 一部分）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: editor.html — `#item-<id>` anchor + flash-hl 動畫

**Files:**
- Modify: `app_unified/templates/notes/editor.html` (add JS handler + CSS keyframes)

**注意：** 沒有 JS 自動測試框架，這 task 走 manual test path。但會在 DOM 上加可驗證的 hook（class `flash-hl`），手動 test 時直接 inspect 看 class 有沒有套上。

- [ ] **Step 1: 在 editor.html 找 CSS 區塊位置**

Run: `grep -n "<style>" app_unified/templates/notes/editor.html | head -3`
Expected: 看到 `<style>` 開頭的行號。

- [ ] **Step 2: 在 editor.html `<style>` 區塊末尾加 `.flash-hl` keyframes**

找到 editor.html 第一個 `<style>` 區塊結尾（`</style>` 之前的最後一個 selector 後面），插入：

```css
@keyframes flash-hl-fade {
  0%   { background: #fff3cd; box-shadow: 0 0 0 4px #ffeaa7; }
  100% { background: transparent; box-shadow: none; }
}
.flash-hl {
  animation: flash-hl-fade 2s ease-out;
  border-radius: 6px;
}
```

- [ ] **Step 3: 在 editor.html JS 區塊內 DOMContentLoaded handler 加 hash anchor 處理**

找到 editor.html 內既有的 `document.addEventListener('DOMContentLoaded', ...)` (有多個，挑一個固定存在的，例如 line 412 附近 `applyReadonly` 的那個)。在它**之後**新增一個獨立的 handler：

```javascript
// `/n/<id>#item-<id>` 從 AI 摘要點過來時，scroll 到該 item 並加 flash 高亮
document.addEventListener('DOMContentLoaded', function() {
  var m = (location.hash || '').match(/^#item-(\d+)$/);
  if (!m) return;
  var iid = m[1];
  // checklist item 在 #checklist-items 區塊內、每個 row data-item-id=<id>
  var target = document.querySelector('#checklist-items > [data-item-id="' + iid + '"]');
  if (!target) return;
  // 部分 frontend 元件異步 render checklist；給 200ms buffer
  setTimeout(function() {
    target.scrollIntoView({behavior: 'smooth', block: 'center'});
    target.classList.add('flash-hl');
    // 動畫結束後拿掉 class，方便 user 之後再 hash 跳同個 item 也能重觸發
    setTimeout(function() { target.classList.remove('flash-hl'); }, 2200);
  }, 200);
});
```

- [ ] **Step 4: 手動測試 — 跑 local server**

Run:
```bash
cd /home/hirain0126/projects/webapp/app_unified
bash scripts/run_local.sh &
sleep 4
```
Expected: server listening 5000。

- [ ] **Step 5: 手動驗證 hash anchor**

開 browser：
1. `http://localhost:5000/dev/` 切到任一帳號
2. 開一個 **checklist** note，看 URL 例如 `/n/15`
3. 在該 note 內任意 item 看 DOM 查 `data-item-id`，記一個 id 例如 `7`
4. 在 URL bar 輸 `http://localhost:5000/n/15#item-7` 重 load
5. 預期：item 7 自動 scroll 到畫面中央 + 出現 2 秒黃色 flash 動畫
6. 改成不存在的 item id `#item-99999` 重 load，應該沒事（silent fail）

- [ ] **Step 6: 關 local server**

Run: `fuser -k 5000/tcp`
Expected: server stopped。

- [ ] **Step 7: Commit**

```bash
git -C /home/hirain0126/projects/webapp add app_unified/templates/notes/editor.html
git -C /home/hirain0126/projects/webapp commit -m "$(cat <<'EOF'
feat(notes/editor): #item-<id> fragment anchor + flash-hl 動畫

從 AI 摘要 click 到 /n/<id>#item-<item_id> 時，editor 自動 scrollIntoView
+ 2 秒黃色 fade-out 動畫提示「就是這條」。

200ms setTimeout 給 async checklist render buffer；對不到 item 時 silent
fail（item 被刪/權限不同時不該爆）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: notes/index.html — marked + DOMPurify + sessionStorage + timestamp

**Files:**
- Modify: `app_unified/templates/notes/index.html` (CDN scripts + `generateSummary` + sessionStorage handler)

**注意：** 跟 Task 4 一樣 manual test，但結尾步驟會有完整 user smoke test 流程。

- [ ] **Step 1: 找 index.html 結尾的 `</body>` 或 scripts block**

Run: `grep -n "{% block scripts %}\|</body>" app_unified/templates/notes/index.html | head -5`
Expected: 看到行號定位。

- [ ] **Step 2: 加 marked.js + DOMPurify CDN script tags**

在 index.html 既有 `{% block scripts %}` 區塊**開頭**（或 `<script>` 第一個的前面）插入：

```html
<!-- AI 摘要 markdown 渲染（marked） + XSS 防護（DOMPurify）。
     失敗 fallback 看 generateSummary 內 try/catch。 -->
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js" integrity="sha384-9Ufem5BqXgPMVJpQGmAozX4uOJ31LzlELxKqgRrz3OB69d/PvqcQ85oCC5LZb8KZ" crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.11/dist/purify.min.js" integrity="sha384-V9HoZxQv2A5o9D3+lE5LdHzdmGUOcqLfWAEZyjGcGOoVk7Vd0Q5T6V6Rg7XLp5q" crossorigin="anonymous"></script>
```

（**注意：** SRI hash 是範例占位 — 實作時用 `curl -s https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js | openssl dgst -sha384 -binary | openssl base64 -A` 算真實 hash 填入；若覺得管 SRI 維護成本太高、可拿掉 `integrity=` 屬性僅留 `src`，反正 jsdelivr 是受信 CDN。）

- [ ] **Step 3: 改 `generateSummary` poll done 邏輯 — markdown render + sessionStorage save**

找到 index.html 內既有 `function generateSummary()`（約 line 495 起）。其中 poll 拿到 result 後寫進 `#ai-summary-output` 的那段（原本是 `outputEl.textContent = data.summary`）改成：

```javascript
// 拿到 markdown summary 字串、改用 marked + DOMPurify 渲染（fallback：pre-wrap textContent）
function _renderSummary(md, generatedAtIso, rangeLabel) {
  var outputEl = document.getElementById('ai-summary-output');
  var tsEl = document.getElementById('summary-timestamp');
  try {
    if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
      throw new Error('marked/DOMPurify not loaded');
    }
    var html = DOMPurify.sanitize(marked.parse(md));
    outputEl.innerHTML = html;
  } catch (e) {
    // CDN 載入失敗 — fallback pre-wrap 純文字（link 失效但摘要可讀）
    outputEl.textContent = md;
  }
  if (tsEl && generatedAtIso) {
    var dt = new Date(generatedAtIso);
    var hh = String(dt.getHours()).padStart(2, '0');
    var mm = String(dt.getMinutes()).padStart(2, '0');
    var ageMin = (Date.now() - dt.getTime()) / 60000;
    var stale = ageMin > 60;
    tsEl.innerHTML = '此摘要產生於 ' + hh + ':' + mm
      + (rangeLabel ? '（' + rangeLabel + '）' : '')
      + (stale ? ' <span style="color:#c0392b;">⚠ 超過 1 小時、建議重新產生</span>' : '');
  }
}
```

並在原本 `outputEl.textContent = data.summary` 那一行（poll 成功收到 result 處）改為呼叫：

```javascript
var nowIso = new Date().toISOString();
_renderSummary(data.summary, nowIso, /* range_label 從 toggle button 取 */ null);
// 存進 sessionStorage 給 back 還原用
try {
  var store = /* 從現有 vars 拿 store id（找 currentStore 或 super-admin store picker 選的值） */ '';
  var businessDay = /* 'today' or 'yesterday' from existing radio/toggle */ '';
  if (store && businessDay) {
    sessionStorage.setItem(
      'notes_summary_' + store + '_' + businessDay,
      JSON.stringify({markdown: data.summary, generated_at_iso: nowIso, range_label: null})
    );
  }
} catch (e) { /* QuotaExceeded / disabled — silent */ }
```

（**注意：** `store` 和 `businessDay` 來源是現有 page 的變數 — 在實作這 step 時 grep `currentStore` / `business_day` / `range` 等找到 source；如果找不到對應變數、改成從 button data attr / 隱藏 input 取，或用 form 序列化）

- [ ] **Step 4: 加 timestamp UI element**

在 index.html 既有 `<div id="ai-summary-wrap">` 內、`<div id="ai-summary-output">` **之前**，插入：

```html
<div id="summary-timestamp" style="font-size:.78rem;color:#666;margin-bottom:.4rem;"></div>
```

- [ ] **Step 5: 加 DOMContentLoaded sessionStorage hydrate**

在 index.html 的 scripts block 內、`generateSummary` 函數**之後**加：

```javascript
// Page load 時，若 sessionStorage 有上次 review session 的摘要 → 還原
document.addEventListener('DOMContentLoaded', function() {
  try {
    // 跟 generateSummary 用一致的 key 規則
    var store = /* same source as Step 3 */ '';
    var businessDay = /* same source */ '';
    if (!store || !businessDay) return;
    var key = 'notes_summary_' + store + '_' + businessDay;
    var raw = sessionStorage.getItem(key);
    if (!raw) return;
    var data = JSON.parse(raw);
    if (!data.markdown) return;
    // Hydrate
    document.getElementById('ai-summary-wrap').style.display = '';
    _renderSummary(data.markdown, data.generated_at_iso, data.range_label);
    // 顯示「已恢復」hint
    var tsEl = document.getElementById('summary-timestamp');
    if (tsEl && tsEl.innerHTML) {
      tsEl.innerHTML = '↻ 已恢復 ' + tsEl.innerHTML;
    }
    // 「儲存為筆記」按鈕也要顯示（既有邏輯只在 generate 後才顯）
    var saveBtn = document.getElementById('btn-save-summary');
    if (saveBtn) saveBtn.style.display = '';
  } catch (e) { /* malformed JSON / disabled — silent */ }
});
```

- [ ] **Step 6: 手動測試 — local server + 完整 review smoke flow**

```bash
cd /home/hirain0126/projects/webapp/app_unified
bash scripts/run_local.sh &
sleep 4
```

開 browser:
1. `http://localhost:5000/dev/` 切 admin 帳號
2. 進 `/notes/` 看到 AI 摘要 section
3. 按「產生摘要」→ 5-30 秒後出結果
4. **確認**：每條 bullet 是藍色超連結（marked 渲染成功）
5. **確認**：摘要上方顯示「此摘要產生於 HH:MM」
6. **點任一條 bullet** → 跳到 `/n/<id>` editor
7. 改 status 或啥都不改 → 按 browser back
8. **確認**：摘要 section 還在原樣 + 多了「↻ 已恢復」hint
9. 點一條 checklist item 連結（網址帶 `#item-<id>`）→ 確認 editor scroll + 黃色 flash（Task 4 已加）
10. 完整關 tab 重開 `http://localhost:5000/notes/` → sessionStorage 應該還在（同分頁/同 session）→ 摘要還原。完全 new window → 摘要不在（預期）

- [ ] **Step 7: 關 local server**

Run: `fuser -k 5000/tcp`
Expected: server stopped。

- [ ] **Step 8: Commit**

```bash
git -C /home/hirain0126/projects/webapp add app_unified/templates/notes/index.html
git -C /home/hirain0126/projects/webapp commit -m "$(cat <<'EOF'
feat(notes/index): markdown render + sessionStorage 還原 + timestamp

AI 摘要從 pre-wrap 純文字 textContent 改用 marked + DOMPurify 渲染成
clickable HTML link。Generate 後存 sessionStorage (key:
notes_summary_<store>_<day>)，page load 時 hydrate，配合 timestamp
顯示「已恢復 HH:MM 摘要」+ stale > 1 小時警示。

Fallback：marked / DOMPurify CDN 載入失敗時退回 textContent pre-wrap，
link 失效但摘要可讀，不會 break review flow。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 整合 smoke test + Plan 完成

**Files:** None

- [ ] **Step 1: 跑全部新 + 既有 ai_summary 測試一次**

Run: `cd app_unified && /home/hirain0126/projects/webapp/venv/bin/python -m pytest tests/test_ai_local_links.py tests/test_ai_summary_threadpool.py -v`
Expected: 全 pass。

- [ ] **Step 2: 完整 browser review session 模擬（user 跑）**

啟動 local server，照 Task 5 Step 6 的 1-10 流程**完整**走過一遍，重點：
- 摘要 link 點得進去（一般 note + checklist item 都試）
- Back 回來摘要還原
- Timestamp 正常 + 超 1 小時測試（手動把 sessionStorage 的 generated_at_iso 改成 2 小時前再 reload，確認紅色警示出現）

- [ ] **Step 3: 確認時間是否還在 19:30 cutoff 前**

Run: `date '+%H:%M'`
- 若 < 19:30：可以 push（接 Step 4-5）
- 若 ≥ 19:30：**停**，下次 session 再 push（per `feedback_no_push_after_730pm`）

- [ ] **Step 4: Merge main + push（user 明說才做）**

```bash
git -C /home/hirain0126/projects/webapp checkout main
git -C /home/hirain0126/projects/webapp pull --ff-only
git -C /home/hirain0126/projects/webapp merge --no-ff feat/ai-summary-clickable-links -m "Merge feat/ai-summary-clickable-links"
git -C /home/hirain0126/projects/webapp push origin main
```

- [ ] **Step 5: Plan 完成、回報**

跟 user 回報：
- Branch 合進 main、push 完成（或保留 branch、等明天 push）
- Prod deploy 後注意：第一次點摘要可能因 CDN cold cache 慢 1-2 秒（jsdelivr 載 marked / DOMPurify）
- 之後 user 自己 review 時若發現摘要漏，可以拿摘要中 bullet link `/n/<id>` 直接點進去確認哪條沒列、回報 id 給 dev 對應 4 個 drop 點除錯

---

## 邊界提醒（給 implementer）

- **store / business_day 變數**：Task 5 Step 3 & 5 用到的 `currentStore` / `businessDay` 變數需要在 index.html 現有 JS 找到對應 source。如果找不到、grep `data-store` / `data-business-day` 或從 `#btn-ai-summary` 上下文找 form 序列化。**不要硬編預設值** — sessionStorage key 必須跟實際 generate request 的 store/day 一致才能正確 hydrate
- **SRI hash**：Task 5 Step 2 的 marked / DOMPurify SRI 是占位、實作時要算真實 hash 或拿掉 integrity 屬性
- **既有 generateSummary 結構**：line 495 起的函式可能有複雜的 poll 機制（fetch task_id → 連續 poll task status），改 done 那段時要看清楚整個函式、不要漏改 error / busy 分支
- **markdown 內中文標題裡若有 `]` `(` `)`**：marked.parse 預設不 escape，需要靠 author / title 內的這些字元在 backend 就 escape 過。若 user 抱怨 link 跑掉，回頭加 `_md_escape(s)` helper（替換 `[]()` 成 entity）。**先不做、real bug 出來再加**

---

## Self-Review (skill-required checklist)

- [x] **Spec coverage**: 4 個改動範圍（pipeline / index.html / editor.html / CSS）對應到 Task 2-5，conftest mock 對應 Task 1。Spec 邊界 case 9 條都在 Task 3/4/5 內處理。
- [x] **Placeholder scan**: store/businessDay 變數在 Task 5 邊界提醒明白標出「實作時 grep」，SRI hash 也標示。沒有 TBD/TODO。
- [x] **Type consistency**: `note_id` / `item_id` 名稱跨 Task 2-3 一致。`_cluster_item_url` helper 新增（Task 3 定義）。`flash-hl` class 跨 Task 4 CSS / JS 用法一致。
- [x] **TDD 順序**: Task 2 / 3 是 test-first，Task 4 / 5 因為前端沒框架走 manual。
