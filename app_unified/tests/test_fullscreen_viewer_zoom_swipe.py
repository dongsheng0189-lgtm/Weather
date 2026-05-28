"""Pinch zoom + swipe coexistence (transform-based track, no scroll-snap)."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_pointer_handlers_per_slide():
    """Pointer handlers bind to current slide's fs-stage (image only), not global."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'function _bindPinchHandlersToSlide(' in src
    # called from openFullscreenGallery + _goTo
    occurrences = src.count('_bindPinchHandlersToSlide')
    assert occurrences >= 3, f'expected >=3 references, got {occurrences}'


def test_pinch_stops_propagation_to_track_swipe():
    """When pinching (2 pointers) or panning a zoomed image, stage handler MUST stopPropagation so the track swipe handler doesn't also engage."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    move_section = src.split('function _fsOnPointerMove')[1].split('function ')[0]
    # 2-pointer pinch branch + 1-pointer zoomed-pan branch both stopPropagation
    assert move_section.count('e.stopPropagation()') >= 2


def test_track_swipe_bails_when_zoomed():
    """Track swipe handler must NOT engage when scale > 1.01 (pinch/pan owns the gesture)."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    down_section = src.split('function _onTrackPointerDown')[1].split('function ')[0]
    assert '_fsState.scale > 1.01' in down_section
