"""Wire notes/editor.html callsites to the shared DOM-driven gallery API (F10 cleanup)."""
from pathlib import Path

TEMPLATE = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_template_has_notes_download_href_helper():
    src = TEMPLATE.read_text(encoding='utf-8')
    # notes download href is built by a local helper (passed into markGalleryItem)
    assert 'function _notesDownloadHref(' in src
    assert "'/notes/api/attachments/'" in src or '"/notes/api/attachments/"' in src
    # constructs downloadHref ending with /download
    assert "'/download'" in src or '"/download"' in src


def test_template_opens_gallery_from_el_three_callsites():
    src = TEMPLATE.read_text(encoding='utf-8')
    # 3 image callsites route through the shared openGalleryFromEl():
    # note attachment thumbnail, pending upload preview, checklist item attachment
    assert src.count('openGalleryFromEl(') >= 3


def test_template_uses_new_api_for_known_callsites():
    """The former per-template helpers and bare openFullscreen callsites are gone."""
    src = TEMPLATE.read_text(encoding='utf-8')
    assert 'openFullscreen(att.url,' not in src
    assert 'openFullscreen(a.url,' not in src
    # old duplicated helpers replaced by shared window.markGalleryItem / openGalleryFromEl
    assert 'function toGalleryItem(' not in src
    assert 'function openGalleryFor(' not in src


def test_template_marks_gallery_items_and_groups():
    src = TEMPLATE.read_text(encoding='utf-8')
    # each media callsite tags its element via the shared markGalleryItem()
    assert src.count('markGalleryItem(') >= 3
    # containers mark the swipe scope so openGalleryFromEl rebuilds the list from the DOM
    assert 'data-gallery-group' in src
