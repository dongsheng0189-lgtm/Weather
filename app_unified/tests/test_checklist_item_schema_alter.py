"""F11 guard: app.py schema repair has 4 new ALTERs for checklist_items authorship.

If anyone removes or renames these, prod first-deploy schema repair would
silently miss the columns and all writes to those fields would 500 ISE.
"""
from pathlib import Path

APP_PY = Path(__file__).parent.parent / 'app.py'


def test_checklist_items_authorship_alters_present():
    src = APP_PY.read_text(encoding='utf-8')
    assert 'checklist_items' in src
    for col in ['created_by', 'created_by_name', 'updated_by', 'updated_by_name']:
        token = f"ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS {col}"
        assert token in src, f'missing ALTER for checklist_items.{col}'
