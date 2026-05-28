"""F11: editor.html renderChecklistItem meta block — 2 rows + (完成) suffix logic."""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_meta_has_two_conditional_rows():
    src = EDITOR.read_text(encoding='utf-8')
    # 建立 row
    assert "'建立: '" in src or '"建立: "' in src
    assert 'item.created_by_name' in src
    # 修改 row + (完成) suffix label
    assert "'修改: '" in src or '"修改: "' in src
    assert '修改(完成): ' in src
    assert 'item.updated_by_name' in src
    # gate for 修改 row — only show when updated_at differs from created_at
    assert "item.updated_at !== item.created_at" in src
    # gate for (完成) suffix — last action was check
    assert "item.checked_at === item.updated_at" in src
    assert 'item.is_checked' in src
    # joined via <br>
    assert "rows.join('<br>')" in src or 'rows.join("<br>")' in src


def test_escape_html_used_for_authorship_names():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'escapeHtml(item.created_by_name)' in src
    assert 'escapeHtml(item.updated_by_name)' in src


def test_old_completion_only_line_removed():
    """The old `✓ 由 X 在 Y 完成` single-row meta should be replaced — `(完成)` is now a suffix on 修改."""
    src = EDITOR.read_text(encoding='utf-8')
    assert "'✓ 由 ' + item.checked_by_name" not in src
    assert "'✓ 由 ' + d.item.checked_by_name" not in src
