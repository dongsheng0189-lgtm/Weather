# mynotes 會議摘要：雲端 LLM → local Python parser 設計

**日期**：2026-05-07
**模組**：私人筆記 (`/mynotes/`)，不含店務筆記 (`/notes/`)

## 動機

`mynotes` 的 meeting 類筆記目前用 `admin.routes.call_llm`（Gemini）做 AI 摘要。會議內容常涉及人事、薪資、商業決策，比 `notes`（店務）更敏感。使用者明確要求私人筆記不上雲，與 `notes` 5/6 改 local Python pipeline 同樣方向。

但 `mynotes` 是 single-note 摘要（一筆會議 → 一份結構化摘要），與 `notes` 的 multi-note 聚類完全不同 input shape，不能共用 `notes/ai_local.py`。需要獨立的 `mynotes/ai_local.py` 走 single-note pipeline。

順帶處理兩個既有問題：

1. `mynotes/routes.py::get_ai_task` 仍用第一次 done poll 立即 `del _ai_tasks[task_id]` 的舊邏輯，跟 `notes` 5/7 修掉的 race bug 一樣（用量小還沒踩到，但遲早會遇到）
2. 編輯器 Tab 鍵會跳下一個 input，使用者要求改成插 4 空白以利會議紀錄縮排對齊

## 範圍

- 新檔 `mynotes/ai_local.py`：single-note regex parser
- 改 `mynotes/routes.py::summarize` + `_run_summary`：拆掉 LLM 路徑、套 threadpool、補 race grace window
- 改 `templates/mynotes/editor.html`：content textarea 攔 Tab 鍵插 4 空白
- 對應 unit tests

## 不在範圍

- LLM-equivalent abstractive 改寫（local Python 做不到，user 同意接受）
- 議題長度截斷 / 句子精簡（user 改要求保留原文）
- 頁面 UI 顯示變動（前端仍從 `n.ai_summary` 抓字串顯示）
- 整體會議「總結段」（前面討論時 user 否決）
- 跨會議比較 / 多會議聚合（single-note 範圍）

## 摘要輸出格式（Format C：議題/結論分兩行 + 人員小類）

固定 2 個頂層 section：**🏪 店務** / **👥 人員**。section 內容由使用者在會議紀錄裡用 `店務:` 或 `店務：`、`人員:` 或 `人員：` 標頭區隔。

### 店務 section

每個項目：議題一行、結論縮排一行（無結論顯示「（無結論）」）。

```
🏪 店務
1. <議題原文>
   → <結論文字>
2. <議題原文>
   （無結論）
```

### 人員 section

人員 section 內按關鍵字偵測自動拆 4 個小類，**順序固定**：離職 → 資遣 → 調整 → 其他。空小類不輸出。

人員小類本身就是「結論」，因此項目下不顯示「（無結論）」；若使用者明確寫了結論詞，仍顯示「→ 結論文字」。

```
👥 人員
### 離職
1. 小李提離職, 5/15 生效
2. 王副店請辭, 月底走

### 資遣
1. 阿華因業績不佳資遣, 月底前

### 調整
1. 王經理升上副總, 6/1 生效
2. 小張調下倉庫部
   → 6 月起新部門報到

### 其他
1. 店長 5/12-14 請假
2. 小王績效好, 考慮獎金
```

### Markers + 摘要起訖

延續 `notes` 既有 convention，前後加 marker：

```
# 會議摘要

【摘要開始】

🏪 店務
...

👥 人員
...

【摘要結束】
```

## Parser 規則

### Section header 偵測

正則：`^\s*[【\[]?\s*(店務|人員)\s*[】\]]?\s*[:：]?\s*$`（單獨一行、可有可無冒號、可有可無【】或 [] 包覆）。例：

- `店務:`
- `人員：`
- `【店務】`
- `[人員]`
- `店務` / `人員`（純文字單獨一行）

- 找到 header 之前的內容歸 `_preamble`（不輸出）
- 兩個 section 之外的內容歸 `_other`（不輸出，但若 user 寫了 section 但內容空，輸出「⚠️ 未偵測到分類，請在會議紀錄加上『店務:』『人員:』標題」）

### Item splitting

Section 內按下列任一 pattern 開頭視為新項目：

- `^\s*\d+[.、)）]\s+` （阿拉伯數字 + 點/頓/括號）
- `^\s*[(（]\d+[)）]\s+` （括號數字）
- `^\s*[●•・▪◆◇★☆\-\*]\s+` （bullet 符號）

項目內容 = 該 pattern 那行起、到下個 item pattern 或下個 section header 之前的全部文字（含跨行）。

### Conclusion 抽取

每個 item 內找關鍵字：`結論`、`決定`、`決議`，後面跟全形或半形冒號。正則：

