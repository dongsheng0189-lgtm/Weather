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
    # counter text format (allow either ' / ' + N or ' / ' + _items.length)
    assert "(newIdx + 1) + ' / ' + N" in src or "(newIdx + 1) + ' / ' + _items.length" in src
    # arrows always reset to enabled state (circular wrap — never disabled)
    assert 'fs-prev' in src and 'fs-next' in src
    assert 'prevBtn.disabled = false' in src and 'nextBtn.disabled = false' in src


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


def test_boundary_wraps_circular():
    """_goTo wraps via modulo — last+1 → first, first-1 → last."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    goto_section = src.split('function _goTo(')[1].split('function ')[0]
    # circular modulo wrap pattern
    assert '((realIdx % N) + N) % N' in goto_section or '% N' in goto_section
    # no longer early-returns out-of-bound (must not match the old stop-not-loop pattern)
    assert 'idx >= _items.length' not in goto_section


def test_clone_wrap_in_scrollend_handler():
    """scrollend instant-jumps when landing on a left/right clone slide."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # clones marked via data-clone="left"/"right" in markup
    assert "data-clone=\"left\"" in src or "'left'" in src
    assert "data-clone=\"right\"" in src or "'right'" in src
    # instant scrollLeft jump for wrap (no behavior:'smooth')
    settled = src.split('function _onScrollSettled')[1].split('function ')[0]
    assert 'track.scrollLeft' in settled
    # both wrap branches
    assert 'domIdx === 0' in settled
    assert 'domIdx === N + 1' in settled
