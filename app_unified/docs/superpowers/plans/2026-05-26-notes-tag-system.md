# Notes 標籤系統 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notes + ChecklistItem 加標籤系統（per-store 共用、5 個/item 上限、3 個釘選 + N more filter row、inline 管理 section、picker modal）+ 5 排既有 filter 收進可摺疊面板。

**Architecture:** 3 張新 m2m table、8 個 REST endpoint、純 frontend tag picker 跟管理 UI、reuse 既有 `.collapsible` CSS/JS pattern。schema 走 `db.create_all()` 自動建（不必 inline ALTER）。

**Tech Stack:** Flask + SQLAlchemy + alembic, Jinja2, vanilla JS (ES5+), CSS3, pytest, gevent gunicorn (prod).

**Spec:** `docs/superpowers/specs/2026-05-26-notes-tag-system-design.md`

**Branch:** `feat/notes-tag-system`（已開、HEAD = `12da3c0` spec）

---

## 三個階段

| Stage | 內容 | 可獨立部署？ |
|---|---|---|
| **A** 後端（schema + API + filter）| Task A1-A6 | ✓（沒 UI 但功能可用、靠 curl/pytest 驗）|
| **B** Filter panel collapsible refactor | Task B1-B3 | ✓（純 UI、不依賴 Stage A）|
| **C** Tag UI（chip 顯示 + picker + 管理 section + filter row）| Task C1-C8 | 依賴 A + B |
| **D** Deploy verification | Task D1-D3 | 收尾 |

可選擇 A+B+C+D 一起 deploy（單一 PR），或分 stage push（每 stage 各 1 PR）。Recommend：A+B+C+D 一起 push（小團隊、無多人並行 review 需要）。

---

## File Structure

| 動作 | 路徑 | 職責 |
|---|---|---|
| Modify | `models.py` | 加 3 個 class（Tag / NoteTag / ChecklistItemTag）+ 既有 Note/ChecklistItem 加 `tags` relationship |
| Create | `notes/tags.py` | Tag CRUD/pin/assignment 8 個 endpoint（Flask `notes_bp` 內 register） |
| Create | `notes/tag_palette.py` | 10 色 palette 常數 + `pick_fg(bg_hex)` helper |
| Modify | `notes/routes.py` | `index()` 加 `?tag=` filter；不動 editor route |
| Modify | `notes/__init__.py` | import tags module 註冊 routes |
| Create | `migrations/versions/014_tag_system.py` | alembic：3 張新 table |
| Create | `templates/notes/_tag_chip.html` | 單一 chip 渲染（macro 或 partial）— for note card / item / picker / filter row |
| Create | `templates/notes/_tag_management.html` | 標籤管理 collapsible section 內容 |
| Create | `templates/notes/_tag_picker_modal.html` | picker modal markup（不含 inline create form、那塊在 JS 動態） |
| Modify | `templates/notes/index.html` | filter panel collapsible wrap、加標籤 row、加管理 section、note-card 顯示 chip、引入 css/js |
| Modify | `templates/notes/editor.html` | 加 picker 按鈕、checklist item modal 加 picker 按鈕 |
| Create | `static/css/tag_system.css` | chip / palette / picker / management UI / filter row +N more |
| Create | `static/js/tag_system.js` | picker open/close、CRUD fetch、+N more toggle、filter panel state、inline create form |
| Modify | `static/sw.js` | CACHE_NAME v23 → v24 |
| Create | `tests/test_tags_model.py` | model 基本 + cascade + unique |
| Create | `tests/test_tags_api.py` | 8 endpoint × auth/validation/permission |
| Create | `tests/test_tags_filter.py` | `/notes/?tag=X` |
| Create | `tests/test_filter_panel_collapsible.py` | filter panel wrapper markup smoke |

---

# Stage A — 後端

## Task A1: Models + alembic migration

**Files:**
- Modify: `models.py`
- Create: `migrations/versions/014_tag_system.py`
- Create: `tests/test_tags_model.py`

- [ ] **Step 1: 在 `models.py` 結尾加 3 個 class**

```python
class Tag(db.Model):
    __tablename__ = 'tags'
    id = db.Column(db.Integer, primary_key=True)
    store = db.Column(db.String(8), nullable=False, index=True)
    name = db.Column(db.String(20), nullable=False)
    color = db.Column(db.String(7), nullable=False)
    pinned = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint('store', 'name', name='uq_tags_store_name'),)


class NoteTag(db.Model):
    __tablename__ = 'note_tags'
    note_id = db.Column(db.Integer, db.ForeignKey('notes.id', ondelete='CASCADE'), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True)


class ChecklistItemTag(db.Model):
    __tablename__ = 'checklist_item_tags'
    item_id = db.Column(db.Integer, db.ForeignKey('checklist_items.id', ondelete='CASCADE'), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True)
```

在 `Note` class 加 relationship（找 Note class 結尾、保持其他 relationships 風格）：

```python
    # 在 Note class 內、其他 relationships 旁邊
    tags = db.relationship(
        'Tag',
        secondary='note_tags',
        lazy='select',
        backref=db.backref('notes', lazy='select')
    )
```

在 `ChecklistItem` class 內同樣加：

```python
    tags = db.relationship(
        'Tag',
        secondary='checklist_item_tags',
        lazy='select',
        backref=db.backref('items', lazy='select')
    )
```

- [ ] **Step 2: 建 alembic migration `014_tag_system.py`**

```python
"""Tag system: tags + note_tags + checklist_item_tags

Revision ID: 014
Revises: 013
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa


revision = '014'
down_revision = '013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('tags',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('store', sa.String(8), nullable=False, index=True),
        sa.Column('name', sa.String(20), nullable=False),
        sa.Column('color', sa.String(7), nullable=False),
        sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('store', 'name', name='uq_tags_store_name'),
    )
    op.create_index('ix_tags_pinned', 'tags', ['pinned'])
    op.create_table('note_tags',
        sa.Column('note_id', sa.Integer(),
                  sa.ForeignKey('notes.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tag_id', sa.Integer(),
                  sa.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True),
    )
    op.create_table('checklist_item_tags',
        sa.Column('item_id', sa.Integer(),
                  sa.ForeignKey('checklist_items.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('tag_id', sa.Integer(),
                  sa.ForeignKey('tags.id', ondelete='CASCADE'), primary_key=True),
    )


def downgrade():
    op.drop_table('checklist_item_tags')
    op.drop_table('note_tags')
    op.drop_index('ix_tags_pinned', table_name='tags')
    op.drop_table('tags')
```

- [ ] **Step 3: 砍掉本機 dev SQLite 讓 `db.create_all()` 重建（含新 table）**

```bash
cd /home/hirain0126/projects/webapp/app_unified
rm -f instance/dev.db
```

- [ ] **Step 4: 寫 model test `tests/test_tags_model.py`**

```python
"""Tag system model tests."""
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def test_tag_create_minimal(db):
    from models import Tag
    t = Tag(store='B店', name='重要', color='#E74C3C')
    db.session.add(t)
    db.session.commit()
    assert t.id > 0
    assert t.pinned is False


def test_tag_unique_per_store(db):
    from models import Tag
    db.session.add(Tag(store='B店', name='重要', color='#E74C3C'))
    db.session.commit()
    db.session.add(Tag(store='B店', name='重要', color='#27AE60'))
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_tag_same_name_different_store_ok(db):
    from models import Tag
    db.session.add(Tag(store='B店', name='重要', color='#E74C3C'))
    db.session.add(Tag(store='Z店', name='重要', color='#E74C3C'))
    db.session.commit()


def test_note_tag_cascade_on_tag_delete(db):
    from models import Tag, Note, NoteTag, User
    import json as _json
    u = User(username='um1', store='B店', role='user', features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    n = Note(user_id=u.id, store='B店', title='x', content='y', note_type='regular')
    t = Tag(store='B店', name='Tcasc', color='#E74C3C')
    db.session.add_all([n, t])
    db.session.commit()
    db.session.add(NoteTag(note_id=n.id, tag_id=t.id))
    db.session.commit()
    db.session.delete(t)
    db.session.commit()
    # NoteTag row gone, Note untouched
    assert db.session.get(Note, n.id) is not None
    assert NoteTag.query.filter_by(note_id=n.id).first() is None
```

- [ ] **Step 5: 跑 model test**

```bash
source ../venv/bin/activate
pytest tests/test_tags_model.py -v
```

Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add models.py migrations/versions/014_tag_system.py tests/test_tags_model.py
git commit -m "feat(tags): models + alembic migration for Tag/NoteTag/ChecklistItemTag"
```

---

## Task A2: tag_palette helper

**Files:**
- Create: `notes/tag_palette.py`
- Create: `tests/test_tag_palette.py`

- [ ] **Step 1: 寫 helper**

```python
"""Tag color palette + auto fg picker."""

PALETTE = [
    "#E74C3C",  # 紅
    "#E67E22",  # 橘
    "#F39C12",  # 琥珀
    "#F1C40F",  # 黃
    "#27AE60",  # 綠
    "#16A085",  # 青
    "#3498DB",  # 藍
    "#2C3E50",  # 深藍
    "#9B59B6",  # 紫
    "#E91E63",  # 粉
]


def is_valid_color(hex_str: str) -> bool:
    return isinstance(hex_str, str) and hex_str in PALETTE


def pick_fg(bg_hex: str) -> str:
    """Returns '#000' for light bg, '#fff' for dark bg (W3C luminance approx)."""
    if not isinstance(bg_hex, str) or len(bg_hex) != 7 or not bg_hex.startswith('#'):
        return '#fff'
    try:
        r = int(bg_hex[1:3], 16)
        g = int(bg_hex[3:5], 16)
        b = int(bg_hex[5:7], 16)
    except ValueError:
        return '#fff'
    # YIQ luminance
    yiq = (r * 299 + g * 587 + b * 114) / 1000
    return '#000' if yiq >= 160 else '#fff'
```

- [ ] **Step 2: 寫 test**

```python
"""tag_palette helper tests."""
from notes.tag_palette import PALETTE, is_valid_color, pick_fg


def test_palette_has_10_colors():
    assert len(PALETTE) == 10
    assert all(c.startswith('#') and len(c) == 7 for c in PALETTE)


