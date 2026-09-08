"""「儲存並返回」的兩個漏存 race（2026-09-08 修）。

背景見 notes-save-return-silent-dataloss-risks。2026-07-10 已補上
`ensureNoteExists()` 的 in-flight 去重，但還有兩個缺口沒堵：

**缺口 A —— 兩條路徑各建各的筆記。**
`doSave()` 用自己的 POST /notes/api 建筆記，**沒有**走 `_ensureNotePromise`。
所以「項目 modal 存檔中（它走 ensureNoteExists）」時按下「儲存並返回」，
會建出**第二篇**筆記：項目掛在第一篇，畫面卻停在第二篇（空的）。

**缺口 B —— 跳頁 abort 掉還沒寫完的請求。**
`doSave()` resolve 後立刻 `window.location.href`，而項目 modal 的儲存是
**獨立的請求鏈**，doSave 等不到它。跳頁會 abort 進行中的 fetch →
項目從未寫入資料庫，而且畫面已經跳走、沒有任何錯誤訊息。

純接線斷言（同專案 editor.html wiring test 慣例；瀏覽器實際併發行為另行手動驗）。
"""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def _fn_body(src, header):
    """擷取某函式 body（到下一個 top-level `function ` 為止）。"""
    start = src.index(header)
    rest = src[start + len(header):]
    nxt = rest.find('\nfunction ')
    return rest if nxt == -1 else rest[:nxt]


# ── 缺口 A：doSave 必須跟 ensureNoteExists 共用同一把鎖 ──
def test_do_save_waits_for_inflight_note_creation():
    """已有人在建筆記時，doSave 不可再 POST 一篇，要接同一個 promise。"""
    body = _fn_body(EDITOR.read_text(encoding='utf-8'), 'function doSave()')
    assert '_ensureNotePromise' in body, \
        'doSave 完全沒碰 in-flight 鎖 —— 會與項目 modal 各建一篇筆記'
    assert 'if (!NOTE_ID && _ensureNotePromise)' in body, \
        'doSave 沒有「已有人在建就等他」的分支'


def test_do_save_publishes_its_own_creation_into_the_lock():
    """反向也要成立：doSave 自己在建筆記時，項目 modal 必須等它，不能另建一篇。"""
    body = _fn_body(EDITOR.read_text(encoding='utf-8'), 'function doSave()')
    assert '_ensureNotePromise = new Promise' in body, \
        'doSave 建立筆記時沒有把自己登記進 in-flight 鎖'
    assert '_publishCreated' in body, \
        '缺少「NOTE_ID 設定完成」的通知點，等待方會一直卡著'


# ── 缺口 B：跳頁前要等 in-flight 寫入落地 ──
def test_inflight_write_registry_exists():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'function trackWrite(' in src, '缺少 in-flight 寫入登記器'
    assert 'function waitForInflightWrites(' in src, '缺少等待 in-flight 寫入的函式'


def test_item_save_is_registered_as_inflight_write():
    """項目 modal 的儲存鏈要登記，否則跳頁時會被 abort。"""
    body = _fn_body(EDITOR.read_text(encoding='utf-8'),
                    'function saveItemFromDialog()')
    assert 'trackWrite(' in body, \
        'saveItemFromDialog 沒有登記為 in-flight 寫入 —— 跳頁會把它 abort'


def test_save_and_return_awaits_inflight_writes_before_navigating():
    """「儲存並返回」必須先等寫入落地才 window.location.href。"""
    src = EDITOR.read_text(encoding='utf-8')
    i = src.index("fb.addEventListener('click'")
    handler = src[i:i + 1600]
    assert 'waitForInflightWrites()' in handler, \
        '儲存並返回沒有等 in-flight 寫入，跳頁會 abort 它們'
    # 導航必須在等待之後（出現在 waitForInflightWrites 之後）
    assert handler.index('waitForInflightWrites()') < \
        handler.index("window.location.href = dest", handler.index('doSave()')), \
        '導航仍然排在等待之前，等於沒等'


def test_failed_save_still_does_not_navigate():
    """回歸防護：存檔失敗時仍應留在頁面（原有行為不可被這次改動弄掉）。"""
    src = EDITOR.read_text(encoding='utf-8')
    i = src.index("fb.addEventListener('click'")
    handler = src[i:i + 1600]
    assert '_refreshFloatBack' in handler, '失敗時沒有還原按鈕標籤'
    assert 'if (!ok)' in handler or 'else if' in handler, \
        '失敗分支不見了 —— 失敗也會跳頁就等於漏存'
