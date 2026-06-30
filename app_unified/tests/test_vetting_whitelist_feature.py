"""vetting_whitelist_add 權限：grant_only、不入模組磚、root 預設有、預設全關。"""
import uuid
from features import (AVAILABLE_FEATURES, feature_meta,
                      user_has_feature, user_visible_features)
from models import User
from extensions import db


def _mk(db_session, root_admin=False, features=None):
    name = "admin" if root_admin else f"u_{uuid.uuid4().hex[:6]}"
    existing = User.query.filter_by(username=name).first()
    if existing:
        if features is not None:
            existing.set_features(features); db_session.commit()
        return existing
    u = User(username=name, role="user", store="B", is_active=True)
    u.set_password("1234")
    if features is not None:
        u.set_features(features)
    db_session.add(u); db_session.commit()
    return u


def test_in_catalog(app):
    meta = feature_meta("vetting_whitelist_add")
    assert meta is not None
    assert meta.get("grant_only") is True
    assert meta.get("emoji")            # 非空，模組權限管理 checkbox 才不顯示 undefined
    assert meta.get("enabled") is True  # 不被誤標「（開發中）」
    assert any(f["key"] == "vetting_whitelist_add" for f in AVAILABLE_FEATURES)


def test_grant_only_not_in_visible_features_for_root(app, db_session):
    root = _mk(db_session, root_admin=True)
    keys = [f["key"] for f in user_visible_features(root)]
    assert "vetting_whitelist_add" not in keys           # 不變模組磚
    assert "notes" in keys                                # 一般模組仍在


def test_default_off_for_normal_user(app, db_session):
    u = _mk(db_session, features=["vetting"])
    assert user_has_feature(u, "vetting_whitelist_add") is False


def test_granted_user_has_it(app, db_session):
    u = _mk(db_session, features=["vetting", "vetting_whitelist_add"])
    assert user_has_feature(u, "vetting_whitelist_add") is True


def test_root_admin_has_it(app, db_session):
    root = _mk(db_session, root_admin=True)
    assert user_has_feature(root, "vetting_whitelist_add") is True