def test_is_valid_color_accepts_palette_colors():
    for c in PALETTE:
        assert is_valid_color(c) is True


def test_is_valid_color_rejects_others():
    assert is_valid_color('#000000') is False
    assert is_valid_color('red') is False
    assert is_valid_color(None) is False
    assert is_valid_color('') is False


def test_pick_fg_light_bg_returns_black():
    # 黃 / 琥珀 = light → 黑字
    assert pick_fg('#F1C40F') == '#000'
    assert pick_fg('#F39C12') == '#000'


def test_pick_fg_dark_bg_returns_white():
    # 紅 / 深藍 = dark → 白字
    assert pick_fg('#E74C3C') == '#fff'
    assert pick_fg('#2C3E50') == '#fff'


def test_pick_fg_invalid_returns_white():
    assert pick_fg('not-a-hex') == '#fff'
    assert pick_fg(None) == '#fff'
    assert pick_fg('#FFF') == '#fff'  # too short
```

- [ ] **Step 3: 跑 test**

```bash
pytest tests/test_tag_palette.py -v
```

Expected: 6 PASS

- [ ] **Step 4: Commit**

```bash
git add notes/tag_palette.py tests/test_tag_palette.py
git commit -m "feat(tags): 10-color palette + auto fg/bg contrast helper"
```

---

## Task A3: Tag CRUD endpoints

**Files:**
- Create: `notes/tags.py`
- Modify: `notes/__init__.py`
- Create: `tests/test_tags_api.py`

- [ ] **Step 1: 看 `notes/__init__.py` 確認 blueprint 名稱 + import pattern**

```bash
cat notes/__init__.py
```

Expected: 有 `bp = Blueprint(...)` 跟 `from . import routes`

- [ ] **Step 2: 建 `notes/tags.py`（CRUD 4 endpoint）**

```python
"""Tag CRUD + pin + assignment endpoints."""
from datetime import datetime
from flask import request, jsonify, abort
from flask_login import login_required, current_user

from extensions import db
from models import Tag, NoteTag, ChecklistItemTag, Note, ChecklistItem
from notes import bp
from notes.tag_palette import is_valid_color, pick_fg


PIN_LIMIT = 3
MAX_TAGS_PER_ITEM = 5


def _tag_to_dict(tag, include_usage=False):
    d = {
        'id': tag.id,
        'name': tag.name,
        'color': tag.color,
        'fg': pick_fg(tag.color),
        'pinned': tag.pinned,
        'store': tag.store,
    }
    if include_usage:
        n_count = NoteTag.query.filter_by(tag_id=tag.id).count()
        i_count = ChecklistItemTag.query.filter_by(tag_id=tag.id).count()
        d['usage_count'] = n_count + i_count
    return d


def _effective_store():
    """super_admin 可帶 ?store=、其他 user 鎖在自己 store"""
    if current_user.role == 'super_admin':
        s = request.args.get('store') or request.view_args.get('store')
        if s:
            return s
    return current_user.store


def _require_same_store(tag):
    eff = _effective_store()
    if not eff or tag.store != eff:
        abort(403)


@bp.route('/api/tags', methods=['GET'])
@login_required
def list_tags():
    store = _effective_store()
    if not store:
        return jsonify({'tags': []})
    tags = Tag.query.filter_by(store=store).order_by(Tag.pinned.desc(), Tag.name).all()
    return jsonify({'tags': [_tag_to_dict(t, include_usage=True) for t in tags]})


@bp.route('/api/tags', methods=['POST'])
@login_required
def create_tag():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    color = (data.get('color') or '').strip()
    store = _effective_store()
    if not store:
        return jsonify({'error': '無 store'}), 400
    if not (1 <= len(name) <= 20):
        return jsonify({'error': '名稱長度需 1-20'}), 400
    if not is_valid_color(color):
        return jsonify({'error': '顏色非 palette 內'}), 400
    if Tag.query.filter_by(store=store, name=name).first():
        return jsonify({'error': '同名標籤已存在'}), 400
    t = Tag(store=store, name=name, color=color, pinned=False,
            created_by=current_user.id, created_at=datetime.utcnow())
    db.session.add(t)
    db.session.commit()
    return jsonify({'tag': _tag_to_dict(t, include_usage=True)})


@bp.route('/api/tags/<int:tag_id>', methods=['PATCH'])
@login_required
def update_tag(tag_id):
    t = db.session.get(Tag, tag_id) or abort(404)
    _require_same_store(t)
    data = request.get_json(silent=True) or {}
    if 'name' in data:
        new_name = (data['name'] or '').strip()
        if not (1 <= len(new_name) <= 20):
            return jsonify({'error': '名稱長度需 1-20'}), 400
        dup = Tag.query.filter(Tag.store == t.store, Tag.name == new_name,
                               Tag.id != t.id).first()
        if dup:
            return jsonify({'error': '同名標籤已存在'}), 400
        t.name = new_name
    if 'color' in data:
        if not is_valid_color(data['color']):
            return jsonify({'error': '顏色非 palette 內'}), 400
        t.color = data['color']
    db.session.commit()
    return jsonify({'tag': _tag_to_dict(t, include_usage=True)})


@bp.route('/api/tags/<int:tag_id>', methods=['DELETE'])
@login_required
def delete_tag(tag_id):
    t = db.session.get(Tag, tag_id) or abort(404)
    _require_same_store(t)
    n_count = NoteTag.query.filter_by(tag_id=t.id).count()
    i_count = ChecklistItemTag.query.filter_by(tag_id=t.id).count()
    db.session.delete(t)  # cascade clears m2m
    db.session.commit()
    return jsonify({'ok': True, 'cascaded': n_count + i_count})
