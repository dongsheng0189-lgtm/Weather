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


def _stamp(row):
    """把「誰在什麼時候掛的」蓋到標籤關聯列上。

    快速標籤鍵與標籤選擇器兩條路都會呼叫，確保兩邊記錄的內容一致。
    """
    row.tagged_by = current_user.id
    row.tagged_by_name = current_user.username
    row.tagged_at = datetime.utcnow()
    return row


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


@notes_bp.route('/api/tags/<int:tag_id>/pin', methods=['POST'])
@login_required
def pin_tag(tag_id):
    t = db.session.get(Tag, tag_id)
    if not t:
        abort(404)
    _require_same_store(t)
    if t.pinned:
        return jsonify({'tag': _tag_to_dict(t, include_usage=True)})
    pinned_count = Tag.query.filter_by(store=t.store, pinned=True).count()
    if pinned_count >= PIN_LIMIT:
        return jsonify({'error': f'釘選已滿 {PIN_LIMIT} 個、先取消其他釘選'}), 400
    t.pinned = True
    db.session.commit()
    return jsonify({'tag': _tag_to_dict(t, include_usage=True)})


@notes_bp.route('/api/tags/<int:tag_id>/pin', methods=['DELETE'])
@login_required
def unpin_tag(tag_id):
    t = db.session.get(Tag, tag_id)
    if not t:
        abort(404)
    _require_same_store(t)
    t.pinned = False
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


@notes_bp.route('/api/<int:note_id>/tags', methods=['PUT'])
@login_required
def set_note_tags(note_id):
    n = db.session.get(Note, note_id)
    if not n:
        abort(404)
    if current_user.role != 'super_admin' and n.store != current_user.store:
        abort(403)
    data = request.get_json(silent=True) or {}
    tag_ids = data.get('tag_ids') or []
    if not isinstance(tag_ids, list) or len(tag_ids) > MAX_TAGS_PER_ITEM:
        return jsonify({'error': f'最多 {MAX_TAGS_PER_ITEM} 個標籤'}), 400
    tag_ids = list(set(tag_ids))  # de-dup
    tags = Tag.query.filter(Tag.id.in_(tag_ids)).all() if tag_ids else []
    if len(tags) != len(tag_ids):
        return jsonify({'error': '部分標籤不存在'}), 400
    for t in tags:
        if t.store != n.store:
            return jsonify({'error': '跨店標籤'}), 400
    # 差集改寫，理由同 set_item_tags。
    existing = {r.tag_id: r for r in NoteTag.query.filter_by(note_id=n.id).all()}
    wanted = set(tag_ids)
    for tid, row in existing.items():
        if tid not in wanted:
            db.session.delete(row)
    for tid in wanted - set(existing):
        db.session.add(_stamp(NoteTag(note_id=n.id, tag_id=tid)))
    db.session.commit()
    return jsonify({'tag_ids': tag_ids})


@notes_bp.route('/api/items/<int:item_id>/tags', methods=['PUT'])
@login_required
def set_item_tags(item_id):
    item = db.session.get(ChecklistItem, item_id)
    if not item:
        abort(404)
    note = db.session.get(Note, item.note_id)
    if not note:
        abort(404)
    if current_user.role != 'super_admin' and note.store != current_user.store:
        abort(403)
    data = request.get_json(silent=True) or {}
    tag_ids = data.get('tag_ids') or []
    if not isinstance(tag_ids, list) or len(tag_ids) > MAX_TAGS_PER_ITEM:
        return jsonify({'error': f'最多 {MAX_TAGS_PER_ITEM} 個標籤'}), 400
    tag_ids = list(set(tag_ids))
    tags = Tag.query.filter(Tag.id.in_(tag_ids)).all() if tag_ids else []
    if len(tags) != len(tag_ids):
        return jsonify({'error': '部分標籤不存在'}), 400
    for t in tags:
        if t.store != note.store:
            return jsonify({'error': '跨店標籤'}), 400
    # 差集改寫，不可退回「全刪重插」—— 那會把 tagged_by / tagged_at 靜默洗掉。
    existing = {r.tag_id: r for r in
                ChecklistItemTag.query.filter_by(item_id=item.id).all()}
    wanted = set(tag_ids)
    for tid, row in existing.items():
        if tid not in wanted:
            db.session.delete(row)
    for tid in wanted - set(existing):
        db.session.add(_stamp(ChecklistItemTag(item_id=item.id, tag_id=tid)))
    db.session.commit()
    return jsonify({'tag_ids': tag_ids})


