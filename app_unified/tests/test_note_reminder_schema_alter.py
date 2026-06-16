"""Guard: app.py schema repair has reminder_date + audit ALTERs for notes + checklist_items.

漏掉任一條 → prod 首次部署 schema repair 靜默缺欄 → 寫入 reminder 欄位 500 ISE。
本機 SQLite 抓不到（feedback_inline_schema_repair）。
"""
from pathlib import Path

APP_PY = Path(__file__).parent.parent / 'app.py'
COLS = ['reminder_date', 'reminder_set_by', 'reminder_set_by_name', 'reminder_set_at']


def test_reminder_alters_present():
    src = APP_PY.read_text(encoding='utf-8')
    for tbl in ('notes', 'checklist_items'):
        for col in COLS:
            token = f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS {col}"
            assert token in src, f'missing ALTER for {tbl}.{col}'