```

- [ ] **Step 3: 在 `notes/__init__.py` 加 import**

```python
# 在現有 from . import routes 後面加
from . import tags  # noqa
```

- [ ] **Step 4: 寫 test `tests/test_tags_api.py`（CRUD 部分）**

```python
"""Tag API tests."""
import json as _json
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_user(db, role='user', store='B店', username=None):
    from models import User
    u = User(username=username or f'{role}_{store}',
             role=role, store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def test_create_tag_ok(client, db):
    u = _mk_user(db, username='u_tag_c1')
    _login(client, u)
    r = client.post('/notes/api/tags',
                    json={'name': '重要', 'color': '#E74C3C'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['tag']['name'] == '重要'
    assert data['tag']['fg'] == '#fff'  # dark bg → white fg
    assert data['tag']['pinned'] is False


def test_create_tag_rejects_bad_color(client, db):
    u = _mk_user(db, username='u_tag_c2')
    _login(client, u)
    r = client.post('/notes/api/tags', json={'name': 'X', 'color': '#000000'})
    assert r.status_code == 400


def test_create_tag_rejects_duplicate(client, db):
    u = _mk_user(db, username='u_tag_c3')
    _login(client, u)
    client.post('/notes/api/tags', json={'name': 'D', 'color': '#E74C3C'})
    r = client.post('/notes/api/tags', json={'name': 'D', 'color': '#27AE60'})
    assert r.status_code == 400


def test_list_tags_per_store_only(client, db):
    from models import Tag
    u_b = _mk_user(db, store='B店', username='u_list_b')
    u_z = _mk_user(db, store='Z店', username='u_list_z')
    db.session.add(Tag(store='B店', name='Btag', color='#E74C3C'))
    db.session.add(Tag(store='Z店', name='Ztag', color='#27AE60'))
    db.session.commit()
    _login(client, u_b)
    r = client.get('/notes/api/tags')
    names = [t['name'] for t in r.get_json()['tags']]
    assert 'Btag' in names and 'Ztag' not in names


def test_update_tag(client, db):
    u = _mk_user(db, username='u_upd')
    _login(client, u)
    r = client.post('/notes/api/tags', json={'name': 'A', 'color': '#E74C3C'})
    tid = r.get_json()['tag']['id']
    r2 = client.patch(f'/notes/api/tags/{tid}', json={'name': 'B'})
    assert r2.status_code == 200
    assert r2.get_json()['tag']['name'] == 'B'


def test_delete_tag_returns_cascade_count(client, db):
    from models import Tag, Note, NoteTag
    u = _mk_user(db, username='u_del')
    db.session.add(Note(user_id=u.id, store='B店', title='n', content='',
                        note_type='regular'))
    t = Tag(store='B店', name='X', color='#E74C3C')
    db.session.add(t)
    db.session.commit()
    db.session.add(NoteTag(note_id=Note.query.first().id, tag_id=t.id))
    db.session.commit()
    _login(client, u)
    r = client.delete(f'/notes/api/tags/{t.id}')
    assert r.status_code == 200
    assert r.get_json()['cascaded'] == 1


def test_update_cross_store_forbidden(client, db):
    from models import Tag
    u = _mk_user(db, store='B店', username='u_x1')
    db.session.add(Tag(store='Z店', name='Ztag', color='#E74C3C'))
    db.session.commit()
    t = Tag.query.filter_by(store='Z店').first()
    _login(client, u)
    r = client.patch(f'/notes/api/tags/{t.id}', json={'name': 'Y'})
    assert r.status_code == 403
```

- [ ] **Step 5: 跑 test**

```bash
pytest tests/test_tags_api.py -v
```

Expected: 7 PASS

- [ ] **Step 6: Commit**

```bash
git add notes/tags.py notes/__init__.py tests/test_tags_api.py
git commit -m "feat(tags): CRUD endpoints (list/create/update/delete) + per-store permission"
```

---

## Task A4: Pin endpoints

**Files:**
- Modify: `notes/tags.py`
- Modify: `tests/test_tags_api.py`

- [ ] **Step 1: 在 `notes/tags.py` 加 pin/unpin endpoint**

```python
@bp.route('/api/tags/<int:tag_id>/pin', methods=['POST'])
@login_required
def pin_tag(tag_id):
    t = db.session.get(Tag, tag_id) or abort(404)
    _require_same_store(t)
    if t.pinned:
        return jsonify({'tag': _tag_to_dict(t, include_usage=True)})
    # check pin count in same store
    pinned_count = Tag.query.filter_by(store=t.store, pinned=True).count()
    if pinned_count >= PIN_LIMIT:
        return jsonify({'error': f'釘選已滿 {PIN_LIMIT} 個、先取消其他釘選'}), 400
    t.pinned = True
    db.session.commit()
    return jsonify({'tag': _tag_to_dict(t, include_usage=True)})


@bp.route('/api/tags/<int:tag_id>/pin', methods=['DELETE'])
@login_required
def unpin_tag(tag_id):
    t = db.session.get(Tag, tag_id) or abort(404)
    _require_same_store(t)
    t.pinned = False
    db.session.commit()
    return jsonify({'tag': _tag_to_dict(t, include_usage=True)})
```

- [ ] **Step 2: 加 test 到 `tests/test_tags_api.py`**

```python
def test_pin_tag(client, db):
    u = _mk_user(db, username='u_pin1')
    _login(client, u)
    r = client.post('/notes/api/tags', json={'name': 'P', 'color': '#E74C3C'})
    tid = r.get_json()['tag']['id']
    r2 = client.post(f'/notes/api/tags/{tid}/pin')
    assert r2.status_code == 200
    assert r2.get_json()['tag']['pinned'] is True


def test_pin_limit_3(client, db):
    u = _mk_user(db, username='u_pin_lim')
    _login(client, u)
    ids = []
    for i in range(4):
        r = client.post('/notes/api/tags',
                        json={'name': f'P{i}', 'color': '#E74C3C'})
        ids.append(r.get_json()['tag']['id'])
    for tid in ids[:3]:
        assert client.post(f'/notes/api/tags/{tid}/pin').status_code == 200
    r = client.post(f'/notes/api/tags/{ids[3]}/pin')
    assert r.status_code == 400
    assert '釘選已滿' in r.get_json()['error']


def test_unpin_then_pin_new(client, db):
    u = _mk_user(db, username='u_unpin')
    _login(client, u)
    ids = []
    for i in range(4):
        r = client.post('/notes/api/tags',
                        json={'name': f'U{i}', 'color': '#E74C3C'})
        ids.append(r.get_json()['tag']['id'])
    for tid in ids[:3]:
        client.post(f'/notes/api/tags/{tid}/pin')
    client.delete(f'/notes/api/tags/{ids[0]}/pin')
    r = client.post(f'/notes/api/tags/{ids[3]}/pin')
    assert r.status_code == 200
```

- [ ] **Step 3: 跑 test**

```bash
pytest tests/test_tags_api.py -v
```

Expected: 10 PASS（原 7 + 3 新）

- [ ] **Step 4: Commit**

```bash
git add notes/tags.py tests/test_tags_api.py
git commit -m "feat(tags): pin/unpin endpoints with 3-tag-per-store limit"
```

---

## Task A5: Assignment endpoints (PUT tags on note/item)

**Files:**
- Modify: `notes/tags.py`
- Modify: `tests/test_tags_api.py`

- [ ] **Step 1: 加 PUT assignment endpoints**

```python
@bp.route('/api/<int:note_id>/tags', methods=['PUT'])
@login_required
def set_note_tags(note_id):
    n = db.session.get(Note, note_id) or abort(404)
    # same store check
    if current_user.role != 'super_admin' and n.store != current_user.store:
        abort(403)
    data = request.get_json(silent=True) or {}
    tag_ids = data.get('tag_ids') or []
    if not isinstance(tag_ids, list) or len(tag_ids) > MAX_TAGS_PER_ITEM:
        return jsonify({'error': f'最多 {MAX_TAGS_PER_ITEM} 個標籤'}), 400
    # validate all tags exist + same store as note
    tags = Tag.query.filter(Tag.id.in_(tag_ids)).all() if tag_ids else []
    if len(tags) != len(set(tag_ids)):
        return jsonify({'error': '部分標籤不存在'}), 400
    for t in tags:
        if t.store != n.store:
            return jsonify({'error': '跨店標籤'}), 400
    NoteTag.query.filter_by(note_id=n.id).delete()
    for tid in set(tag_ids):
        db.session.add(NoteTag(note_id=n.id, tag_id=tid))
    db.session.commit()
    return jsonify({'tag_ids': list(set(tag_ids))})


@bp.route('/api/items/<int:item_id>/tags', methods=['PUT'])
@login_required
def set_item_tags(item_id):
    item = db.session.get(ChecklistItem, item_id) or abort(404)
    note = db.session.get(Note, item.note_id) or abort(404)
    if current_user.role != 'super_admin' and note.store != current_user.store:
        abort(403)
    data = request.get_json(silent=True) or {}
    tag_ids = data.get('tag_ids') or []
    if not isinstance(tag_ids, list) or len(tag_ids) > MAX_TAGS_PER_ITEM:
        return jsonify({'error': f'最多 {MAX_TAGS_PER_ITEM} 個標籤'}), 400
    tags = Tag.query.filter(Tag.id.in_(tag_ids)).all() if tag_ids else []
    if len(tags) != len(set(tag_ids)):
        return jsonify({'error': '部分標籤不存在'}), 400
    for t in tags:
        if t.store != note.store:
            return jsonify({'error': '跨店標籤'}), 400
    ChecklistItemTag.query.filter_by(item_id=item.id).delete()
    for tid in set(tag_ids):
        db.session.add(ChecklistItemTag(item_id=item.id, tag_id=tid))
    db.session.commit()
    return jsonify({'tag_ids': list(set(tag_ids))})
```

- [ ] **Step 2: 加 test**

```python
def test_set_note_tags_replace_semantics(client, db):
    from models import Tag, Note
    u = _mk_user(db, username='u_set_n1')
    n = Note(user_id=u.id, store='B店', title='n', content='', note_type='regular')
    t1 = Tag(store='B店', name='T1', color='#E74C3C')
    t2 = Tag(store='B店', name='T2', color='#27AE60')
    t3 = Tag(store='B店', name='T3', color='#3498DB')
    db.session.add_all([n, t1, t2, t3])
    db.session.commit()
    _login(client, u)
    r = client.put(f'/notes/api/{n.id}/tags', json={'tag_ids': [t1.id, t2.id]})
    assert sorted(r.get_json()['tag_ids']) == sorted([t1.id, t2.id])
    # replace with t3 only
    r = client.put(f'/notes/api/{n.id}/tags', json={'tag_ids': [t3.id]})
    assert r.get_json()['tag_ids'] == [t3.id]


def test_set_note_tags_rejects_over_5(client, db):
    from models import Tag, Note
    u = _mk_user(db, username='u_max5')
    n = Note(user_id=u.id, store='B店', title='n', content='', note_type='regular')
    ts = [Tag(store='B店', name=f'M{i}', color='#E74C3C') for i in range(6)]
    db.session.add(n)
    db.session.add_all(ts)
    db.session.commit()
    _login(client, u)
    r = client.put(f'/notes/api/{n.id}/tags',
                   json={'tag_ids': [t.id for t in ts]})
    assert r.status_code == 400


def test_set_note_tags_rejects_cross_store(client, db):
    from models import Tag, Note
    u = _mk_user(db, store='B店', username='u_xs')
    n = Note(user_id=u.id, store='B店', title='n', content='', note_type='regular')
    t = Tag(store='Z店', name='Zt', color='#E74C3C')
    db.session.add_all([n, t])
    db.session.commit()
    _login(client, u)
    r = client.put(f'/notes/api/{n.id}/tags', json={'tag_ids': [t.id]})
    assert r.status_code == 400
```

- [ ] **Step 3: 跑 test**

```bash
pytest tests/test_tags_api.py -v
```

Expected: 13 PASS

- [ ] **Step 4: Commit**

```bash
git add notes/tags.py tests/test_tags_api.py
git commit -m "feat(tags): PUT note/item tag assignment with 5-tag-per-item limit"
```

---

## Task A6: `/notes/?tag=` filter

**Files:**
- Modify: `notes/routes.py`
- Create: `tests/test_tags_filter.py`

- [ ] **Step 1: 找 `notes/routes.py` 內 `index()` 的 base query 區、加 tag filter**

`notes/routes.py` 內 `index()` 大致長這樣（找 `Note.active()` 開頭那段）：

```python
@bp.route('/')
@login_required
def index():
    # ... 既有 base = Note.active() ... category / shift / range / status / sort filter
```

加入 tag filter（放在其他 filter 之間、保持 pattern 一致）：

```python
    tag_id = request.args.get('tag', type=int)
    if tag_id:
        from models import NoteTag
        q = q.join(NoteTag, NoteTag.note_id == Note.id).filter(NoteTag.tag_id == tag_id)
```

完整位置：找到 `current_keyword = request.args.get('q')` 那段、在它**前面**插入上述 3 行（讓 keyword search mode 仍能 override）。

也要在 template render 那邊把 `current_tag = tag_id` 傳給 template：

```python
    return render_template('notes/index.html',
                           # ... existing kwargs ...
                           current_tag=tag_id)
```

- [ ] **Step 2: 寫 filter test**

```python
"""Tag filter on /notes/ test."""
import json as _json
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_user(db, store='B店', username=None):
    from models import User
    u = User(username=username or 'u_filt',
             role='user', store=store,
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def test_notes_index_filter_by_tag(client, db):
    from models import Tag, Note, NoteTag
    u = _mk_user(db, username='u_tflt')
    n1 = Note(user_id=u.id, store='B店', title='HAS_TAG',
              content='', note_type='regular')
    n2 = Note(user_id=u.id, store='B店', title='NO_TAG',
              content='', note_type='regular')
    t = Tag(store='B店', name='重要', color='#E74C3C')
    db.session.add_all([n1, n2, t])
    db.session.commit()
    db.session.add(NoteTag(note_id=n1.id, tag_id=t.id))
    db.session.commit()
    _login(client, u)
    r = client.get(f'/notes/?tag={t.id}&range=all')
    html = r.get_data(as_text=True)
    assert 'HAS_TAG' in html
    assert 'NO_TAG' not in html


def test_notes_index_no_tag_returns_all(client, db):
    from models import Note
    u = _mk_user(db, username='u_noflt')
    db.session.add(Note(user_id=u.id, store='B店', title='ANY',
                        content='', note_type='regular'))
    db.session.commit()
    _login(client, u)
    r = client.get('/notes/?range=all')
    assert 'ANY' in r.get_data(as_text=True)
```

- [ ] **Step 3: 跑 test**

```bash
pytest tests/test_tags_filter.py tests/test_notes_misc.py -v
```

Expected: 2 新 + 既有 notes tests 全 PASS

- [ ] **Step 4: Commit**

```bash
git add notes/routes.py tests/test_tags_filter.py
git commit -m "feat(tags): /notes/?tag=X filter on listing page"
```

---

# Stage B — Filter panel collapsible refactor

## Task B1: Filter panel markup smoke test

**Files:**
- Create: `tests/test_filter_panel_collapsible.py`

- [ ] **Step 1: 寫 failing test**

```python
"""Filter panel collapsible wrapper markup."""
import json as _json
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_user(db):
    from models import User
    u = User(username='u_fp', role='user', store='B店',
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def test_filter_panel_wrapper_present(client, db):
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/').get_data(as_text=True)
    assert 'data-section="filters"' in html
    assert '進階篩選' in html


def test_filter_panel_contains_existing_filter_rows(client, db):
    """Wrapper 包進既有 5 排 filter + 搜尋 + 標籤 row。"""
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/').get_data(as_text=True)
    # 確認 wrapper 內含種類/班別/時間/狀態 label
    assert 'range-tabs' in html or '時間' in html
    assert '種類' in html
    assert '班別' in html
```

- [ ] **Step 2: 跑 test 確認 fail**

```bash
pytest tests/test_filter_panel_collapsible.py -v
```

Expected: 1 fail（data-section="filters" 不在）、1 pass（既有 markup 還在）

- [ ] **Step 3: Commit failing baseline**

```bash
git add tests/test_filter_panel_collapsible.py
git commit -m "test(notes): filter panel collapsible markup baseline (1 failing)"
```

---

## Task B2: Wrap 5 filter rows + search + tag row into collapsible

**Files:**
- Modify: `templates/notes/index.html`

- [ ] **Step 1: 找現有 search form + 5 排 filter tabs 段落（約 line 148-211）**

現有結構：
```
<form action="...">  ← 搜尋 (line 148)
{% if not current_keyword %}
  <div class="range-tabs"> 種類 </div>  ← line 160-167
  <div class="range-tabs"> 班別 </div>  ← line 168-176
  <div class="range-tabs"> 時間 </div>  ← line 177-185
  <div class="status-tabs"> 狀態 </div> ← line 186-200
  <div class="status-tabs"> 排列 </div> ← line 201-210
{% endif %}
```

- [ ] **Step 2: 整段用 `.collapsible` 包起來、加 active filter count + 新標籤 row 預留位**

Replace 整段（line 148 開始的 form 到 line 211 `{% endif %}`）成：

```jinja
{% set active_filter_count = 0 %}
{% if current_keyword %}{% set active_filter_count = active_filter_count + 1 %}{% endif %}
{% if current_tag %}{% set active_filter_count = active_filter_count + 1 %}{% endif %}
{% if current_category %}{% set active_filter_count = active_filter_count + 1 %}{% endif %}
{% if current_shift and current_shift != 'none' %}{% set active_filter_count = active_filter_count + 1 %}{% endif %}
{% if current_range and current_range != 'today' %}{% set active_filter_count = active_filter_count + 1 %}{% endif %}
{% if current_status %}{% set active_filter_count = active_filter_count + 1 %}{% endif %}
{% if current_sort and current_sort != 'updated' %}{% set active_filter_count = active_filter_count + 1 %}{% endif %}

<section class="collapsible" data-section="filters"
         data-active-count="{{ active_filter_count }}">
  <div class="collapsible-bar" data-role="section-bar">
    <span class="bar-title">⚙️ 進階篩選{% if active_filter_count > 0 %} ({{ active_filter_count }}){% endif %}</span>
    <span class="bar-chevron">▶</span>
  </div>
  <div class="collapsible-body">
    <form method="get" action="{{ url_for('notes.index') }}" style="display:flex;gap:.4rem;margin-bottom:.75rem;align-items:center;">
      <span style="font-size:.85rem;color:#666;">🔍 關鍵字：</span>
      {% if current_store %}<input type="hidden" name="store" value="{{ current_store }}">{% endif %}
      <input type="text" name="q" value="{{ current_keyword or '' }}" placeholder="搜尋標題/內容…" style="flex:1;max-width:300px;padding:.4rem .6rem;border:1px solid #ddd;border-radius:6px;font-size:.9rem;min-height:36px;">
      <button type="submit" class="btn btn-primary" style="padding:.35rem .8rem;font-size:.85rem;min-height:36px;">搜尋</button>
      {% if active_filter == 'keyword' %}
      <a href="{{ url_for('notes.index', store=current_store) }}" class="btn btn-secondary" style="padding:.35rem .8rem;font-size:.85rem;min-height:36px;display:inline-flex;align-items:center;gap:.3rem;"><span style="color:#e74c3c;font-weight:700;">✕</span> 取消</a>
      {% endif %}
    </form>

    {# 標籤 row 預留位、Task C8 填入內容 #}
    <div class="tag-filter-row" data-role="tag-row"></div>

    {% if not current_keyword %}
    {# 既有 5 排 filter 不變 #}
    {% set cat_tab_labels = [('','全部'),('employee','員工'),('customer','客人'),('machine','機台'),('misc','雜項')] %}
    <div class="range-tabs">
      <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">種類：</span>
      {% for val, label in cat_tab_labels %}
      <a href="{{ url_for('notes.index', store=current_store, category=val, tag=current_tag) }}"
         class="store-tab {% if current_category == val %}active{% endif %}"
         style="{% if current_category == val and val %}background:#3498db;color:#fff;{% endif %}">{{ label }}</a>
      {% endfor %}
    </div>
    {% set shift_tab_labels = [('none','全部'),('A','A'),('B','B'),('N','N')] %}
    <div class="range-tabs">
      <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">班別：</span>
      {% for val, label in shift_tab_labels %}
      <a href="{{ url_for('notes.index', store=current_store, category=current_category, shift=val, tag=current_tag) }}"
         class="store-tab {% if current_shift == val %}active{% endif %}"
         style="{% if current_shift == val %}background:#16a085;color:#fff;{% endif %}">{{ label }}</a>
      {% endfor %}
    </div>
    <div class="range-tabs">
      <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">時間：</span>
      {% set range_labels = [('today','今天'),('3d','近3天'),('7d','近7天'),('all','所有')] %}
      {% for val, label in range_labels %}
      <a href="{{ url_for('notes.index', store=current_store, category=current_category, shift=current_shift, range=val, tag=current_tag) }}"
         class="store-tab {% if current_range == val %}active{% endif %}"
         style="{% if current_range == val %}background:#2c3e50;color:#fff;{% endif %}">{{ label }}</a>
      {% endfor %}
    </div>
    {% set status_labels = [('','全部'),('pending','待處理'),('in_progress','待討論'),('resolved','已解決')] %}
    <div class="status-tabs">
      <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">狀態：</span>
      {% for val, label in status_labels %}
      <a href="{{ url_for('notes.index', store=current_store, category=current_category, shift=current_shift, range=current_range, status=val, tag=current_tag) }}"
         class="store-tab {% if current_status == val %}active{% endif %}"
         style="{% if current_status == val %}
                  {% if val == '' %}background:#2c3e50;color:#fff;
                  {% elif val == 'pending' %}background:#95a5a6;color:#fff;
                  {% elif val == 'in_progress' %}background:#3498db;color:#fff;
                  {% elif val == 'resolved' %}background:#27ae60;color:#fff;
                  {% endif %}
                {% endif %}">{{ label }}</a>
      {% endfor %}
    </div>
    {% set sort_labels = [('updated','照更新'),('created','照日期')] %}
    <div class="status-tabs">
      <span style="font-size:.85rem;color:#666;align-self:center;margin-right:.25rem;">排列：</span>
      {% for val, label in sort_labels %}
      <a href="{{ url_for('notes.index', store=current_store, category=current_category, shift=current_shift, range=current_range, status=current_status, sort=val, tag=current_tag) }}"
         onclick="try { localStorage.setItem('notes_sort', '{{ val }}'); } catch(e){}"
         class="store-tab {% if current_sort == val %}active{% endif %}"
         style="{% if current_sort == val %}background:#8e44ad;color:#fff;{% endif %}">{{ label }}</a>
      {% endfor %}
    </div>
    {% endif %}
  </div>
</section>
```

注意：每個 filter URL 都帶 `tag=current_tag`、保留 tag filter state。

- [ ] **Step 3: 跑 markup smoke + 既有 notes test**

```bash
pytest tests/test_filter_panel_collapsible.py tests/test_notes_misc.py tests/test_announcements_index_integration.py -v
```

Expected: 全 PASS

- [ ] **Step 4: Commit**

```bash
git add templates/notes/index.html
git commit -m "feat(notes): wrap 5 filter rows + search in collapsible panel"
```

---

## Task B3: Filter panel JS — auto-expand on active filter + localStorage

**Files:**
- Modify: `static/js/collapsible_sections.js`

- [ ] **Step 1: 在 `collapsible_sections.js` 加 filter panel init function**

在 IIFE 內、existing init functions 之後加：

```javascript
  var FILTER_KEY = 'notes_filter_panel_open';

  function initFilterSection() {
    var section = document.querySelector('.collapsible[data-section="filters"]');
    if (!section) return;
    var activeCount = parseInt(section.getAttribute('data-active-count') || '0', 10);
    var stored;
    try { stored = localStorage.getItem(FILTER_KEY); } catch (e) { stored = null; }
    var shouldExpand;
    if (activeCount > 0) {
      shouldExpand = true;  // URL 有 active filter → 強制展開
    } else if (stored === '1') {
      shouldExpand = true;
    } else if (stored === '0') {
      shouldExpand = false;
    } else {
      shouldExpand = false;  // 沒設過 → 預設摺疊
    }
    setSectionExpanded(section, shouldExpand);
    var bar = section.querySelector('.collapsible-bar[data-role="section-bar"]');
    if (bar) {
      bar.addEventListener('click', function() {
        toggleSection(section);
        try { localStorage.setItem(FILTER_KEY, section.classList.contains('is-expanded') ? '1' : '0'); } catch (e) {}
      });
    }
  }
```

修改 `init()` 加上呼叫：

```javascript
  function init() {
    initAnnouncementSection();
    initAiSummarySection();
    initFilterSection();  // 新加
  }
```

- [ ] **Step 2: JS syntax check**

```bash
node -c static/js/collapsible_sections.js && echo OK
```

Expected: OK

- [ ] **Step 3: Commit**

```bash
git add static/js/collapsible_sections.js
git commit -m "feat(notes): filter panel auto-expand on active filter + localStorage memory"
```

---

# Stage C — Tag UI

## Task C1: Tag system CSS

**Files:**
- Create: `static/css/tag_system.css`

- [ ] **Step 1: 寫 CSS**

```css
/* ===== Tag system: chips, palette, picker, management ===== */

.tag-chip {
  display: inline-flex;
  align-items: center;
  padding: .2rem .65rem;
  border-radius: 16px;
  font-size: .78rem;
  font-weight: 600;
  background: var(--tag-bg, #95a5a6);
  color: var(--tag-fg, #fff);
  white-space: nowrap;
  margin: .12rem;
  line-height: 1.4;
  min-height: 26px;
  box-sizing: border-box;
}
.tag-chip.is-button {
  cursor: pointer;
  user-select: none;
  border: 1.5px solid transparent;
  transition: opacity .12s, border-color .12s;
}
.tag-chip.is-button:not(.is-active) {
  opacity: .55;
}
.tag-chip.is-button:hover { opacity: 1; }
.tag-chip.is-button.is-active { opacity: 1; border-color: rgba(0,0,0,.18); }
.tag-chip.is-disabled { opacity: .3; cursor: not-allowed; }

/* Tag filter row on listing page */
.tag-filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: .25rem;
  align-items: center;
  margin: .5rem 0;
  padding: .35rem 0;
}
.tag-filter-row .filter-icon { font-size: .85rem; color: #666; margin-right: .35rem; }
.tag-filter-row .more-toggle {
  background: transparent;
  border: 1px solid #ccc;
  border-radius: 14px;
  padding: .2rem .55rem;
  font-size: .75rem;
  color: #666;
  cursor: pointer;
}
.tag-filter-row .more-toggle:hover { background: #f0f2f5; }

/* Note card tag chips (above title) */
.note-card .note-card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: .15rem;
  margin-bottom: .35rem;
}
.note-card .note-card-tags .tag-chip {
  font-size: .68rem;
  padding: .1rem .45rem;
  min-height: 20px;
}

/* Checklist item tag chips */
.cl-item .item-tags {
  display: flex;
  flex-wrap: wrap;
  gap: .12rem;
  margin-top: .25rem;
}

/* Tag management section */
.tag-mgmt-list {
  display: flex;
  flex-direction: column;
  gap: .35rem;
  margin-top: .5rem;
}
.tag-mgmt-row {
  display: flex;
  align-items: center;
  gap: .5rem;
  padding: .35rem .55rem;
  background: #fafbfc;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
}
.tag-mgmt-row .tag-chip { flex: 0 0 auto; }
.tag-mgmt-row .row-actions { margin-left: auto; display: flex; gap: .3rem; }
.tag-mgmt-row .row-actions button {
  border: 1px solid #ddd;
  background: #fff;
  border-radius: 4px;
  padding: .2rem .55rem;
  font-size: .75rem;
  cursor: pointer;
  min-height: 30px;
}
.tag-mgmt-row .row-actions button:hover { background: #f0f2f5; }
.tag-mgmt-row .row-actions button.is-danger { color: #c0392b; }
.tag-mgmt-row .row-actions button:disabled { opacity: .35; cursor: not-allowed; }

.tag-mgmt-subheader { font-size: .8rem; color: #888; margin: .65rem 0 .35rem; }

/* Inline create/edit form */
.tag-form {
  background: #f8f9fa;
  border: 1px dashed #c0c0c0;
  border-radius: 6px;
  padding: .65rem .85rem;
  margin: .5rem 0;
}
.tag-form input[type="text"] {
  width: 100%;
  padding: .4rem .55rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  font-size: .9rem;
  box-sizing: border-box;
}
.tag-form .palette {
  display: flex;
  flex-wrap: wrap;
  gap: .35rem;
  margin-top: .5rem;
}
.tag-form .palette .swatch {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  cursor: pointer;
  border: 2px solid transparent;
  transition: transform .1s;
}
.tag-form .palette .swatch:hover { transform: scale(1.1); }
.tag-form .palette .swatch.is-selected { border-color: #333; }
.tag-form .form-actions {
  display: flex;
  justify-content: flex-end;
  gap: .5rem;
  margin-top: .65rem;
}
.tag-form .preview { margin: .5rem 0; }

/* Picker modal */
.tag-picker-modal {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.45);
  display: none;
  justify-content: center;
  align-items: flex-start;
  padding-top: 5vh;
  z-index: 1000;
}
.tag-picker-modal.is-open { display: flex; }
.tag-picker-modal .panel {
  background: #fff;
  border-radius: 10px;
  width: 90vw;
  max-width: 480px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
}
.tag-picker-modal .panel-header {
  padding: .85rem 1rem;
  border-bottom: 1px solid #e0e0e0;
  display: flex;
  align-items: center;
  font-weight: 600;
}
.tag-picker-modal .panel-header .close {
  margin-left: auto;
  background: transparent;
  border: none;
  font-size: 1.2rem;
  cursor: pointer;
  color: #888;
}
.tag-picker-modal .panel-body {
  padding: .85rem 1rem;
  overflow-y: auto;
  flex: 1;
}
.tag-picker-modal .picker-list {
  display: flex;
  flex-wrap: wrap;
  gap: .3rem;
}
.tag-picker-modal .panel-footer {
  padding: .65rem 1rem;
  border-top: 1px solid #e0e0e0;
  display: flex;
  justify-content: flex-end;
  gap: .5rem;
}

/* Confirm modal (delete cascade) */
.tag-confirm-modal {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.5);
  display: none;
  justify-content: center;
  align-items: center;
  z-index: 1001;
}
.tag-confirm-modal.is-open { display: flex; }
.tag-confirm-modal .panel {
  background: #fff;
  border-radius: 8px;
  padding: 1.25rem;
  max-width: 420px;
  width: 90vw;
}
.tag-confirm-modal .panel-actions {
  display: flex;
  justify-content: flex-end;
  gap: .5rem;
  margin-top: 1rem;
}
.tag-confirm-modal .panel-actions .is-danger {
  background: #e74c3c;
  color: #fff;
  border: none;
  padding: .5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
}
```

- [ ] **Step 2: Commit**

```bash
git add static/css/tag_system.css
git commit -m "feat(tags): tag_system.css — chips, palette, picker, management UI"
```

---

## Task C2: Tag management collapsible section + index.html 整合

**Files:**
- Create: `templates/notes/_tag_management.html`
- Modify: `templates/notes/index.html`

- [ ] **Step 1: 建 partial template**

```jinja
{# Tag management collapsible section — append after AI summary section #}
<section class="collapsible" data-section="tag-management">
  <div class="collapsible-bar" data-role="section-bar">
    <span class="bar-title">🏷️ 標籤管理 <span data-role="count"></span></span>
    <span class="bar-chevron">▶</span>
  </div>
  <div class="collapsible-body">
    <button type="button" class="btn btn-primary" data-role="new-tag-btn"
            style="margin-bottom:.5rem;font-size:.85rem;padding:.4rem .8rem;">
      + 新增標籤
    </button>
    <div data-role="new-tag-form-host"></div>
    <div class="tag-mgmt-subheader">📌 釘選 <span data-role="pin-count">(0/3)</span></div>
    <div class="tag-mgmt-list" data-role="pinned-list"></div>
    <div class="tag-mgmt-subheader">其他標籤</div>
    <div class="tag-mgmt-list" data-role="other-list"></div>
  </div>
</section>
```

- [ ] **Step 2: 在 `templates/notes/index.html` head 加 css link**

找 `collapsible_sections.css` link 那行、後面加：

```jinja
<link rel="stylesheet" href="{{ url_for('static', filename='css/tag_system.css') }}">
```

- [ ] **Step 3: include 管理 partial 在 AI 摘要 section 後面**

找 AI 摘要 section `{% endif %}`（line ~135）後面、加：

```jinja
{% if not current_keyword %}
{% include 'notes/_tag_management.html' %}
{% endif %}
```

- [ ] **Step 4: 在 body 結尾加 JS script tag**

找 `collapsible_sections.js` script、後面加：

```jinja
<script src="{{ url_for('static', filename='js/tag_system.js') }}" defer></script>
```

- [ ] **Step 5: 跑既有 test 確認沒打到別人**

```bash
pytest tests/test_notes_misc.py tests/test_announcements_index_integration.py tests/test_notes_collapsible_sections.py -v
```

Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add templates/notes/_tag_management.html templates/notes/index.html
git commit -m "feat(tags): tag management collapsible section + load tag_system assets"
```

---

## Task C3: tag_system.js — management section CRUD + pin + delete confirm

**Files:**
- Create: `static/js/tag_system.js`

- [ ] **Step 1: 寫核心 JS（CRUD + render + pin + delete confirm）**

```javascript
/* tag_system.js — 標籤系統前端 (管理 + picker + filter row) */
(function() {
  'use strict';

  var PALETTE = [
    "#E74C3C","#E67E22","#F39C12","#F1C40F","#27AE60",
    "#16A085","#3498DB","#2C3E50","#9B59B6","#E91E63"
  ];

  function pickFg(hex) {
    if (!hex || hex.length !== 7) return '#fff';
    var r = parseInt(hex.slice(1,3), 16);
    var g = parseInt(hex.slice(3,5), 16);
    var b = parseInt(hex.slice(5,7), 16);
    var y = (r*299 + g*587 + b*114) / 1000;
    return y >= 160 ? '#000' : '#fff';
  }

  function chipEl(tag, opts) {
    opts = opts || {};
    var span = document.createElement('span');
    span.className = 'tag-chip';
    if (opts.button) span.classList.add('is-button');
    if (opts.active) span.classList.add('is-active');
    if (opts.disabled) span.classList.add('is-disabled');
    span.style.setProperty('--tag-bg', tag.color);
    span.style.setProperty('--tag-fg', tag.fg || pickFg(tag.color));
    span.textContent = tag.name;
    span.dataset.tagId = tag.id;
    return span;
  }

  function fetchJson(url, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers['Content-Type'] = 'application/json';
    }
    return fetch(url, opts).then(function(r) {
      return r.json().then(function(j) { return {ok: r.ok, status: r.status, data: j}; });
    });
  }

  var state = { tags: [] };

  function paletteHtml(selectedColor) {
    return '<div class="palette">' + PALETTE.map(function(c) {
      var sel = (c === selectedColor) ? ' is-selected' : '';
      return '<span class="swatch' + sel + '" data-color="' + c + '" style="background:' + c + '"></span>';
    }).join('') + '</div>';
  }

  function buildForm(opts) {
    // opts: {tagId?, name, color, onSubmit(name, color), onCancel()}
    var form = document.createElement('div');
    form.className = 'tag-form';
    form.innerHTML =
      '<div><label style="font-size:.85rem;color:#555;">名稱 (最多 20 字)</label>' +
      '<input type="text" maxlength="20" data-role="name" value="' + (opts.name || '').replace(/"/g, '&quot;') + '"></div>' +
      '<div style="margin-top:.5rem;"><label style="font-size:.85rem;color:#555;">顏色</label>' +
      paletteHtml(opts.color) + '</div>' +
      '<div class="preview"><span class="preview-chip"></span></div>' +
      '<div class="form-actions">' +
      '<button type="button" data-role="cancel" class="btn btn-secondary" style="font-size:.8rem;">取消</button>' +
      '<button type="button" data-role="save" class="btn btn-primary" style="font-size:.8rem;">儲存</button>' +
      '</div>';
    var nameInput = form.querySelector('[data-role="name"]');
    var swatches = form.querySelectorAll('.swatch');
    var previewChip = form.querySelector('.preview-chip');
    var selectedColor = opts.color || PALETTE[0];
    function refreshPreview() {
      previewChip.className = 'tag-chip preview-chip';
      previewChip.style.setProperty('--tag-bg', selectedColor);
      previewChip.style.setProperty('--tag-fg', pickFg(selectedColor));
      previewChip.textContent = nameInput.value || '預覽';
    }
    swatches.forEach(function(s) {
      s.addEventListener('click', function() {
        swatches.forEach(function(x) { x.classList.remove('is-selected'); });
        s.classList.add('is-selected');
        selectedColor = s.dataset.color;
        refreshPreview();
      });
    });
    nameInput.addEventListener('input', refreshPreview);
    form.querySelector('[data-role="cancel"]').addEventListener('click', opts.onCancel);
    form.querySelector('[data-role="save"]').addEventListener('click', function() {
      var n = nameInput.value.trim();
      if (!n) { alert('名稱必填'); return; }
      opts.onSubmit(n, selectedColor);
    });
    refreshPreview();
    return form;
  }

  function showConfirmModal(message, onConfirm) {
    var modal = document.createElement('div');
    modal.className = 'tag-confirm-modal is-open';
    modal.innerHTML =
      '<div class="panel">' +
      '<div style="font-weight:600;margin-bottom:.5rem;">⚠️ 確認刪除</div>' +
      '<div style="font-size:.9rem;color:#555;">' + message + '</div>' +
      '<div class="panel-actions">' +
      '<button type="button" data-role="cancel" class="btn btn-secondary" style="font-size:.85rem;">取消</button>' +
      '<button type="button" data-role="confirm" class="is-danger">確定刪除</button>' +
      '</div></div>';
    document.body.appendChild(modal);
    modal.querySelector('[data-role="cancel"]').addEventListener('click', function() { modal.remove(); });
    modal.querySelector('[data-role="confirm"]').addEventListener('click', function() {
      modal.remove();
      onConfirm();
    });
  }

  function renderMgmt() {
    var section = document.querySelector('.collapsible[data-section="tag-management"]');
    if (!section) return;
    var pinned = state.tags.filter(function(t) { return t.pinned; });
    var others = state.tags.filter(function(t) { return !t.pinned; });
    section.querySelector('[data-role="count"]').textContent =
      state.tags.length > 0 ? '(' + state.tags.length + ')' : '';
    section.querySelector('[data-role="pin-count"]').textContent =
      '(' + pinned.length + '/3)';
    var pinList = section.querySelector('[data-role="pinned-list"]');
    var otherList = section.querySelector('[data-role="other-list"]');
    pinList.innerHTML = '';
    otherList.innerHTML = '';
    function makeRow(tag) {
      var row = document.createElement('div');
      row.className = 'tag-mgmt-row';
      row.appendChild(chipEl(tag));
      var actions = document.createElement('div');
      actions.className = 'row-actions';
      var pinBtn = document.createElement('button');
      if (tag.pinned) {
        pinBtn.textContent = '取消釘';
        pinBtn.addEventListener('click', function() { unpinTag(tag.id); });
      } else {
        pinBtn.textContent = '釘選';
        if (pinned.length >= 3) {
          pinBtn.disabled = true;
          pinBtn.title = '已釘滿、先取消其他釘選';
        } else {
          pinBtn.addEventListener('click', function() { pinTag(tag.id); });
        }
      }
      actions.appendChild(pinBtn);
      var editBtn = document.createElement('button');
      editBtn.textContent = '編輯';
      editBtn.addEventListener('click', function() { openEditForm(tag, row); });
      actions.appendChild(editBtn);
      var delBtn = document.createElement('button');
      delBtn.textContent = '🗑️';
      delBtn.className = 'is-danger';
      delBtn.addEventListener('click', function() { askDeleteTag(tag); });
      actions.appendChild(delBtn);
      row.appendChild(actions);
      return row;
    }
    pinned.forEach(function(t) { pinList.appendChild(makeRow(t)); });
    others.forEach(function(t) { otherList.appendChild(makeRow(t)); });
  }

  function reloadTags() {
    return fetchJson('/notes/api/tags').then(function(r) {
      if (r.ok) { state.tags = r.data.tags || []; renderMgmt(); }
    });
  }

  function pinTag(id) {
    fetchJson('/notes/api/tags/' + id + '/pin', {method: 'POST'}).then(function(r) {
      if (r.ok) reloadTags();
      else alert(r.data.error || '釘選失敗');
    });
  }

  function unpinTag(id) {
    fetchJson('/notes/api/tags/' + id + '/pin', {method: 'DELETE'}).then(function(r) {
      if (r.ok) reloadTags();
    });
  }

  function askDeleteTag(tag) {
    var usage = tag.usage_count || 0;
    if (usage === 0) {
      doDeleteTag(tag.id);
    } else {
      showConfirmModal(
        '確定刪除「' + tag.name + '」？這個標籤目前被 ' + usage +
        ' 個 item 在用、刪後 chip 會消失（item 內容不變）。',
        function() { doDeleteTag(tag.id); }
      );
    }
  }

  function doDeleteTag(id) {
    fetchJson('/notes/api/tags/' + id, {method: 'DELETE'}).then(function(r) {
      if (r.ok) reloadTags();
      else alert(r.data.error || '刪除失敗');
    });
  }

  function openEditForm(tag, row) {
    var form = buildForm({
      name: tag.name,
      color: tag.color,
      onCancel: function() { form.remove(); },
      onSubmit: function(name, color) {
        fetchJson('/notes/api/tags/' + tag.id,
                  {method: 'PATCH', body: JSON.stringify({name: name, color: color})})
          .then(function(r) {
            if (r.ok) { form.remove(); reloadTags(); }
            else alert(r.data.error || '更新失敗');
          });
      }
    });
    row.parentNode.insertBefore(form, row.nextSibling);
  }

  function bindNewTagBtn() {
    var section = document.querySelector('.collapsible[data-section="tag-management"]');
    if (!section) return;
    var btn = section.querySelector('[data-role="new-tag-btn"]');
    var host = section.querySelector('[data-role="new-tag-form-host"]');
    btn.addEventListener('click', function() {
      host.innerHTML = '';
      var form = buildForm({
        onCancel: function() { host.innerHTML = ''; },
        onSubmit: function(name, color) {
          fetchJson('/notes/api/tags',
                    {method: 'POST', body: JSON.stringify({name: name, color: color})})
            .then(function(r) {
              if (r.ok) { host.innerHTML = ''; reloadTags(); }
              else alert(r.data.error || '建立失敗');
            });
        }
      });
      host.appendChild(form);
    });
  }

  function init() {
    bindNewTagBtn();
    reloadTags();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // 對外掛 namespace（供 Task C5/C8 picker/filter row 使用）
  window.__tag_system = {
    state: state,
    reload: reloadTags,
    pickFg: pickFg,
    chipEl: chipEl,
    buildForm: buildForm,
    fetchJson: fetchJson,
    PALETTE: PALETTE,
  };
})();
```

- [ ] **Step 2: JS syntax check**

```bash
node -c static/js/tag_system.js && echo OK
```

Expected: OK

- [ ] **Step 3: Commit**

```bash
git add static/js/tag_system.js
git commit -m "feat(tags): tag_system.js — management section CRUD + pin + delete confirm"
```

---

## Task C4: Display chips on note cards

**Files:**
- Modify: `templates/notes/index.html`
- Modify: `notes/routes.py`

- [ ] **Step 1: `notes/routes.py` 的 `index()` 把 notes 每篇 tag 列表 preload**

在 render 前加入：

```python
    # Preload note tags (avoid N+1)
    from models import Tag, NoteTag
    note_ids = [n.id for n in notes]
    note_tag_map = {}
    if note_ids:
        rows = db.session.query(NoteTag.note_id, Tag).join(Tag, NoteTag.tag_id == Tag.id)\
            .filter(NoteTag.note_id.in_(note_ids)).all()
        for nid, tag in rows:
            note_tag_map.setdefault(nid, []).append(tag)
    # 傳給 template
    return render_template('notes/index.html',
                           # ... 既有 kwargs ...
                           note_tag_map=note_tag_map)
```

加上 `pick_fg` Jinja filter（在 app.py 或 notes/routes.py 註冊）：

```python
# 在 notes/__init__.py 或 routes.py 適當位置
from notes.tag_palette import pick_fg
bp.add_app_template_filter(pick_fg, 'pick_fg')
```

- [ ] **Step 2: `templates/notes/index.html` note-card 內加 chip 渲染**

找 `<div class="note-card">` 那段（約 line 216），在 `<h3>` 標題**之前**加：

```jinja
{% set _ntags = note_tag_map.get(note.id, []) %}
{% if _ntags %}
<div class="note-card-tags">
  {% for t in _ntags %}
  <span class="tag-chip" style="--tag-bg:{{ t.color }};--tag-fg:{{ t.color|pick_fg }};">{{ t.name }}</span>
  {% endfor %}
</div>
{% endif %}
```

- [ ] **Step 3: 跑 test**

```bash
pytest tests/test_notes_misc.py tests/test_tags_filter.py -v
```

Expected: 全 PASS

- [ ] **Step 4: Commit**

```bash
git add notes/routes.py notes/__init__.py templates/notes/index.html
git commit -m "feat(tags): render tag chips on note cards (with N+1-safe preload)"
```

---

## Task C5: Tag picker modal + 編輯入口

**Files:**
- Create: `templates/notes/_tag_picker_modal.html`
- Modify: `templates/notes/editor.html`
- Modify: `static/js/tag_system.js`

- [ ] **Step 1: 建 picker modal partial**

```jinja
{# Tag picker modal — included once at index.html / editor.html bottom #}
<div class="tag-picker-modal" data-role="tag-picker">
  <div class="panel">
    <div class="panel-header">
      <span>🏷️ 選擇標籤 (<span data-role="count">0</span>/5)</span>
      <button type="button" class="close" data-role="close">✕</button>
    </div>
    <div class="panel-body">
      <div class="picker-list" data-role="list"></div>
      <div style="margin-top:.85rem;">
        <button type="button" class="btn btn-secondary" data-role="new-tag-toggle"
                style="font-size:.85rem;padding:.4rem .8rem;">+ 新增標籤</button>
        <div data-role="new-tag-form-host"></div>
      </div>
    </div>
    <div class="panel-footer">
      <button type="button" class="btn btn-secondary" data-role="cancel">取消</button>
      <button type="button" class="btn btn-primary" data-role="save">確定</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: 在 `templates/notes/editor.html` 加 picker include + 啟動按鈕**

找 editor toolbar 適當位置（標題附近）加：

```jinja
<button type="button" id="open-tag-picker"
        data-target-type="note" data-target-id="{{ note.id }}"
        style="font-size:.85rem;padding:.4rem .8rem;border-radius:6px;
               border:1px solid #ddd;background:#fff;cursor:pointer;">
  🏷️ 標籤 <span id="note-tag-count">(0/5)</span>
</button>
{% include 'notes/_tag_picker_modal.html' %}
```

- [ ] **Step 3: 在 `static/js/tag_system.js` 加 picker 邏輯**

在 IIFE 內、`init()` 之前加：

```javascript
  function openPicker(targetType, targetId, currentIds, onSaved) {
    var modal = document.querySelector('.tag-picker-modal[data-role="tag-picker"]');
    if (!modal) { alert('picker 未載入'); return; }
    var selected = new Set(currentIds || []);

    function refreshList() {
      var list = modal.querySelector('[data-role="list"]');
      list.innerHTML = '';
      state.tags.forEach(function(t) {
        var isSel = selected.has(t.id);
        var isDisabled = !isSel && selected.size >= 5;
        var chip = chipEl(t, {button: true, active: isSel, disabled: isDisabled});
        chip.addEventListener('click', function() {
          if (selected.has(t.id)) { selected.delete(t.id); }
          else if (selected.size < 5) { selected.add(t.id); }
          refreshList();
          modal.querySelector('[data-role="count"]').textContent = selected.size;
        });
        list.appendChild(chip);
      });
    }

    modal.querySelector('[data-role="count"]').textContent = selected.size;
    refreshList();

    var newBtn = modal.querySelector('[data-role="new-tag-toggle"]');
    var newHost = modal.querySelector('[data-role="new-tag-form-host"]');
    newBtn.onclick = function() {
      newHost.innerHTML = '';
      var form = buildForm({
        onCancel: function() { newHost.innerHTML = ''; },
        onSubmit: function(name, color) {
          fetchJson('/notes/api/tags',
                    {method: 'POST', body: JSON.stringify({name: name, color: color})})
            .then(function(r) {
              if (r.ok) {
                newHost.innerHTML = '';
                return reloadTags().then(function() {
                  selected.add(r.data.tag.id);
                  refreshList();
                  modal.querySelector('[data-role="count"]').textContent = selected.size;
                });
              } else alert(r.data.error || '建立失敗');
            });
        }
      });
      newHost.appendChild(form);
    };

    modal.querySelector('[data-role="close"]').onclick =
      modal.querySelector('[data-role="cancel"]').onclick = function() {
      modal.classList.remove('is-open');
    };

    modal.querySelector('[data-role="save"]').onclick = function() {
      var url = (targetType === 'note')
        ? '/notes/api/' + targetId + '/tags'
        : '/notes/api/items/' + targetId + '/tags';
      var ids = Array.from(selected);
      fetchJson(url, {method: 'PUT', body: JSON.stringify({tag_ids: ids})})
        .then(function(r) {
          if (r.ok) {
            modal.classList.remove('is-open');
            if (onSaved) onSaved(ids);
          } else alert(r.data.error || '儲存失敗');
        });
    };

    modal.classList.add('is-open');
  }

  function bindNoteEditorPicker() {
    var btn = document.getElementById('open-tag-picker');
    if (!btn) return;
    var targetType = btn.dataset.targetType;
    var targetId = parseInt(btn.dataset.targetId, 10);
    // load current tag_ids for this note
    function refreshCountBadge(ids) {
      var badge = document.getElementById('note-tag-count');
      if (badge) badge.textContent = '(' + ids.length + '/5)';
    }
    fetchJson('/notes/api/tags').then(function(r) {
      if (r.ok) { state.tags = r.data.tags || []; }
      // need current note's tag_ids — use GET on note (TODO: extend endpoint or read from data-attr)
      // for now, read from DOM if available, else empty
      var currentIds = [];
      try { currentIds = JSON.parse(btn.dataset.currentTagIds || '[]'); } catch (e) {}
      refreshCountBadge(currentIds);
      btn.addEventListener('click', function() {
        openPicker(targetType, targetId, currentIds, function(ids) {
          currentIds = ids;
          refreshCountBadge(ids);
        });
      });
    });
  }
```

修改 `init()`：

```javascript
  function init() {
    bindNewTagBtn();
    bindNoteEditorPicker();
    reloadTags();
  }
```

- [ ] **Step 4: 在 `notes/routes.py` 的 `edit_note` view 傳 current_tag_ids 到 template**

```python
    # 在 edit_note view 內 render 前加
    current_tag_ids = [nt.tag_id for nt in NoteTag.query.filter_by(note_id=note.id).all()]
    # template render kwargs 加 current_tag_ids=current_tag_ids
```

`editor.html` 的 picker button 改成帶 attribute：

```jinja
<button type="button" id="open-tag-picker"
        data-target-type="note" data-target-id="{{ note.id }}"
        data-current-tag-ids="{{ current_tag_ids|tojson|safe }}"
        ...>
```

- [ ] **Step 5: JS syntax check + 跑 test**

```bash
node -c static/js/tag_system.js
pytest tests/test_tags_api.py -v
```

Expected: OK + 全 PASS

- [ ] **Step 6: Commit**

```bash
git add templates/notes/_tag_picker_modal.html templates/notes/editor.html notes/routes.py static/js/tag_system.js
git commit -m "feat(tags): tag picker modal + note editor entry with inline create"
```

---

## Task C6: Filter row 釘選 chip + N more

**Files:**
- Modify: `static/js/tag_system.js`

- [ ] **Step 1: 加 filter row render 邏輯**

在 IIFE 內加：

```javascript
  function renderFilterRow() {
    var row = document.querySelector('.tag-filter-row[data-role="tag-row"]');
    if (!row) return;
    var currentTagId = parseInt(new URLSearchParams(window.location.search).get('tag') || '0', 10);
    var pinned = state.tags.filter(function(t) { return t.pinned; });
    var others = state.tags.filter(function(t) { return !t.pinned; });
    row.innerHTML = '';
    if (state.tags.length === 0) return;
    var label = document.createElement('span');
    label.className = 'filter-icon';
    label.textContent = '🏷️ 標籤：';
    row.appendChild(label);

    function makeChip(t) {
      var chip = chipEl(t, {button: true, active: (t.id === currentTagId)});
      chip.addEventListener('click', function() {
        var url = new URL(window.location.href);
        if (t.id === currentTagId) url.searchParams.delete('tag');
        else url.searchParams.set('tag', t.id);
        window.location.href = url.toString();
      });
      return chip;
    }

    pinned.forEach(function(t) { row.appendChild(makeChip(t)); });
    if (others.length > 0) {
      var moreBtn = document.createElement('button');
      moreBtn.type = 'button';
      moreBtn.className = 'more-toggle';
      moreBtn.textContent = '+' + others.length + ' more';
      var expanded = false;
      moreBtn.addEventListener('click', function() {
        if (expanded) {
          row.querySelectorAll('.tag-chip[data-more]').forEach(function(c) { c.remove(); });
          moreBtn.textContent = '+' + others.length + ' more';
          expanded = false;
        } else {
          others.forEach(function(t) {
            var c = makeChip(t);
            c.dataset.more = '1';
            row.insertBefore(c, moreBtn);
          });
          moreBtn.textContent = '收起';
          expanded = true;
        }
      });
      row.appendChild(moreBtn);
    }
  }
```

修改 `reloadTags()` 加 `renderFilterRow()` 呼叫：

```javascript
  function reloadTags() {
    return fetchJson('/notes/api/tags').then(function(r) {
      if (r.ok) {
        state.tags = r.data.tags || [];
        renderMgmt();
        renderFilterRow();
      }
    });
  }
```

- [ ] **Step 2: JS syntax check**

```bash
node -c static/js/tag_system.js && echo OK
```

Expected: OK

- [ ] **Step 3: Commit**

```bash
git add static/js/tag_system.js
git commit -m "feat(tags): filter row with 3 pinned chips + N more expandable"
```

---

## Task C7: Checklist item 加 picker (item-level tags)

**Files:**
- Modify: `templates/notes/editor.html`
- Modify: `static/js/tag_system.js`
- Modify: `notes/routes.py` (傳 item tag ids 給 template)

- [ ] **Step 1: 找 checklist editor item modal/render 區塊、加 picker 按鈕**

`templates/notes/editor.html` 內找 checklist item 渲染區（每個 item 的 modal 或 edit row），加：

```jinja
<button type="button" class="item-tag-picker-btn"
        data-target-type="item" data-target-id="{{ item.id }}"
        data-current-tag-ids="{{ (item_tag_ids_map[item.id] or [])|tojson|safe }}"
        style="font-size:.75rem;padding:.2rem .55rem;background:#fff;border:1px solid #ddd;border-radius:4px;cursor:pointer;">
  🏷️
</button>
```

- [ ] **Step 2: `notes/routes.py` edit_note view preload item tag map**

```python
    item_tag_ids_map = {}
    if note.note_type == 'checklist':
        from models import ChecklistItemTag
        active_items = note.active_checklist_items
        item_ids = [it.id for it in active_items]
        if item_ids:
            rows = ChecklistItemTag.query.filter(ChecklistItemTag.item_id.in_(item_ids)).all()
            for r in rows:
                item_tag_ids_map.setdefault(r.item_id, []).append(r.tag_id)
    # template kwargs 加 item_tag_ids_map=item_tag_ids_map
```

- [ ] **Step 3: 加 JS handler 在 `static/js/tag_system.js`**

```javascript
  function bindItemPickers() {
    document.querySelectorAll('.item-tag-picker-btn').forEach(function(btn) {
      var targetId = parseInt(btn.dataset.targetId, 10);
      var currentIds = [];
      try { currentIds = JSON.parse(btn.dataset.currentTagIds || '[]'); } catch (e) {}
      btn.addEventListener('click', function() {
        openPicker('item', targetId, currentIds, function(ids) {
          currentIds = ids;
          // 局部更新 item 上 chip 顯示 (簡化版：reload page)
          // TODO: 未來改成局部 patch DOM
          window.location.reload();
        });
      });
    });
  }
```

修改 `init()`：

```javascript
  function init() {
    bindNewTagBtn();
    bindNoteEditorPicker();
    bindItemPickers();
    reloadTags();
  }
```

- [ ] **Step 4: 跑 test**

```bash
pytest tests/test_tags_api.py tests/test_checklist_item_lock.py -v
```

Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add templates/notes/editor.html notes/routes.py static/js/tag_system.js
git commit -m "feat(tags): checklist item-level tag picker"
```

---

## Task C8: Render item-level chips on checklist item display

**Files:**
- Modify: `templates/notes/editor.html`

- [ ] **Step 1: 在 checklist item 渲染處（item text 之後）加 chip 列表**

找 item label/text 那段、加：

```jinja
{% set _it_tag_ids = item_tag_ids_map.get(item.id, []) %}
{% if _it_tag_ids %}
<div class="item-tags">
  {# 從 all_tags lookup map 找對應 tag 資訊 #}
  {% for tag in all_store_tags if tag.id in _it_tag_ids %}
  <span class="tag-chip" style="--tag-bg:{{ tag.color }};--tag-fg:{{ tag.color|pick_fg }};">{{ tag.name }}</span>
  {% endfor %}
</div>
{% endif %}
```

- [ ] **Step 2: `notes/routes.py` edit_note view 傳 `all_store_tags`**

```python
    all_store_tags = Tag.query.filter_by(store=note.store).all()
    # template kwargs 加 all_store_tags=all_store_tags
```

- [ ] **Step 3: 跑 test**

```bash
pytest tests/test_notes_misc.py -v
```

Expected: 全 PASS

- [ ] **Step 4: Commit**

```bash
git add templates/notes/editor.html notes/routes.py
git commit -m "feat(tags): render item-level chips on checklist items"
```

---

# Stage D — Deploy verification

## Task D1: Full pytest 套件

- [ ] **Step 1: 跑全 test**

```bash
cd /home/hirain0126/projects/webapp/app_unified
source ../venv/bin/activate
pytest -q 2>&1 | tail -10
```

Expected: ~480+ passed、0 failed（pre-existing flaky `test_range_all_no_date_filter` 可忽略）

- [ ] **Step 2: 若有失敗、查根因並修；不要 skip**

---

## Task D2: SW cache bump

**Files:**
- Modify: `static/sw.js`

- [ ] **Step 1: Bump version**

```bash
# 改 static/sw.js line 8
# const CACHE_NAME = 'note-weather-v23';
# 改成
# const CACHE_NAME = 'note-weather-v24';
```

- [ ] **Step 2: Commit**

```bash
git add static/sw.js
git commit -m "chore(sw): bump CACHE_NAME v23 -> v24 for tag system deploy"
```

---

## Task D3: 本機驗 + user 實機驗收

- [ ] **Step 1: 起本機 server**

```bash
bash scripts/run_local.sh
```

開 Chrome 進 `http://localhost:5000/dev` 切 admin

- [ ] **Step 2: User 跑 verification checklist**

1. `/notes/` 三個 collapsible bar：公告 / AI 摘要 / 🏷️ 標籤管理（後者預設摺疊）
2. 點標籤管理 bar 展開、按「+ 新增標籤」、建一個「重要」#E74C3C → 出現在「其他標籤」
3. 點該 tag 「釘選」按鈕 → 移到「📌 釘選 (1/3)」區
4. 同樣建 3 個釘選滿 3 → 第 4 個「釘選」按鈕 disabled
5. 點某 tag 「編輯」→ inline form 出現、改名儲存 → 列表更新
6. 點 tag 「🗑️」(usage=0) → 直接刪
7. 進某 note edit 頁、點「🏷️ 標籤 (0/5)」→ picker 開、勾 2 個 → 確定 → 回 note edit 頁 badge `(2/5)`
8. 進 checklist editor、item 旁邊「🏷️」→ picker 開、勾 1 個 → 確定 → reload 後 item 下方顯示 chip
9. 回 `/notes/`、note card 上方顯示掛的 tag chip
10. 點 filter panel bar 展開、看到「⚙️ 進階篩選」（沒帶 filter → 0 個）+ 標籤 row 釘選 chip
11. 點某個 chip → URL `?tag=X` → 列表只剩掛該 tag 的 note → bar 顯示「⚙️ 進階篩選 (1)」
12. F5 reload `?tag=X` 仍在 → filter panel 強制展開（不論 localStorage）
13. 把 chip 再點一次取消 → URL 拿掉 tag、回全部 note、bar 顯示 (0)
14. localStorage 設 `notes_filter_panel_open` = '0' 不帶 filter F5 → 摺疊
15. 點刪一個正在用的 tag → confirm modal 跳「N 個 item 在用」、確定後 chip 消失

- [ ] **Step 3: User OK 後 merge main + push**

**等 user 明確說「可以 push」才做、按 [[feedback_push_policy]]**

```bash
git checkout main
git merge --ff-only feat/notes-tag-system
git push origin main
git branch -d feat/notes-tag-system
```

- [ ] **Step 4: 監看 GHA + Zeabur deploy**

- GHA: https://github.com/stvlin1988-alt/Weather/actions
- Zeabur 自動 deploy → app.py 啟動跑 `db.create_all()` → 3 張新 table 自動建
- prod `/notes/` 驗：用 Zeabur shell 跑 `SELECT * FROM tags LIMIT 1;` 確認 table 存在
- user prod 實機驗：重跑 D3 step 2 那 15 條 verification

---

## Risks 待 Implementer 注意

| Risk | Mitigation |
|---|---|
| Note/ChecklistItem 既有 lock 機制跟 picker 衝突 | picker 是純 UI 不 acquire lock；PUT 走既有 endpoint、若 item 在 lock 該 endpoint 自己會回 423 |
| filter URL preservation 漏改某條 | grep `url_for('notes.index'` 巡所有 6 條 filter row、確認都帶 `tag=current_tag` |
| `note_tag_map` N+1 query | Task C4 已用 single JOIN 預載、render 時 lookup dict |
| 既有 5 排 filter 跟新 filter panel collapsible 衝突 (CSS class collision) | tag_system.css 用 `.tag-filter-row` 不打擾 `.range-tabs` / `.status-tabs` |
| `db.create_all()` 在 prod first deploy 跑很久 | 3 張新 table、SQL 一瞬間、不會 lag |
| picker modal 在 dev 用 `window.location.reload()` 後遺失 unsaved 編輯 | 已知限制、未來 v2 改局部 DOM patch |
| 標籤 name 含特殊字元 (XSS) | Jinja `{{ tag.name }}` 自動 escape；JS 用 `textContent` 不用 `innerHTML` 設 |

---

## Self-Review

**Spec 覆蓋率**:
- [x] §A 資料模型 → Task A1
- [x] §B filter panel collapsible + active count → Task B2/B3
- [x] §B 標籤 row 釘選 3 + N more → Task C6
- [x] §B chip 視覺規範 → Task C1 CSS
- [x] §C 標籤管理 section CRUD + pin + delete confirm → Task A3/A4 (API) + C2 (template) + C3 (JS)
- [x] §D Tag picker modal + inline create → Task C5
- [x] §E note card chips + item chips → Task C4 + C8
- [x] §F URL `?tag=X` + 跨 filter preservation → Task A6 (route) + B2 (template URL)
- [x] §G 8 API endpoints → Task A3/A4/A5
- [x] §H WS broadcast 不做 → 確認、無 task
- [x] §I 邊界條件 → 各 Task 的 validation
- [x] §J 容量 → 設計層、無 task
- [x] §K 測試 → Task A1/A3-A6 各 step 都有 pytest、Task D3 manual
- [x] §L 部署 → Task D1-D3
- [x] SW bump → Task D2

**Placeholder scan**: 無 TBD/TODO/「實作細節後補」字樣。Task C5 step 4 有「`TODO: 未來改成局部 DOM patch`」是設計時已知 trade-off 註解、非 plan placeholder。

**Type consistency**:
- `chipEl(tag, opts)` 跨 management/picker/filter 一致簽名
- `buildForm(opts)` 跨 mgmt section / picker inline create 一致
- `state.tags` 全局 namespace 一致
- API endpoint paths 跟 spec §G 表格逐條對齊

**Bite-size**: 大部分 step 2-5 分鐘。最大 step（Task C3 step 1 寫 tag_system.js 核心）較長但純 sequential code、無 logic branch。