# ─────────────────────────────────────────────────────────────
# 快速標籤鍵（2026-09-08）
#
# 專用的單標籤 toggle 端點。刻意**不**重用 `PUT .../tags` —— 那個吃的是整份
# 清單，前端得先知道目前有哪些標籤才能送，兩個人同時操作會互相覆蓋。
#
# ⚠️ 這些端點刻意**不觸碰** item.updated_at / note.updated_at。項目與筆記列表
# 都按 updated_at DESC 排序，碰了就會讓使用者交接時逐項按下去、把整張清單的
# 順序打亂。有測試守著（test_quick_tag_api.py）。
# ─────────────────────────────────────────────────────────────

def _assignment_to_dict(row):
    return {
        'tag_id': row.tag_id,
        'tagged_by_name': row.tagged_by_name,
        'tagged_at': row.tagged_at.isoformat() if row.tagged_at else None,
    }


def _resolve_item_and_tag(item_id, tag_id):
    """回傳 (item, note, tag)。權限或資料不符時直接 abort / 回傳錯誤 response。"""
    item = ChecklistItem.active().filter_by(id=item_id).first()
    if not item:
        abort(404)
    note = db.session.get(Note, item.note_id)
    if not note or note.deleted_at is not None:
        abort(404)
    if current_user.role != 'super_admin' and note.store != current_user.store:
        abort(403)
    tag = db.session.get(Tag, tag_id)
    if not tag:
        abort(404)
    return item, note, tag


@notes_bp.route('/api/items/<int:item_id>/tags/<int:tag_id>', methods=['POST'])
@login_required
def quick_add_item_tag(item_id, tag_id):
    item, note, tag = _resolve_item_and_tag(item_id, tag_id)
    if tag.store != note.store:
        return jsonify({'error': '跨店標籤'}), 400
    existing = ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).first()
    if existing:
        return jsonify(_assignment_to_dict(existing))   # 連按兩次不視為錯誤
    if ChecklistItemTag.query.filter_by(item_id=item.id).count() >= MAX_TAGS_PER_ITEM:
        return jsonify({'error': f'此項目已有 {MAX_TAGS_PER_ITEM} 個標籤，請先移除其他標籤'}), 400
    row = _stamp(ChecklistItemTag(item_id=item.id, tag_id=tag.id))
    db.session.add(row)
    db.session.commit()
    return jsonify(_assignment_to_dict(row))


@notes_bp.route('/api/items/<int:item_id>/tags/<int:tag_id>', methods=['DELETE'])
@login_required
def quick_remove_item_tag(item_id, tag_id):
    item, note, tag = _resolve_item_and_tag(item_id, tag_id)
    # 與 POST 對稱：跨店標籤一律拒絕。目前寫入路徑都擋了跨店、不可能存在這種
    # 關聯列，但少了這道檢查，呼叫者能用 200/404 的差別探出別店有哪些 tag_id。
    if tag.store != note.store:
        return jsonify({'error': '跨店標籤'}), 400
    ChecklistItemTag.query.filter_by(item_id=item.id, tag_id=tag.id).delete()
    db.session.commit()
    return jsonify({'ok': True})


def _resolve_note_and_tag(note_id, tag_id):
    note = db.session.get(Note, note_id)
    if not note or note.deleted_at is not None:
        abort(404)
    if current_user.role != 'super_admin' and note.store != current_user.store:
        abort(403)
    tag = db.session.get(Tag, tag_id)
    if not tag:
        abort(404)
    return note, tag


@notes_bp.route('/api/<int:note_id>/tags/<int:tag_id>', methods=['POST'])
@login_required
def quick_add_note_tag(note_id, tag_id):
    note, tag = _resolve_note_and_tag(note_id, tag_id)
    if tag.store != note.store:
        return jsonify({'error': '跨店標籤'}), 400
    existing = NoteTag.query.filter_by(note_id=note.id, tag_id=tag.id).first()
    if existing:
        return jsonify(_assignment_to_dict(existing))
    if NoteTag.query.filter_by(note_id=note.id).count() >= MAX_TAGS_PER_ITEM:
        return jsonify({'error': f'此筆記已有 {MAX_TAGS_PER_ITEM} 個標籤，請先移除其他標籤'}), 400
    row = _stamp(NoteTag(note_id=note.id, tag_id=tag.id))
    db.session.add(row)
    db.session.commit()
    return jsonify(_assignment_to_dict(row))


@notes_bp.route('/api/<int:note_id>/tags/<int:tag_id>', methods=['DELETE'])
@login_required
def quick_remove_note_tag(note_id, tag_id):
    note, tag = _resolve_note_and_tag(note_id, tag_id)
    if tag.store != note.store:
        return jsonify({'error': '跨店標籤'}), 400
    NoteTag.query.filter_by(note_id=note.id, tag_id=tag.id).delete()
    db.session.commit()
    return jsonify({'ok': True})
