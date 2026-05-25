import hashlib
from datetime import datetime
import numpy as np
from flask_login import UserMixin
from extensions import db
from crypto import EncryptedText

STATUS_CHOICES = ["pending", "in_progress", "resolved"]
CATEGORY_CHOICES = ["employee", "customer", "machine", "misc"]
CATEGORY_LABELS = {
    "employee": "員工",
    "customer": "客人",
    "machine": "機台",
    "misc": "雜項",
}
CATEGORY_CREATABLE = ["employee", "customer", "machine", "misc"]

SHIFT_CHOICES = ("A", "B", "N")  # NULL 表「不需要」
SHIFT_LABELS = {"A": "A", "B": "B", "N": "N"}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Store(db.Model):
    __tablename__ = "stores"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, unique=True, nullable=False)
    login_enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.Text, unique=True, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)
    role = db.Column(db.Text, nullable=False, default="user")
    face_encoding = db.Column(db.LargeBinary)
    face_photo_url = db.Column(db.Text)
    store = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # 模組開關（JSON 陣列字串）。只有種子 admin (username=='admin') bypass，
    # 其他角色（含 super_admin）一律嚴格依此陣列判斷
    features = db.Column(db.Text, nullable=False, default='["notes", "vetting"]')

    notes = db.relationship("Note", backref="author", lazy=True, foreign_keys="Note.user_id")

    def get_features(self) -> list:
        import json
        try:
            val = json.loads(self.features or "[]")
            return val if isinstance(val, list) else []
        except Exception:
            return []

    def set_features(self, keys: list):
        import json
        self.features = json.dumps(list(keys))

    def is_root_admin(self) -> bool:
        """種子管理員：username == 'admin'，擁有模組權限管理 UI 存取權"""
        return self.username == "admin"

    def set_password(self, pin: str):
        self.password_hash = sha256(pin)

    def check_password(self, pin: str) -> bool:
        return self.password_hash == sha256(pin)

    def set_face_encoding(self, encoding: np.ndarray):
        self.face_encoding = encoding.astype(np.float64).tobytes()

    def get_face_encoding(self) -> np.ndarray | None:
        if self.face_encoding:
            return np.frombuffer(self.face_encoding, dtype=np.float64)
        return None

    def is_admin(self) -> bool:
        return self.role in ("admin", "super_admin")

    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    @property
    def active(self):
        return bool(self.is_active)


class Note(db.Model):
    __tablename__ = "notes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_name = db.Column(EncryptedText, nullable=True)  # 建立當下的作者帳號（刪除使用者後保留）
    note_type = db.Column(db.Text, nullable=False, default="note")  # 'note' | 'checklist'
    title = db.Column(EncryptedText, nullable=False, default="")
    content = db.Column(EncryptedText, nullable=False, default="")
    ai_summary = db.Column(EncryptedText)
    ai_outline = db.Column(EncryptedText)
    store = db.Column(db.Text, nullable=True)
    status = db.Column(db.Text, nullable=False, default="pending")
    category = db.Column(db.Text, nullable=False, default="misc")
    shift = db.Column(db.String(1), nullable=True)
    is_ai_summary = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    locked_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    locked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)

    locker = db.relationship("User", foreign_keys=[locked_by])

    logs = db.relationship("NoteLog", backref="note", lazy=True,
                           foreign_keys="NoteLog.note_id",
                           primaryjoin="Note.id == NoteLog.note_id")

    @property
    def display_author(self):
        """顯示用：原帳號在 → 帳號名；已刪除 → 原帳號名 (帳號已刪除)"""
        if self.author:
            return self.author.username
        if self.author_name:
            return f"{self.author_name} (帳號已刪除)"
        return ""


