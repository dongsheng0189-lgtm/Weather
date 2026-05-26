"""Tag CRUD + pin + assignment endpoints."""
from datetime import datetime
from flask import request, jsonify, abort
from flask_login import login_required, current_user

from extensions import db
from models import Tag, NoteTag, ChecklistItemTag, Note, ChecklistItem
from notes.routes import notes_bp
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
        s = request.args.get('store')
        if s:
            return s
    return current_user.store


def _require_same_store(tag):
    eff = _effective_store()
    if not eff or tag.store != eff:
        abort(403)


@notes_bp.route('/api/tags', methods=['GET'])
@login_required
def list_tags():
    store = _effective_store()
    if not store:
        return jsonify({'tags': []})
    tags = Tag.query.filter_by(store=store).order_by(Tag.pinned.desc(), Tag.name).all()
    return jsonify({'tags': [_tag_to_dict(t, include_usage=True) for t in tags]})


@notes_bp.route('/api/tags', methods=['POST'])
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


@notes_bp.route('/api/tags/<int:tag_id>', methods=['PATCH'])
@login_required
def update_tag(tag_id):
    t = db.session.get(Tag, tag_id)
    if not t:
        abort(404)
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


@notes_bp.route('/api/tags/<int:tag_id>', methods=['DELETE'])
@login_required
def delete_tag(tag_id):
    t = db.session.get(Tag, tag_id)
    if not t:
        abort(404)
    _require_same_store(t)
    n_count = NoteTag.query.filter_by(tag_id=t.id).count()
    i_count = ChecklistItemTag.query.filter_by(tag_id=t.id).count()
    db.session.delete(t)  # cascade clears m2m
    db.session.commit()
    return jsonify({'ok': True, 'cascaded': n_count + i_count})
