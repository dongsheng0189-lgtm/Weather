"""公告 (announcements) — UI 整合進 notes 模組，資料層獨立。

Routes 註冊到既有 notes_bp（共用 URL prefix `/notes`），檔案分離避免 notes/routes.py 繼續肥。
"""
from flask import request, jsonify, render_template, abort
from flask_login import login_required, current_user

from extensions import db
from notes.routes import notes_bp


# ── Permission helpers ───────────────────────────────────────

def _can_create() -> bool:
    """誰能建立公告：admin / super_admin"""
    return current_user.is_admin()


def _can_target_store(store) -> bool:
    """檢查當前 user 能否對這個 scope 發/改公告。

    super_admin: 任何 store 或 None（全部）皆可
    admin:       只能 store == user.store（不能 None、不能別店）
    user:        永遠 False
    """
    if current_user.is_super_admin():
        return True
    if current_user.is_admin():
        return store == current_user.store and store is not None
    return False


def _can_modify(announcement) -> bool:
    """編輯/刪除這筆公告"""
    if current_user.is_super_admin():
        return True
    if current_user.is_admin() and announcement.store == current_user.store:
        return True
    return False


def _can_view(announcement) -> bool:
    """看得到這筆公告（API GET 用；list 是 query filter，不走這函式）"""
    if current_user.is_super_admin():
        return True
    return announcement.store is None or announcement.store == current_user.store
