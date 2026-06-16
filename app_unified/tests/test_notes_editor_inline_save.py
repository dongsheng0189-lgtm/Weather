"""Wiring assertions for notes editor 就地儲存 + 未存指示器 (2026-06-16).

純前端行為（dirty state machine + 返回攔截 + beforeunload + 新筆記 replaceState），
靠檔案內容斷言確認接線沒掉，瀏覽器互動另行手動驗證。
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent
EDITOR = ROOT / 'templates' / 'notes' / 'editor.html'
TAG_SYSTEM = ROOT / 'static' / 'js' / 'tag_system.js'


def test_editor_has_dirty_state_machine():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'function markDirty' in src
    assert 'function setSaved' in src
    assert 'window.markEditorDirty' in src
    assert '_leavingIntentionally' in src


def test_editor_has_unsaved_guard_and_beforeunload():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'beforeunload' in src
    # 返回攔截的自訂中文 confirm
    assert '尚未儲存變更，確定離開？' in src


def test_editor_new_note_replaces_url_in_place():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'history.replaceState' in src


def _dosave_body(src):
    """擷取 doSave() 函式 body（到下一個 top-level `function ` 宣告為止）。
    doDelete 仍合理保留 redirect 回列表，故只檢查 doSave 範圍。"""
    start = src.index('function doSave()')
    rest = src[start + len('function doSave()'):]
    nxt = rest.find('\nfunction ')
    return rest if nxt == -1 else rest[:nxt]


def test_editor_save_no_longer_redirects_to_list():
    """doSave 成功後留在編輯器：移除原本兩處 redirect 回列表的跳轉。"""
    src = EDITOR.read_text(encoding='utf-8')
    body = _dosave_body(src)
    # doSave 內不該再有「存檔成功 → 跳列表」這行
    assert 'window.location.href = NOTES_INDEX_URL' not in body
    # 改成留在編輯器標記已儲存
    assert 'setSaved();' in body


def test_tag_system_marks_editor_dirty_on_apply():
    src = TAG_SYSTEM.read_text(encoding='utf-8')
    assert 'markEditorDirty' in src
