"""Wiring: 通用浮動返回鈕（取代 review-back-widget）。"""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_float_back_widget_present():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'id="float-back-widget"' in src


def test_float_back_mirrors_btn_back_and_uses_observer():
    src = EDITOR.read_text(encoding='utf-8')
    assert "getElementById('btn-back')" in src
    assert 'IntersectionObserver' in src


def test_old_review_back_widget_removed():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'review-back-widget' not in src


def test_review_mode_label_preserved():
    src = EDITOR.read_text(encoding='utf-8')
    assert '← 返回摘要' in src


def test_float_back_dirty_label_present():
    src = EDITOR.read_text(encoding='utf-8')
    assert '💾 儲存並返回' in src


def test_float_back_saves_then_navigates_when_dirty():
    """dirty 點浮動鈕 → doSave() 成功才導頁（取代放棄變更 confirm）。"""
    src = EDITOR.read_text(encoding='utf-8')
    assert 'doSave().then(' in src


def test_unsaved_guard_only_on_btn_back_not_float():
    """_guardLeave 的 confirm 只掛在 #btn-back；浮動鈕改走存後導頁、不再掛 guard。"""
    src = EDITOR.read_text(encoding='utf-8')
    # 浮動鈕不再以 capture 監聽 _guardLeave
    assert "floatBack.addEventListener('click', _guardLeave, true)" not in src
    # btn-back 仍掛 guard
    assert "backBtn.addEventListener('click', _guardLeave)" in src