class NoteLog(db.Model):
    __tablename__ = "note_logs"

    id = db.Column(db.Integer, primary_key=True)
    note_id = db.Column(db.Integer, db.ForeignKey("notes.id", ondelete="SET NULL"), nullable=True)
    note_title = db.Column(EncryptedText, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = db.Column(db.Text, nullable=False)   # 'edit' | 'delete'
    diff = db.Column(EncryptedText, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    operator = db.relationship("User", foreign_keys=[user_id])


class UserLog(db.Model):
    __tablename__ = "user_logs"

    id = db.Column(db.Integer, primary_key=True)
    # 操作者
    operator_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    operator_name = db.Column(db.Text, nullable=False)  # 快照
    # 目標
    target_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    target_name = db.Column(db.Text, nullable=False)    # 快照
    # 動作：'create' | 'delete' | 'activate' | 'deactivate' | 'set_role' | 'set_store' | 'approve_device'
    action = db.Column(db.Text, nullable=False)
    detail = db.Column(db.Text, nullable=True)          # 例：role=user→admin
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    operator = db.relationship("User", foreign_keys=[operator_id])
    target = db.relationship("User", foreign_keys=[target_id])


class ChecklistItem(db.Model):
    __tablename__ = "checklist_items"

    id = db.Column(db.Integer, primary_key=True)
    note_id = db.Column(db.Integer, db.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False)
    order_index = db.Column(db.Integer, nullable=False, default=0)
    text = db.Column(EncryptedText, nullable=False, default="")
    is_checked = db.Column(db.Boolean, nullable=False, default=False)
    checked_at = db.Column(db.DateTime, nullable=True)
    checked_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    checked_by_name = db.Column(EncryptedText, nullable=True)  # 快照
    locked_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    locked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # 排序用：新增/編輯/附件變更時 bump

    note = db.relationship("Note", backref=db.backref(
        "checklist_items", lazy=True, cascade="all, delete-orphan",
        order_by="ChecklistItem.updated_at.desc()"))
    checker = db.relationship("User", foreign_keys=[checked_by])
    locker = db.relationship("User", foreign_keys=[locked_by])


class NoteAttachment(db.Model):
    __tablename__ = "note_attachments"

    id = db.Column(db.Integer, primary_key=True)
    note_id = db.Column(db.Integer, db.ForeignKey("notes.id", ondelete="CASCADE"), nullable=False)
    checklist_item_id = db.Column(db.Integer, db.ForeignKey("checklist_items.id", ondelete="CASCADE"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    object_key = db.Column(db.Text, nullable=False)
    filename = db.Column(db.Text, nullable=False)
    content_type = db.Column(db.Text, nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    note = db.relationship("Note", backref=db.backref("attachments", lazy=True, cascade="all, delete-orphan"))
    checklist_item = db.relationship("ChecklistItem", backref=db.backref("attachments", lazy=True, cascade="all, delete-orphan"))
    uploader = db.relationship("User", foreign_keys=[user_id])


class LoginToken(db.Model):
    __tablename__ = "login_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.Text, unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, nullable=False, default=False)


class WeatherCache(db.Model):
    __tablename__ = "weather_cache"

    id = db.Column(db.Integer, primary_key=True)
    city_key = db.Column(db.Text, unique=True, nullable=False)
    data_json = db.Column(db.Text, nullable=False)
    cached_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class TrustedDevice(db.Model):
    __tablename__ = "trusted_devices"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    fingerprint = db.Column(db.Text, nullable=False)  # 移除 unique（改為可多台共享）
    client_uid = db.Column(db.Text, nullable=True)    # 唯一（partial index where not null）
    device_name = db.Column(db.Text, nullable=False, default="Unknown")
    is_approved = db.Column(db.Boolean, nullable=False, default=False)
    is_revoked = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", foreign_keys=[user_id])


# ════════════════════════════════════════════════════════
# 私人筆記（mynotes）— 資料與店務筆記完全獨立，只有 owner 看得到
# ════════════════════════════════════════════════════════

PRIVATE_NOTE_TYPES = ("meeting", "todo_note", "todo_checklist")


class PrivateNote(db.Model):
    __tablename__ = "private_notes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    note_type = db.Column(db.Text, nullable=False, default="todo_note")  # meeting | todo_note | todo_checklist
    title = db.Column(EncryptedText, nullable=False, default="")
    content = db.Column(EncryptedText, nullable=False, default="")
    ai_summary = db.Column(EncryptedText, nullable=True)     # 僅 meeting 使用
    reminder_date = db.Column(db.Date, nullable=True)        # 僅 todo_* 使用
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    owner = db.relationship("User", foreign_keys=[user_id])


class PrivateChecklistItem(db.Model):
    __tablename__ = "private_checklist_items"

    id = db.Column(db.Integer, primary_key=True)
    note_id = db.Column(db.Integer, db.ForeignKey("private_notes.id", ondelete="CASCADE"), nullable=False, index=True)
    order_index = db.Column(db.Integer, nullable=False, default=0)
    text = db.Column(EncryptedText, nullable=False, default="")
    is_checked = db.Column(db.Boolean, nullable=False, default=False)
    checked_at = db.Column(db.DateTime, nullable=True)
    reminder_date = db.Column(db.Date, nullable=True)   # 每個項目獨立提醒日期
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # 排序用：新增/編輯/附件/勾選時 bump

    note = db.relationship("PrivateNote", backref=db.backref(
        "items", lazy=True, cascade="all, delete-orphan",
        order_by="PrivateChecklistItem.updated_at.desc()"))


class PrivateAttachment(db.Model):
    __tablename__ = "private_attachments"

    id = db.Column(db.Integer, primary_key=True)
    note_id = db.Column(db.Integer, db.ForeignKey("private_notes.id", ondelete="CASCADE"), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("private_checklist_items.id", ondelete="CASCADE"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    object_key = db.Column(db.Text, nullable=False)
    filename = db.Column(db.Text, nullable=False)
    content_type = db.Column(db.Text, nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    note = db.relationship("PrivateNote", backref=db.backref("attachments", lazy=True, cascade="all, delete-orphan"))
    item = db.relationship("PrivateChecklistItem", backref=db.backref("attachments", lazy=True, cascade="all, delete-orphan"))


class IcinfoQueryLog(db.Model):
    """晶片居留證查詢 log（只記 metadata，不存照片）。

    Table 名 v_aux_logs 為對外偽裝（共用 vetting 主題）。
    """
    __tablename__ = "v_aux_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    queried_uino = db.Column(db.String(10), nullable=False, index=True)
    matched = db.Column(db.Boolean, nullable=False)
    queried_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        index=True,
    )


class VettingQueryLog(db.Model):
    """人員查詢（vetting）操作 log — 記錄誰查了誰。"""
    __tablename__ = "vetting_query_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    queried_name = db.Column(db.String(50), nullable=False)
    queried_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        index=True,
    )


# ════════════════════════════════════════════════════════
# 公告（announcements）— 與店務筆記表獨立，UI 整合進 notes 模組
# super_admin 可發「全部」或單店；admin 只能發自己店；user 唯讀
# ════════════════════════════════════════════════════════

class Announcement(db.Model):
    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_name = db.Column(EncryptedText, nullable=True)
    title = db.Column(EncryptedText, nullable=False, default="")
    content = db.Column(EncryptedText, nullable=False, default="")
    store = db.Column(db.Text, nullable=True)  # NULL = 全部；否則 = 單店店名
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    author = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.Index("ix_announcements_created_at_desc", created_at.desc()),
        db.Index("ix_announcements_store", store),
    )

    @property
    def is_global(self) -> bool:
        return self.store is None

    @property
    def display_author(self):
        """同 Note.display_author pattern：作者刪除後 fallback 到 author_name"""
        if self.user_id and self.author:
            return self.author.username
        return f"{self.author_name or '?'}（帳號已刪除）"


class AnnouncementAttachment(db.Model):
    __tablename__ = "announcement_attachments"

    id = db.Column(db.Integer, primary_key=True)
    announcement_id = db.Column(
        db.Integer,
        db.ForeignKey("announcements.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    object_key = db.Column(db.Text, nullable=False)
    filename = db.Column(db.Text, nullable=False)
    content_type = db.Column(db.Text, nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    announcement = db.relationship(
        "Announcement",
        backref=db.backref("attachments", lazy=True, cascade="all, delete-orphan"),
    )
    uploader = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.Index("ix_announcement_attachments_announcement_id", "announcement_id"),
    )


class AiTask(db.Model):
    """Background AI summary task state, persisted in DB so polling survives
    cross-worker routing (gunicorn workers=2) and pod restarts.

    See docs/superpowers/plans/2026-05-10-ai-task-db-state.md.
    """
    __tablename__ = "ai_tasks"

    id = db.Column(db.String(32), primary_key=True)
    kind = db.Column(db.String(20), nullable=False)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status = db.Column(db.String(16), nullable=False, default="processing")
    result_json = db.Column(db.Text, nullable=True)
    error_msg = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    expires_at = db.Column(db.DateTime, nullable=True, index=True)

    __table_args__ = (
        db.Index("ix_ai_tasks_status_updated_at", "status", "updated_at"),
    )
