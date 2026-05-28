"""Lazy load: data-src → src for current ±1 only on open + on page change."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_slides_use_data_src_not_src():
    """Initial slide markup uses data-src; real src is set lazily (no eager src on fs-img)."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'data-src="' in src
    assert "<img class=\"fs-img\" data-src=" in src or "<img class='fs-img' data-src=" in src
    # negative guard: fs-img must not carry an eager src attribute in the slide builder
    assert 'class="fs-img" src=' not in src and "class='fs-img' src=" not in src


def test_ensureloaded_called_for_three_indices_on_navigation():
    """_goTo triggers _ensureLoaded(realIdx) + _ensureLoaded((realIdx-1+N)%N) + _ensureLoaded((realIdx+1)%N) (wraps)."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    goto_section = src.split('function _goTo(')[1].split('function ')[0]
    assert goto_section.count('_ensureLoaded') >= 3


def test_ensureloaded_swaps_dataset_src_to_src():
    src = VIEWER_JS.read_text(encoding='utf-8')
    fn_section = src.split('function _ensureLoaded')[1].split('function ')[0]
    assert 'dataset.src' in fn_section
    assert '.src = ' in fn_section or '.src=' in fn_section
