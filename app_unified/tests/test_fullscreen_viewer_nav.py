"""Page nav: scrollend / arrow / keyboard."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_scrollend_listener():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'scrollend' in src
    assert "'onscrollend' in window" in src or 'onscrollend in window' in src
    # debounced scroll fallback
    assert 'addEventListener' in src and "'scroll'" in src


def test_onpagechange_updates_chrome():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'function _onPageChange(' in src
    # counter text format
    assert "(idx + 1) + ' / ' + _items.length" in src or "(newIdx + 1) + ' / ' + _items.length" in src
    # arrow disabled state via id lookups or local vars
    assert ".disabled = (" in src
    assert 'fs-prev' in src and 'fs-next' in src


def test_arrow_button_click_handlers():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert "fs-prev" in src
    assert "fs-next" in src
    assert 'function _goTo(' in src


def test_keyboard_handlers():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert "'keydown'" in src
    assert "'ArrowLeft'" in src
    assert "'ArrowRight'" in src
    assert "'Escape'" in src


def test_boundary_stop_not_loop():
    """_goTo should return early on out-of-bound, not wrap."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # the _goTo body has early-return for boundary
    goto_section = src.split('function _goTo(')[1].split('function ')[0]
    assert 'idx < 0' in goto_section
    assert 'idx >= _items.length' in goto_section
