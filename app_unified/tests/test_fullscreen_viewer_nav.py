"""Page nav: scrollend / arrow / keyboard."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_swipe_pointer_handlers():
    """F10 uses our own pointer-driven swipe (no native scrollend); track listens to pointerdown/move/up + transitionend."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert "'pointerdown'" in src and "'pointermove'" in src and "'pointerup'" in src
    # transitionend used to re-center after swipe animation
    assert "'transitionend'" in src
    # native scrollend listener REMOVED with the rewrite
    assert 'addEventListener(\'scrollend\'' not in src


def test_chrome_update_helper():
    """Chrome (counter / download / arrows) updated by _updateChrome — arrows always enabled (circular wrap)."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'function _updateChrome(' in src
    # counter text format
    assert "(_currentIdx + 1) + ' / ' + N" in src or "(_currentIdx + 1) + ' / ' + _items.length" in src
    # circular wrap — arrows always enabled
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
    assert '% N' in goto_section
    # no early-return on out-of-bound (no stop-not-loop)
    assert 'idx >= _items.length' not in goto_section


def test_multi_copy_seamless_wrap():
    """Multi-item gallery renders 3 copies (left/middle/right) and re-centers after swipe to give seamless circular flow."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # 3-copy layout markers
    assert "'l'" in src and "'m'" in src and "'r'" in src
    assert 'data-copy=' in src
    # canonical slot is in the middle copy
    assert 'function _canonicalSlot(' in src
    # re-center after transition ends
    assert 'function _recenter(' in src
    # transitionend triggers recenter
    assert "'transitionend'" in src
