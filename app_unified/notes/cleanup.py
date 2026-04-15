"""Resolved 筆記 30 天後自動刪除（含 R2 附件清理）。

呼叫：外部呼叫應以 try/except 包住，避免例外影響頁面顯示。
節流：每個 process 每 _THROTTLE_SECONDS 最多跑一次（除非 force=True）。
"""
import time
from datetime import datetime, timedelta

from flask import current_app

from extensions import db
from models import Note, NoteAttachment
from storage import delete_attachment

_RETENTION_DAYS = 30
_THROTTLE_SECONDS = 3600
_last_run: float = 0.0


def cleanup_resolved_notes(force: bool = False) -> int:
    """回傳刪除筆數。"""
    global _last_run
    now = time.time()
    if not force and now - _last_run < _THROTTLE_SECONDS:
        return 0
    _last_run = now

    cutoff = datetime.utcnow() - timedelta(days=_RETENTION_DAYS)
    notes = Note.query.filter(
        Note.status == "resolved",
        Note.resolved_at.isnot(None),
        Note.resolved_at <= cutoff,
    ).all()

    deleted = 0
    for n in notes:
        for att in NoteAttachment.query.filter_by(note_id=n.id).all():
            try:
                delete_attachment(att.object_key)
            except Exception:
                current_app.logger.exception(
                    "R2 delete failed (orphan tolerated): note_id=%s key=%s",
                    n.id, att.object_key,
                )
        db.session.delete(n)
        deleted += 1

    if deleted:
        db.session.commit()
    return deleted
