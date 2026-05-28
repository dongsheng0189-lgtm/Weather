"""Pinch zoom + native swipe coexistence."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_pointer_handlers_per_slide():
    """Pointer handlers should bind to current slide's fs-stage, not global."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'function _bindPinchHandlersToSlide(' in src
    # called from _onPageChange + openFullscreenGallery
    occurrences = src.count('_bindPinchHandlersToSlide')
    assert occurrences >= 3, f'expected >=3 references, got {occurrences}'


def test_single_pointer_scale1_does_not_preventdefault():
    """Single pointer + scale===1 must let native scroll-snap handle the swipe."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # explicit scale === 1 / <= 1.01 detection
    assert '_fsState.scale <= 1.01' in src or '_fsState.scale === 1' in src or '_fsState.scale > 1.01' in src or 'scale <= 1.01' in src


def test_track_overflow_locked_on_zoom():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert "overflowX = 'hidden'" in src or 'overflowX = "hidden"' in src
    assert "overflowX = 'auto'" in src or 'overflowX = "auto"' in src


def test_fsreset_restores_overflow():
    src = VIEWER_JS.read_text(encoding='utf-8')
    reset_section = src.split('function _fsReset')[1].split('function ')[0]
    assert 'overflowX' in reset_section
