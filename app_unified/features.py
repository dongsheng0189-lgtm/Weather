"""模組權限系統（Features）— 集中定義可用模組清單與權限檢查 helper"""
from flask import abort


# 模組清單（未來新增功能加這裡即可）
# key    : 內部識別字串，寫入 user.features
# label  : UI 顯示名稱
# emoji  : dashboard 圖示
# url    : 模組首頁 URL
# enabled: 是否已完成開發（未完成顯示「開發中」）
AVAILABLE_FEATURES = [
    {
        "key": "notes",
        "label": "店務筆記",
        "emoji": "📝",
        "url": "/notes/",
        "enabled": True,
    },
    {
        "key": "mynotes",
        "label": "私人筆記",
        "emoji": "🗂️",
        "url": "/mynotes/",
        "enabled": True,  # 只有建立者本人看得到
    },
    {
        "key": "vetting",
        "label": "人員查詢",
        "emoji": "🛡️",
        "url": "/vetting/",
        "enabled": True,
    },
    {
        "key": "vetting_whitelist_add",
        "label": "新增白名單電話",
        "emoji": "📞",
        "enabled": True,
        "grant_only": True,
    },
]


def feature_meta(key):
    for f in AVAILABLE_FEATURES:
        if f["key"] == key:
            return f
    return None


def user_has_feature(user, key):
    """檢查使用者是否可進入該模組。

    規則（嚴格）：
    - 種子 admin（username=='admin'）bypass，永遠擁有全部模組
    - 其他所有人（含 super_admin、admin、user）嚴格依 features[] 判斷
      → 角色（role）不再自動授予任何模組權限
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_root_admin():
        return True
    return key in user.get_features()


def user_visible_features(user):
    """使用者可看到的模組清單（給導覽列/選單頁用）。grant_only 權限不算模組、不顯示。"""
    if not user or not user.is_authenticated:
        return []
    modules = [f for f in AVAILABLE_FEATURES if not f.get("grant_only")]
    if user.is_root_admin():
        return list(modules)
    keys = set(user.get_features())
    return [f for f in modules if f["key"] in keys]


def require_feature(key):
    """路由入口用：無權限直接 404（看不見即不存在）"""
    from flask_login import current_user
    if not user_has_feature(current_user, key):
        abort(404)


def require_root_admin():
    """只有種子管理員（username == 'admin'）可通過"""
    from flask_login import current_user
    if not current_user.is_authenticated or not current_user.is_root_admin():
        abort(404)