```python
re.compile(r"(?:結論|決定|決議)\s*[:：]\s*(.+?)(?=\n|$)")
```

抓 capture group 1 trim 後當結論。

- 找到多個結論詞 → 取**最後一個**（會議紀錄通常是討論後寫結論，最後出現的最定論）
- 結論文字若為空字串 → 視為無結論
- 議題文字 = item 全文移除「結論：xxx」那段後 strip

### 人員子類分類

依關鍵字字典命中第一個就 break；都沒中歸「其他」。順序：離職 → 資遣 → 調整 → 其他。

```python
PERSONNEL_KEYWORDS = {
    "離職": ["離職", "請辭", "辭職", "自請"],
    "資遣": ["資遣", "解雇", "解僱", "終止勞動契約", "開除"],
    "調整": [
        "異動", "調整", "調職", "調班", "轉部門",
        "升職", "升遷", "升任", "升上",
        "降職", "降下", "調上", "調下", "下調", "下放",
        "轉上", "轉下",
    ],
}
```

注意：「調整」**不**包含單字「上」「下」（會誤判「上週」「下個月」），改用 2 字組合表達升降語意。

### 議題長度

**不做截斷、不做精簡**。原文照貼。理由：local Python 沒有 abstractive 改寫能力，硬截斷會丟資訊；user 同意原文輸出。

## 元件設計：`mynotes/ai_local.py`

四個 pure function（接純字串、回純字串/list/dict，沒有 Flask / SQLAlchemy 依賴 — 方便 unit test）：

```python
SECTION_HEADER_RE = re.compile(...)  # 店務/人員 標頭
ITEM_START_RE = re.compile(...)       # 項目起始 pattern
CONCLUSION_RE = re.compile(...)       # 結論抽取
PERSONNEL_KEYWORDS = {...}            # 人員子類關鍵字表

def _split_sections(text: str) -> dict[str, str]:
    """切『店務』『人員』兩個 section。回傳 {section_name: section_body_text}。
    沒偵測到的 section 不在 dict 裡（不是空字串）。"""

def _split_items(section_body: str) -> list[str]:
    """切項目。回傳 list of item texts (含結論詞、不含項目編號)。"""

def _extract_conclusion(item_text: str) -> tuple[str, str | None]:
    """抽結論。回傳 (議題文字, 結論文字 or None)。"""

def _classify_personnel(item_text: str) -> str:
    """判斷人員小類。回傳 '離職' / '資遣' / '調整' / '其他'。"""

def summarize_meeting(title: str, content: str) -> str:
    """主入口：title + content → markdown 摘要字串。"""
```

`summarize_meeting` 的責任是組合上述四個 helper 並 format markdown 輸出（含【摘要開始】/【摘要結束】marker）。

## 修動 `mynotes/routes.py`

### `summarize` route

```python
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
```

不再帶 `prompt` 參數（local pipeline 不需要）。

### `_run_summary`

走 threadpool 包 CPU 部份 — 跟 `notes._run_local_summary` 同一個防 worker recycle 模式。雖然 mynotes parser 是純 regex（很快），仍建議用 threadpool 以求架構一致與 future-proof（萬一未來引入 sentence-transformers）。

```python
def _run_summary(task_id, app, note_id):
    with app.app_context():
        try:
            n = PrivateNote.query.get(note_id)
            if not n:
                _ai_tasks[task_id] = {"status": "error", "message": "筆記不存在"}
                return
            from mynotes.ai_local import summarize_meeting
            import gevent
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
            _logging_mod.getLogger("mynotes_ai_summary").exception("[mynotes] local pipeline failed")
            _ai_tasks[task_id] = {"status": "error", "message": f"本地摘要失敗：{e}"}
```

- 不再 import `call_llm`（雲端 LLM 路徑徹底切掉）
- DB 更新仍寫 `n.ai_summary`（前端顯示路徑不變）
- 整段保留在 `with app.app_context()` 內，因為 `n.title` / `n.content` 是 `EncryptedText` column，需要 app context 才能解密

### `get_ai_task` 補 race grace window

跟 `notes/routes.py` 5/7 修法同步：

```python
_AI_TASK_GRACE_SEC = 30

@mynotes_bp.route("/api/ai/task/<task_id>", methods=["GET"])
@login_required
def get_ai_task(task_id):
    now = time.time()
    expired = [tid for tid, t in _ai_tasks.items() if t.get("delete_at", float("inf")) < now]
    for tid in expired:
        _ai_tasks.pop(tid, None)

    task = _ai_tasks.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "任務不存在"}), 404

    if task["status"] in ("done", "error") and "delete_at" not in task:
        task["delete_at"] = now + _AI_TASK_GRACE_SEC

    if task["status"] == "done":
        return jsonify({"status": "done", "result": task["result"]})
    if task["status"] == "error":
        return jsonify({"status": "error", "message": task["message"]})
    return jsonify({"status": "processing"})
```

