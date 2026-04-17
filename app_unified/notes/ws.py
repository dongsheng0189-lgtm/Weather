from datetime import datetime, timedelta
from flask import request
from flask_login import current_user
from flask_socketio import emit
from extensions import db
from models import Note, Store, NoteLog, User, STATUS_CHOICES, PRIORITY_CHOICES


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
        priority_filter = data.get('priority', '')
        keyword = (data.get('q') or '').strip()
        stores = _get_stores()
        query = Note.query
        if current_user.is_super_admin():
            if store_filter in stores:
                query = query.filter_by(store=store_filter)
        elif current_user.is_admin():
            query = query.filter_by(store=current_user.store)
        else:
            query = query.filter_by(store=current_user.store)

        # 互斥篩選：關鍵字 > 優先權 > 狀態 > 日期範圍
        from models import PRIORITY_CHOICES
        keyword_mode = False
        if keyword:
            keyword_mode = True  # title/content 加密 → Python 端 filter
        elif priority_filter and priority_filter in PRIORITY_CHOICES:
            query = query.filter_by(priority=priority_filter)
        elif 'status' in data and data.get('status') is not None:
            if status_filter in STATUS_CHOICES:
                query = query.filter_by(status=status_filter)
            # status='' (全部狀態) → 不加 filter
        else:
            from notes.routes import _get_business_day_range
            range_days = {'today': 0, '3d': 3, '5d': 5, '7d': 7}
            days = range_days.get(range_param if range_param else 'today', 3)
            if days == 0:
                start_utc, end_utc = _get_business_day_range()
                query = query.filter(Note.updated_at >= start_utc, Note.updated_at < end_utc)
            else:
                since = datetime.utcnow() - timedelta(days=days)
                query = query.filter(Note.updated_at >= since)

        notes = query.order_by(Note.updated_at.desc()).all()
        if keyword_mode:
            kl = keyword.lower()
            notes = [n for n in notes if kl in (n.title or "").lower() or kl in (n.content or "").lower()]
        emit('r', {'op': 'ln', 'notes': [{
            'id': n.id, 'title': n.title, 'content': n.content,
            'note_type': n.note_type or 'note',
            'store': n.store, 'status': n.status or 'pending',
            'priority': n.priority or 'medium',
            'author': n.display_author,
            'created_at': n.created_at.isoformat() if n.created_at else '',
            'updated_at': n.updated_at.isoformat() if n.updated_at else '',
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
        priority = data.get('priority') if data.get('priority') in PRIORITY_CHOICES else 'medium'
        note_type = data.get('note_type') if data.get('note_type') in ('note', 'checklist') else 'note'
        note = Note(
            user_id=current_user.id,
            author_name=current_user.username,
            note_type=note_type,
            title=data.get('title', '未命名筆記'),
            content=data.get('content', ''),
            store=store, status=status, priority=priority,
            created_at=now, updated_at=now,
        )
        db.session.add(note)
        db.session.commit()
        emit('r', {'op': 'cn', 'status': 'ok', 'id': note.id})

    def _update_note(data):
        note_id = data.get('id')
        if current_user.is_super_admin():
            note = Note.query.get(note_id)
        elif current_user.is_admin():
            note = Note.query.filter_by(id=note_id, store=current_user.store).first()
        else:
            note = Note.query.filter_by(id=note_id, store=current_user.store).first()
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
        if 'priority' in data and data['priority'] in PRIORITY_CHOICES:
            if data['priority'] != note.priority:
                diff_parts.append(f"優先度: {note.priority} → {data['priority']}")
            note.priority = data['priority']
        # 編輯鎖檢查
        if note.locked_by and note.locked_by != current_user.id:
            from datetime import timedelta as _td
            age = (datetime.utcnow() - note.locked_at).total_seconds() if note.locked_at else 9999
            if age < 5 * 60:
                emit('r', {'op': 'un', 'status': 'error', 'message': '目前由他人編輯中'})
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
        emit('r', {'op': 'un', 'status': 'ok'})

    def _delete_note(data):
        from storage import delete_attachment as r2_delete
        note_id = data.get('id')
        if current_user.is_super_admin():
            note = Note.query.get(note_id)
        elif current_user.is_admin():
            note = Note.query.filter_by(id=note_id, store=current_user.store).first()
        else:
            note = Note.query.filter_by(id=note_id, store=current_user.store).first()
        if not note:
            emit('r', {'op': 'er', 'message': 'not found'})
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
            note = Note.query.get(note_id)
        elif current_user.is_admin():
            note = Note.query.filter_by(id=note_id, store=current_user.store).first()
        else:
            note = Note.query.filter_by(id=note_id, store=current_user.store).first()
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
            'priority': note.priority or 'medium', 'updated_by': updater,
            'created_at': note.created_at.isoformat() if note.created_at else '',
            'updated_at': note.updated_at.isoformat() if note.updated_at else '',
        }})

    def _ai_summary(data):
        # AI 摘要已改為 HTTP 異步處理，WebSocket 不再直接呼叫 LLM
        emit('r', {'op': 'er', 'message': '請使用 HTTP 介面'})
