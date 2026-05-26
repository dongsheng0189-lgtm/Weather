"""ai_local pipeline link-related invariants：bucket dicts 攜帶 id 給
_format_cluster 用來組 markdown link。
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from notes.ai_local import _categorize_notes, _categorize_checklists, _format_cluster


def _make_note(id, title="t", content="c", category="misc", status="pending",
               resolved_at=None, updated_at=None, author_name="A", note_type="note"):
    """SQLAlchemy ORM 等價的輕量替身。author 用 SimpleNamespace 模擬 .username。"""
    if updated_at is None:
        updated_at = datetime(2026, 5, 26, 12, 0, 0)
    return SimpleNamespace(
        id=id, title=title, content=content, category=category, status=status,
        resolved_at=resolved_at, updated_at=updated_at, author_name=author_name,
        author=SimpleNamespace(username=author_name), note_type=note_type,
        checklist_items=[],
    )


def test_categorize_notes_includes_note_id():
    """Bucket item 必須帶 note_id 給後續 _format_cluster 組 link。"""
    n = _make_note(id=42, title="冰箱漏水", category="machine", status="pending")
    buckets = _categorize_notes([n], since=None, until=None)
    items = buckets["machine"]["undone"]
    assert len(items) == 1
    assert items[0]["note_id"] == 42


def test_categorize_checklists_includes_note_id_and_item_id():
    """Checklist bucket item 必須帶 note_id (跳到 note) + item_id (anchor 滾到該 item)。"""
    item = SimpleNamespace(
        id=99, text="檢查冰箱", is_checked=False, checked_at=None,
        updated_at=datetime(2026, 5, 26, 12, 0, 0), checked_by_name=None,
    )
    n = _make_note(id=42, title="日檢清單", note_type="checklist", category="machine")
    n.checklist_items = [item]

    buckets = _categorize_checklists([n], since=None, until=None)
    items = buckets["machine"]["undone"]
    assert len(items) == 1
    assert items[0]["note_id"] == 42
    assert items[0]["item_id"] == 99


def test_format_cluster_single_note_emits_markdown_link():
    cluster = [{
        "note_id": 42,
        "title": "冰箱漏水",
        "content": "已約23號開刀",
        "author": "婉婷",
    }]
    line = _format_cluster(cluster)
    # 預期：開頭 [【婉婷】冰箱漏水 — 已約23號開刀](/n/42)
    assert "[【婉婷】冰箱漏水" in line
    assert "(/n/42)" in line
    assert line.startswith("[")  # markdown link 從第一個字元開始


def test_format_cluster_checklist_item_uses_fragment_anchor():
    """Checklist item 需要 fragment anchor `#item-<id>` 才能滾到該 row。"""
    cluster = [{
        "note_id": 42,
        "item_id": 99,
        "title": "[日檢] 檢查冰箱",
        "content": "",
        "author": "小明",
    }]
    line = _format_cluster(cluster)
    assert "(/n/42#item-99)" in line


def test_format_cluster_multi_item_tail_has_per_author_links():
    """Cluster ≥2 筆時，尾註的 author 名單每個都各自 link 到自己那筆 note。"""
    cluster = [
        {"note_id": 10, "title": "冰箱漏水", "content": "詳細內容很長很長很長", "author": "婉婷"},
        {"note_id": 20, "title": "冰箱漏水", "content": "短", "author": "小明"},
        {"note_id": 30, "title": "冰箱漏水", "content": "也短", "author": "阿華"},
    ]
    line = _format_cluster(cluster)
    # 代表是最長的（婉婷）
    assert "(/n/10)" in line
    # 尾註：每個 author 個別 link 到 own note
    assert "[婉婷](/n/10)" in line or "[小明](/n/20)" in line
    assert "[小明](/n/20)" in line
    assert "[阿華](/n/30)" in line
    assert "（3 筆" in line