## 編輯器 Tab → 4 空白

### 範圍

只攔 `templates/mynotes/editor.html` 的 **content textarea**（會議紀錄主體）。title / checklist 不改（單行短文字、不需要縮排）。

### 邏輯

```javascript
contentTextarea.addEventListener('keydown', (e) => {
  if (e.key === 'Tab' && !e.shiftKey) {
    e.preventDefault();
    const start = e.target.selectionStart;
    const end = e.target.selectionEnd;
    const value = e.target.value;
    e.target.value = value.slice(0, start) + '    ' + value.slice(end);
    e.target.selectionStart = e.target.selectionEnd = start + 4;
    // 觸發 input 事件讓 auto-save 等聽眾收到
    e.target.dispatchEvent(new Event('input', { bubbles: true }));
  }
});
```

- Shift+Tab 反縮排：**不做**（YAGNI；user 沒提）
- 多行選取 + Tab 整段縮排：**不做**（YAGNI）
- 純插 4 個半形空白（不是 `\t`，避免不同字型對齊不一致）

## 模組隔離（feedback_module_isolation）

- 新檔 `mynotes/ai_local.py` 完全獨立，**不** import `notes/ai_local.py`（不同 input shape，混用會誘導未來改動互相牽連）
- `_ai_tasks` 維持 `mynotes/routes.py` module-local（不跟 `notes` 共用 dict，避免 task_id 衝突可能性）
- URL prefix 維持 `/mynotes/api/...`（不動）
- 不修改 `notes/` 任何檔案

## 錯誤處理

| 情境 | 行為 |
|-----|------|
| `n.note_type != "meeting"` | 400 「僅會議紀錄可摘要」（不變） |
| 找不到「店務」「人員」section | 輸出「⚠️ 未偵測到分類，請在會議紀錄加上『店務:』『人員:』標題」 |
| 一個 section 有 header 但 0 個 item | section 整個不輸出（避免空標題） |
| 結論文字為空字串 | 當作無結論（顯示「（無結論）」for 店務、不顯示 for 人員） |
| threadpool 內 raise | 寫 `_ai_tasks[task_id] = {"status": "error", ...}`，前端 `get_ai_task` 回 error message |
| `PrivateNote.query.get(note_id)` 回 None | 寫 error 「筆記不存在」 |

## 測試

### `tests/test_mynotes_ai_local.py`

純函數 unit test，**不需要 Flask / DB fixture**：

- `test_split_sections_basic` — 兩個 section 都有
- `test_split_sections_only_one` — 只有店務 / 只有人員
- `test_split_sections_neither` — 都沒有
- `test_split_sections_format_variants` — `:` / `：` / `【店務】`
- `test_split_items_numbered` — 數字+. / 數字+、/ (1) / 1)
- `test_split_items_bullets` — `- ` / `* ` / `● ` / `▪ `
- `test_split_items_multiline` — 跨行項目（每行不另起 item）
- `test_extract_conclusion_simple` — 「結論：xxx」
- `test_extract_conclusion_synonyms` — 決定 / 決議
- `test_extract_conclusion_full_width_colon` — 全形冒號
- `test_extract_conclusion_multiple_takes_last` — 多個結論詞取最後
- `test_extract_conclusion_none` — 沒結論詞
- `test_classify_personnel_*` — 4 小類各 2-3 個 keyword 樣本
- `test_classify_personnel_misc` — 都沒命中歸「其他」
- `test_summarize_meeting_e2e` — 用本 spec「Parser 規則」前面那段 input/output 範例做完整管線比對：

  Input：

  ```
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
  ```

  Expected output（包 `【摘要開始】` / `【摘要結束】` marker，順序：店務 → 人員 → 子類順序固定）：

  ```
  # 會議摘要

  【摘要開始】

  🏪 店務
  1. 外牆修補, 廠商報價 8 萬太貴, 先問其他家
     → 請小張下週前找 3 家報價
  2. 客訴：上週六飲料品質有問題, 已退費道歉
     → 本週六開檢討會
  3. 庫存不足, 紙杯只剩 2 箱
     （無結論）
  4. 收銀機卡紙
     （無結論）

  👥 人員
  ### 離職
  1. 小李提離職, 5/15 生效

  ### 資遣
  1. 阿華因業績不佳資遣, 月底前

  ### 調整
  1. 王經理升上副總, 6/1 生效

  ### 其他
  1. 店長 5/12-14 請假

  【摘要結束】
  ```
- `test_summarize_meeting_no_sections` — 沒 section header 時的 ⚠️ 提示
- `test_summarize_meeting_personnel_no_conclusion_label_omitted` — 人員小類項目沒結論時**不**顯示「（無結論）」

