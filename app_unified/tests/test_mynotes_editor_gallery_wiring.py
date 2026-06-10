"""Mynotes editor: swap inline fullscreen viewer for shared gallery."""
from pathlib import Path

TEMPLATE = Path(__file__).parent.parent / 'templates' / 'mynotes' / 'editor.html'


def test_template_includes_shared_viewer_assets():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'fullscreen_viewer.js' in src
    assert 'fullscreen_viewer.css' in src


def test_template_uses_shared_gallery_api():
    src = TEMPLATE.read_text(encoding='utf-8')
    # mynotes now uses the shared DOM-driven API (F10 cleanup)
    assert 'markGalleryItem(' in src
    assert 'openGalleryFromEl(' in src
    # old per-template helpers removed
    assert 'function _toMyGalleryItem(' not in src
    assert 'function _openMyGalleryFor(' not in src
    # mynotes is private — MUST NOT build any /download endpoint URL
    assert "'/download'" not in src and '"/download"' not in src


def test_template_no_inline_fs_overlay():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'id="fs-overlay"' not in src
    assert "id='fs-overlay'" not in src
    assert 'function openFs(' not in src
    assert 'function closeFs(' not in src


def test_three_callsites_use_gallery():
    src = TEMPLATE.read_text(encoding='utf-8')
    # 3 image callsites (note att list, checklist-item view, item dialog) route
    # through the shared openGalleryFromEl()
    assert src.count('openGalleryFromEl(') >= 3
