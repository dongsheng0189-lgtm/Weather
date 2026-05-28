"""Announcement attachment gallery wiring."""
from pathlib import Path

JS_FILE = Path(__file__).parent.parent / 'static' / 'js' / 'announcement_attachments.js'
EDIT_TEMPLATE = Path(__file__).parent.parent / 'templates' / 'announcements' / 'edit.html'
CARD_TEMPLATE = Path(__file__).parent.parent / 'templates' / 'announcements' / '_card_list.html'


def test_rendermedia_takes_siblings():
    src = JS_FILE.read_text(encoding='utf-8')
    assert 'function _renderMedia(att, siblings)' in src


def test_helpers_present():
    src = JS_FILE.read_text(encoding='utf-8')
    assert 'function _toAnnGalleryItem(' in src
    assert 'function _openAnnGalleryFor(' in src
    # endpoint for announcement attachment downloads
    assert "'/notes/announcement/api/attachments/'" in src or '"/notes/announcement/api/attachments/"' in src


def test_image_onclick_uses_gallery():
    src = JS_FILE.read_text(encoding='utf-8')
    # the click handler routes to gallery helper
    assert '_openAnnGalleryFor(att, siblings)' in src
    # old openFullscreen single-arg call is gone
    assert 'openFullscreen(att.url' not in src


def test_caller_signatures_passing_siblings():
    """createAnnAttItem(att, siblings) + renderAnnAttView(att, siblings) signatures."""
    src = JS_FILE.read_text(encoding='utf-8')
    assert 'function createAnnAttItem(att, siblings)' in src
    assert 'function renderAnnAttView(att, siblings)' in src


def test_external_templates_pass_sibling_list():
    edit = EDIT_TEMPLATE.read_text(encoding='utf-8')
    card = CARD_TEMPLATE.read_text(encoding='utf-8')
    # edit.html caller passes d.attachments
    assert 'createAnnAttItem(att, d.attachments)' in edit
    # _card_list.html caller passes d.attachments
    assert 'renderAnnAttView(att, d.attachments)' in card
