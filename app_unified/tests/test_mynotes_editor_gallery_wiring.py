"""Mynotes editor: swap inline fullscreen viewer for shared gallery."""
from pathlib import Path

TEMPLATE = Path(__file__).parent.parent / 'templates' / 'mynotes' / 'editor.html'


def test_template_includes_shared_viewer_assets():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'fullscreen_viewer.js' in src
    assert 'fullscreen_viewer.css' in src


def test_template_has_mynotes_gallery_helpers():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'function _toMyGalleryItem(' in src
    assert 'function _openMyGalleryFor(' in src
    # mynotes is private — no download
    assert 'downloadHref: null' in src
    # MUST NOT build any /download endpoint URL in mynotes
    assert "'/download'" not in src and '"/download"' not in src


def test_template_no_inline_fs_overlay():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'id="fs-overlay"' not in src
    assert "id='fs-overlay'" not in src
    assert 'function openFs(' not in src
    assert 'function closeFs(' not in src


def test_three_callsites_use_gallery():
    src = TEMPLATE.read_text(encoding='utf-8')
    # 3 original openFs(a.url) callsites should now use _openMyGalleryFor
    assert src.count('_openMyGalleryFor(') >= 3
