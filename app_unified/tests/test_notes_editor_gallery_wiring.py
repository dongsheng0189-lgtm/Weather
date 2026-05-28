"""Wire notes/editor.html callsites to openFullscreenGallery."""
from pathlib import Path

TEMPLATE = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_template_has_togallery_helper():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'function toGalleryItem(' in src
    # download endpoint base used inside helper
    assert "'/notes/api/attachments/'" in src or '"/notes/api/attachments/"' in src
    # constructs downloadHref ending with /download
    assert "'/download'" in src or '"/download"' in src


def test_template_calls_gallery_api_three_times():
    src = TEMPLATE.read_text(encoding='utf-8')
    # 3 callsites: note attachment thumbnail, pending upload preview, checklist item attachment
    assert src.count('openFullscreenGallery(') >= 3


def test_template_uses_new_api_for_known_callsites():
    """Specifically: the 3 known former openFullscreen attachment callsites are gone."""
    src = TEMPLATE.read_text(encoding='utf-8')
    # the bare openFullscreen(att.url ...) / openFullscreen(a.url ...) / openFullscreen(url, { downloadHref: null })
    # patterns should no longer appear (they're replaced by gallery calls or wrappers).
    # NOTE: openFullscreen() may still appear in the backward-compat shim's commentary in the JS file
    # but should be absent from this template.
    assert 'openFullscreen(att.url,' not in src
    assert 'openFullscreen(a.url,' not in src


def test_template_has_open_gallery_for_helper():
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'function openGalleryFor(' in src
    # callsites should use the helper directly (DRY)
    assert src.count('openGalleryFor(') >= 3   # 1 def + 2 calls
