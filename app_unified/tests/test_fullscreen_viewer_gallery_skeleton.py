"""Smoke: fullscreen_viewer.js gallery skeleton + backward compat shim."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'
VIEWER_CSS = Path(__file__).parent.parent / 'static' / 'css' / 'fullscreen_viewer.css'


def test_gallery_api_export():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'window.openFullscreenGallery' in src
    assert 'function openFullscreenGallery(' in src


def test_backward_compat_shim_for_openfullscreen():
    """openFullscreen(url, opts) must call gallery with single-item array."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # shim definition
    assert 'openFullscreen = function' in src or 'window.openFullscreen = function' in src
    # shim body should construct a single-item array and delegate
    assert 'openFullscreenGallery([{' in src or 'openFullscreenGallery([' in src


def test_track_and_slide_markup_in_ensuremarkup():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'id="fs-track"' in src or "id='fs-track'" in src
    assert 'class="fs-slide"' in src or "class='fs-slide'" in src


def test_chrome_elements_present():
    """counter + prev + next + download + close + hint all in markup."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    for cls in ['fs-counter', 'fs-prev', 'fs-next', 'fs-close', 'fs-download', 'fs-hint']:
        assert cls in src, f'missing chrome element: {cls}'


def test_css_scroll_snap():
    css = VIEWER_CSS.read_text(encoding='utf-8')
    assert 'scroll-snap-type: x mandatory' in css
    assert 'scroll-snap-align: center' in css
    assert 'scroll-snap-stop: always' in css
    assert 'overscroll-behavior-x: contain' in css


def test_css_arrow_hidden_on_touch():
    css = VIEWER_CSS.read_text(encoding='utf-8')
    assert '@media (hover: none)' in css
    # arrows hidden under hover:none
    assert '.fs-prev' in css and '.fs-next' in css
