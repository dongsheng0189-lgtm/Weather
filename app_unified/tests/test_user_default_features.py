"""Test User.features column default (newly-created users should auto-receive vetting)."""
import json

from models import User


def test_user_features_column_default_includes_notes_and_vetting():
    """The Column default string is the source of truth for new-user features.

    Existing users in prod won't pick up this default — they need a one-time UPDATE.
    """
    default_arg = User.__table__.columns["features"].default.arg
    default_list = json.loads(default_arg)
    assert "notes" in default_list
    assert "vetting" in default_list


def test_new_user_inserted_into_db_gets_default_features(app, db_session):
    """End-to-end: insert a new User without setting features → SQLAlchemy applies default
    on INSERT → row has both 'notes' and 'vetting' in its features JSON."""
    u = User(username="default_features_probe", role="user", store="B", is_active=True)
    u.set_password("1234")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    feats = u.get_features()
    assert "notes" in feats
    assert "vetting" in feats