### `tests/test_mynotes_summarize_route.py`

整合 test，仿 `tests/test_ai_summary_threadpool.py` 模式：

- `test_summarize_route_dispatches_via_threadpool` — mock `gevent.get_hub` 驗證 `summarize_meeting` 透過 threadpool 派送
- `test_summarize_route_rejects_non_meeting` — `note_type="checklist"` 回 400
- `test_summarize_route_writes_ai_summary_field` — task done 後 `n.ai_summary` 有值
- `test_summarize_route_handles_pipeline_exception` — pipeline raise 時寫 error status

### `tests/test_mynotes_ai_task_race.py`

仿 `tests/test_ai_task_race.py` 模式驗 `mynotes/api/ai/task/<id>`：

- `test_done_task_repeatable_within_grace_window`
- `test_error_task_repeatable_within_grace_window`
- `test_done_task_swept_after_grace`
- `test_processing_task_never_marked_for_deletion`

### 編輯器 Tab 鍵不寫 unit test

純前端 keydown handler，需要 jsdom 或 selenium，目前 repo 沒設這套。改人工驗（user 實機按 Tab 確認插 4 空白且不跳焦）。

## 部署計畫

按 `feedback_branch_workflow`：

1. 開 worktree `feat-mynotes-summary-local` (branch `feat/mynotes-summary-local`)
2. 跑完所有 unit test 全綠
3. 切兩個 commit：
   - `feat(mynotes/ai-summary): replace LLM with local Python parser` — `ai_local.py` + `routes.py` 改動 + race fix + 對應 tests
   - `feat(mynotes/editor): Tab key inserts 4 spaces for outline alignment` — 編輯器 UX 獨立 commit（萬一要 revert 不影響 AI 摘要）
4. push branch → workflow_dispatch 觸發 GHA 或直接 merge main 後 push
5. Zeabur Redeploy → user 進「指令」shell 進去 `grep "summarize_meeting" /app/mynotes/ai_local.py` 驗版本
6. user 實機驗證：
   - 按 mynotes meeting 摘要 → 出現 Format C 結構化輸出
   - 連按 5 次都沒「任務不存在」
   - 編輯器 Tab 鍵不跳焦、插 4 空白
7. 驗 OK 才 merge `feat/mynotes-summary-local` 回 main

## Trade-offs / Open questions

1. **議題太長但沒寫結論的項目，視覺上仍只顯示原文一行**：原文若 100 字，顯示就是 100 字一行，前端會自動換行但失去「重點感」。Spec review 時若 user 想加截斷規則，加 `_TITLE_MAX_CHARS` 常數即可（目前不做）。
2. **「上」「下」沒做單字 keyword**：若 user 強烈要求單字 match，要加白名單例外（「上週」「下週」「上個月」「下個月」「上班」「下班」「上面」「下面」等不算「調整」）。Spec 標 open question，user 可在 review 時改。
3. **「人員」section 沒寫小類關鍵字的項目歸「其他」**：但「其他」可能變大宗（user 不一定每次都用「離職」這個詞），導致小類分類效果有限。後續若觀察使用者實際輸入習慣，可加更多 alias。
4. **`_ai_tasks` 仍是 module-level dict**：跟 `notes` 一樣，pod restart 還是會丟 task。`notes` 5/7 用 threadpool 解決 reactor blocking 問題後 pod 不再因 readiness probe 被殺 — `mynotes` parser 是純 regex 不會 block reactor，理論上 dict 比 `notes` 還穩。但若未來真要做 100% durable task，C1 路線（DB 表存 task）仍是後備方案。
5. **舊 LLM prompt 完全切掉**：不像 `notes` 5/6 留 `_run_ai_task` 在 routes.py 供 git revert。理由：`mynotes` LLM 路徑跟 local 路徑共用同個 endpoint URL `/mynotes/api/<id>/summarize`，留 dead code 反而誘導 confusion。要 revert 直接 git revert 那個 commit。

## 變更檔案總覽

| 檔案 | 動作 | 行數預估 |
|------|------|---------|
| `app_unified/mynotes/ai_local.py` | 新檔 | ~150 |
| `app_unified/mynotes/routes.py` | 改 `summarize` / `_run_summary` / `get_ai_task` | -50 / +40 |
| `app_unified/templates/mynotes/editor.html` | content textarea Tab handler | +15 |
| `app_unified/tests/test_mynotes_ai_local.py` | 新檔 | ~250 |
| `app_unified/tests/test_mynotes_summarize_route.py` | 新檔 | ~120 |
| `app_unified/tests/test_mynotes_ai_task_race.py` | 新檔 | ~80 |

合計約 ~700 行（含 test 約 450 行）。
