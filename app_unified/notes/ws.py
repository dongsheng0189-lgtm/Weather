from datetime import datetime, timedelta
from flask import request
from flask_login import current_user
from flask_socketio import emit
from extensions import db
from models import Note, Store, NoteLog, User, STATUS_CHOICES, CATEGORY_CHOICES
from tw_time import tw_iso


def register_ws_events(socketio):

    @socketio.on('connect')
    def handle_connect():
        if not current_user.is_authenticated:
            return False

    @socketio.on('d')
    def handle_data(data):
        if not current_user.is_authenticated:
            emit('r', {'op': 'er', 'message': 'unauthorized'})
            return
        op = data.get('op', '')
        try:
            if op == 'ln': _list_notes(data)
            elif op == 'cn': _create_note(data)
            elif op == 'un': _update_note(data)
            elif op == 'dn': _delete_note(data)
            elif op == 'gn': _get_note(data)
            elif op == 'as': _ai_summary(data)
            elif op == 'jn': _join_note(data)
            else: emit('r', {'op': 'er', 'message': 'unknown op'})
        except Exception as e:
            emit('r', {'op': 'er', 'message': str(e)})

    @socketio.on('p')
    def handle_padding(data):
        pass

    def _get_stores():
        return [s.name for s in Store.query.order_by(Store.name).all()]

    def _list_notes(data):
        store_filter = data.get('store', '')
        status_filter = data.get('status', '')
        range_param = data.get('range', '')
        category_filter = data.get('category', '')
        shift_filter = data.get('shift', '')
        keyword = (data.get('q') or '').strip()
        stores = _get_stores()
        query = Note.active()
        if current_user.is_super_admin():
            if store_filter in stores:
                query = query.filter_by(store=store_filter)
        elif current_user.is_admin():
            query = query.filter_by(store=current_user.store)
        else:
            query = query.filter_by(store=current_user.store)

        # 互斥篩選：關鍵字 > 種類 > 班別 > 狀態 > 日期範圍
        from models import CATEGORY_CHOICES, SHIFT_CHOICES
        keyword_mode = False
        if keyword:
            keyword_mode = True  # title/content 加密 → Python 端 filter
        elif category_filter and category_filter in CATEGORY_CHOICES:
            query = query.filter_by(category=category_filter)
        elif shift_filter and shift_filter in SHIFT_CHOICES:
            query = query.filter_by(shift=shift_filter)
        elif 'status' in data and data.get('status') is not None:
            if status_filter in STATUS_CHOICES:
                query = query.filter_by(status=status_filter)
            # status='' (全部狀態) → 不加 filter
        else:
            from notes.routes import RANGE_DAYS, _get_business_day_range
            key = range_param if range_param else 'today'
            days = RANGE_DAYS.get(key, 3)
            if days is None:
                # range=all → 不過濾日期
                pass
            elif days == 0:
                start_utc, end_utc = _get_business_day_range()
                query = query.filter(Note.updated_at >= start_utc, Note.updated_at < end_utc)
            else:
                since = datetime.utcnow() - timedelta(days=days)
                query = query.filter(Note.updated_at >= since)

        notes = query.order_by(Note.updated_at.desc()).all()
        if keyword_mode:
            kl = keyword.lower()
            notes = [n for n in notes if kl in (n.title or "").lower() or kl in (n.content or "").lower()
                     or any(kl in (item.text or "").lower() for item in (n.checklist_items or []))]
        emit('r', {'op': 'ln', 'notes': [{
            'id': n.id, 'title': n.title, 'content': n.content,
            'note_type': n.note_type or 'note',
            'store': n.store, 'status': n.status or 'pending',
            'category': n.category or 'misc',
            'shift': n.shift,
            'author': n.display_author,
            'created_at': tw_iso(n.created_at),
            'updated_at': tw_iso(n.updated_at),
        } for n in notes]})

    def _create_note(data):
        stores = _get_stores()
        now = datetime.utcnow()
        if current_user.is_super_admin():
            store = data.get('store') if data.get('store') in stores else None
        elif current_user.is_admin():
            store = current_user.store
        else:
            store = current_user.store if current_user.store in stores else None
        status = data.get('status') if data.get('status') in STATUS_CHOICES else 'pending'
        category = data.get('category') if data.get('category') in CATEGORY_CHOICES else 'misc'
        is_ai_summary = data.get('is_ai_summary') is True
        note_type = data.get('note_type') if data.get('note_type') in ('note', 'checklist') else 'note'
        from models import SHIFT_CHOICES
        shift = data.get('shift') if data.get('shift') in SHIFT_CHOICES else None
        note = Note(
            user_id=current_user.id,
            author_name=current_user.username,
            note_type=note_type,
            title=data.get('title', '未命名筆記'),
            content=data.get('content', ''),
            store=store, status=status, category=category, shift=shift,
            is_ai_summary=is_ai_summary,
            created_at=now, updated_at=now,
        )
        db.session.add(note)
        db.session.commit()
        emit('r', {'op': 'cn', 'status': 'ok', 'id': note.id})

    def _update_note(data):
        note_id = data.get('id')
        if current_user.is_super_admin():
            note = Note.active().filter_by(id=note_id).first()
        elif current_user.is_admin():
            note = Note.active().filter_by(id=note_id, store=current_user.store).first()
        else:
            note = Note.active().filter_by(id=note_id, store=current_user.store).first()
        if not note:
            emit('r', {'op': 'er', 'message': 'not found'})
            return
        stores = _get_stores()
        diff_parts = []
        if 'title' in data and data['title'] != note.title:
            diff_parts.append(f"標題: {note.title} → {data['title']}")
            note.title = data['title']
        if 'content' in data and data['content'] != note.content:
            from notes.diff_utils import compute_paragraph_diff, PARAGRAPH_DIFF_PREFIX
            pdiff = compute_paragraph_diff(note.content, data['content'])
            if pdiff:
                diff_parts.append(f"{PARAGRAPH_DIFF_PREFIX}{pdiff}")
            else:
                diff_parts.append(f"內容變更（全文）")
            note.content = data['content']
        if 'store' in data and current_user.is_super_admin():
            note.store = data['store'] if data['store'] in stores else None
        if 'status' in data and data['status'] in STATUS_CHOICES:
            if data['status'] != note.status:
                diff_parts.append(f"狀態: {note.status} → {data['status']}")
            note.status = data['status']
        if 'category' in data and data['category'] in CATEGORY_CHOICES:
            if data['category'] != note.category:
                diff_parts.append(f"類別: {note.category} → {data['category']}")
            note.category = data['category']
        # 編輯鎖：lazy acquire（同 HTTP /api/<id> PUT 邏輯）
        from notes.routes import _ensure_lock_for_mutation
        ok, locker = _ensure_lock_for_mutation(note, current_user.id)
        if not ok:
            emit('r', {'op': 'un', 'status': 'error',
                       'message': f"目前由 {locker.username if locker else '他人'} 編輯中"})
            return
        note.updated_by = current_user.id
        note.updated_at = datetime.utcnow()
        # 儲存後釋放鎖
        note.locked_by = None
        note.locked_at = None
        db.session.flush()
        if diff_parts:
            log = NoteLog(note_id=note.id, note_title=note.title, user_id=current_user.id, action='edit', diff='; '.join(diff_parts))
            db.session.add(log)
        db.session.commit()
        # 廣播鎖釋放給同筆記的其他 client (排除自己)
        from extensions import socketio
        socketio.emit('r', {'op': 'lr', 'note_id': note.id, 'by': current_user.id},
                      to=f'note-{note.id}', include_self=False)
        emit('r', {'op': 'un', 'status': 'ok'})

    def _delete_note(data):
        from storage import delete_attachment as r2_delete
        note_id = data.get('id')
        if current_user.is_super_admin():
            note = Note.active().filter_by(id=note_id).first()
        elif current_user.is_admin():
            note = Note.active().filter_by(id=note_id, store=current_user.store).first()
        else:
            note = Note.active().filter_by(id=note_id, store=current_user.store).first()
        if not note:
            emit('r', {'op': 'er', 'message': 'not found'})
            return

        # 編輯鎖：別人持鎖中時不可刪除
        from notes.routes import _ensure_lock_for_mutation
        ok, locker = _ensure_lock_for_mutation(note, current_user.id)
        if not ok:
            emit('r', {'op': 'dn', 'status': 'error',
                       'message': f"目前由 {locker.username if locker else '他人'} 編輯中"})
            return

        for att in note.attachments:
            r2_delete(att.object_key)

        log = NoteLog(note_id=note.id, note_title=note.title, user_id=current_user.id, action='delete')
        db.session.add(log)
        db.session.delete(note)
        db.session.commit()
        emit('r', {'op': 'dn', 'status': 'ok'})

    def _get_note(data):
        note_id = data.get('id')
        if current_user.is_super_admin():
            note = Note.active().filter_by(id=note_id).first()
        elif current_user.is_admin():
            note = Note.active().filter_by(id=note_id, store=current_user.store).first()
        else:
            note = Note.active().filter_by(id=note_id, store=current_user.store).first()
        if not note:
            emit('r', {'op': 'er', 'message': 'not found'})
            return
        updater = None
        if note.updated_by:
            u = User.query.get(note.updated_by)
            updater = u.username if u else None
        emit('r', {'op': 'gn', 'note': {
            'id': note.id, 'title': note.title, 'content': note.content,
            'store': note.store, 'status': note.status or 'pending',
            'category': note.category or 'misc',
            'shift': note.shift,
            'updated_by': updater,
            'created_at': tw_iso(note.created_at),
            'updated_at': tw_iso(note.updated_at),
        }})

    def _ai_summary(data):
        # AI 摘要已改為 HTTP 異步處理，WebSocket 不再直接呼叫 LLM
        emit('r', {'op': 'er', 'message': '請使用 HTTP 介面'})

    def _join_note(data):
        from flask_socketio import join_room
        nid = data.get('id')
        if not nid:
            emit('r', {'op': 'er', 'message': 'missing id'})
            return
        # 權限檢查（同 _get_note）
        if current_user.is_super_admin():
            note = Note.active().filter_by(id=nid).first()
        elif current_user.is_admin():
            note = Note.active().filter_by(id=nid, store=current_user.store).first()
        else:
            note = Note.active().filter_by(id=nid, store=current_user.store).first()
        if not note:
            emit('r', {'op': 'er', 'message': 'not found'})
            return
        join_room(f'note-{nid}')
        emit('r', {'op': 'jn', 'status': 'ok', 'note_id': nid})
