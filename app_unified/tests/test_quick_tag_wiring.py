"""快速標籤鍵的前端接線（2026-09-08）。

同專案 editor.html wiring test 慣例：原始碼層斷言。
"""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_all_store_tags_exposes_pinned():
    """前端要靠 pinned 決定哪些標籤渲染成按鈕 —— 沒傳就什麼都不會出現。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert 'pinned:' in src, 'ALL_STORE_TAGS 沒有帶 pinned 欄位'


def test_tag_meta_maps_exposed():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'window.ITEM_TAG_META_MAP' in src, '缺少項目標籤的作者資訊 map'
    assert 'window.NOTE_TAG_META' in src, '缺少筆記標籤的作者資訊 map'


def test_quick_tag_button_factory_exists():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'function makeQuickTagBtn(' in src, '缺少快速標籤鍵的建構函式'


def test_quick_tag_uses_dedicated_endpoint_not_full_list():
    """必須打單標籤端點。用 PUT .../tags 會覆寫整份清單、兩人同時操作互相蓋掉。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert "'/notes/api/items/' + targetId + '/tags/' + tag.id" in src or \
           '"/notes/api/items/" + targetId + "/tags/" + tag.id' in src, \
        '快速鍵沒有打單標籤端點'


def test_quick_tag_rendered_for_pinned_only():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'return t.pinned;' in src, '沒有依 pinned 過濾 —— 會把所有標籤都變成按鈕'


def test_quick_tag_shows_authorship_on_hover():
    """hover 顯示「誰在什麼時候掛的」。

    斷言必須落在 makeQuickTagBtn 的定義區塊內 —— 只檢查整檔有沒有出現
    'ITEM_TAG_META_MAP' / 'title' 會偽陽性（前者是上個任務注入的、後者
    整份模板到處都是），那樣的測試在功能實作前就會通過、對迴歸沒有偵測力。
    """
    src = EDITOR.read_text(encoding='utf-8')
    start = src.index('function makeQuickTagBtn(')
    block = src[start:start + 2500]
    assert '_quickTagMeta(' in block, 'makeQuickTagBtn 沒有讀取作者資訊'
    assert 'btn.title' in block, '作者資訊沒有放進 title（hover 提示）'


def test_toggle_repaints_chip_row():
    """toggle 成功後要同步重繪 chip 列。

    少了這一步，按鈕會變成 ✓ 但上方的 chip 列停在舊狀態（那排是 render 當下
    建好的靜態 DOM），要等下次整批重繪才校正。
    """
    src = EDITOR.read_text(encoding='utf-8')
    assert 'function paintItemTagChips(' in src, '缺少 chip 列重繪函式'
    start = src.index('function makeQuickTagBtn(')
    rest = src[start + len('function makeQuickTagBtn('):]
    nxt = rest.find('\nfunction ')
    block = rest if nxt == -1 else rest[:nxt]
    assert 'paintItemTagChips(' in block, 'toggle 成功後沒有重繪 chip 列'


def test_empty_tag_container_is_hidden():
    """容器一律建立，但空的時候要隱藏，否則 0 標籤的項目會多出 4px 空隙。"""
    from pathlib import Path
    css = (Path(__file__).parent.parent / 'static' / 'css' / 'tag_system.css').read_text(encoding='utf-8')
    assert '.item-tags:empty' in css, '空的標籤容器沒有隱藏，會佔掉 margin-top 的空間'


def test_note_level_quick_tag_container_exists():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'id="note-quick-tags"' in src, '工具列缺少筆記層快速鍵的容器'


def test_note_level_quick_tags_are_rendered():
    src = EDITOR.read_text(encoding='utf-8')
    assert "makeQuickTagBtn('note'" in src, '筆記層沒有重用 makeQuickTagBtn'


def test_note_quick_tags_only_when_note_exists():
    """新建但還沒存的筆記沒有 id，不能渲染（會打到 /notes/api/undefined/tags/...）。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert 'if (!NOTE_ID) return;' in src or 'if (NOTE_ID)' in src, \
        '沒有擋住「筆記尚未建立」的情形'


def test_note_quick_tags_wait_for_dom_ready():
    """筆記層的渲染必須等 DOMContentLoaded。

    ALL_STORE_TAGS / NOTE_TAG_IDS / NOTE_TAG_META 是在後面另一個 <script>
    區塊才賦值的。若在前一個區塊直接裸呼叫 renderNoteQuickTags()，執行時那些
    全域變數還是 undefined，函式開頭的 guard 會 return、按鈕永遠不出現 ——
    而且測試會全綠，只有在瀏覽器上才看得出來。
    """
    src = EDITOR.read_text(encoding='utf-8')
    assert "addEventListener('DOMContentLoaded', renderNoteQuickTags)" in src, \
        'renderNoteQuickTags 沒有等 DOMContentLoaded，按鈕會因為腳本執行順序而永遠不渲染'
