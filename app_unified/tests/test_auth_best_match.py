"""/auth/verify best-match 取代 first-match-wins —
單元測 _best_match_among 純函式 + 兩條 integration 確認 endpoint 走 best-match。"""
from unittest.mock import patch

import numpy as np
import pytest

from auth.routes import _best_match_among
from tests.conftest import FAKE_ENCODING


class _FakeUser:
    def __init__(self, username, encoding):
        self.username = username
        # 對齊 User.face_encoding 真實格式（raw bytes）
        if isinstance(encoding, np.ndarray):
            self.face_encoding = encoding.astype(np.float64).tobytes()
        else:
            self.face_encoding = encoding


# ── _best_match_among 純函式 ─────────────────────────────────────────

def test_best_match_picks_lowest_distance():
    rng = np.random.default_rng(0)
    target = rng.random(128)
    users = [
        _FakeUser("u1", rng.random(128)),                          # 隨機、距離大
        _FakeUser("u2", target + rng.normal(0, 0.015, 128)),       # 接近 target
        _FakeUser("u3", rng.random(128)),                          # 隨機、距離大
    ]
    matched, info = _best_match_among(users, target)
    assert matched is not None
    assert matched.username == "u2"
    assert info["best_dist"] < 0.45


def test_best_match_rejects_when_above_threshold():
    rng = np.random.default_rng(0)
    users = [
        _FakeUser("u1", rng.random(128)),
        _FakeUser("u2", rng.random(128)),
    ]
    submitted = rng.random(128)  # 跟 u1/u2 都距離 ~4.6
    matched, info = _best_match_among(users, submitted, threshold=0.45)
    assert matched is None
    assert info["best_dist"] > 0.45


def test_best_match_rejects_ambiguous_close_call():
    """兩個候選臉太像 → 拒（避免同 PIN 兩員工互登）。"""
    rng = np.random.default_rng(0)
    target = rng.random(128)
    users = [
        _FakeUser("u1", target + rng.normal(0, 0.01, 128)),
        _FakeUser("u2", target + rng.normal(0, 0.01, 128)),
    ]
    matched, info = _best_match_among(users, target,
                                       threshold=0.45, ambiguous_margin=0.05)
    assert matched is None
    assert info.get("ambiguous") is True


def test_best_match_handles_single_candidate_ignores_ambiguous():
    """只 1 個 enrolled user 不可能 ambiguous、正常 match。"""
    rng = np.random.default_rng(0)
    target = rng.random(128)
    users = [_FakeUser("only", target + rng.normal(0, 0.01, 128))]
    matched, info = _best_match_among(users, target)
    assert matched is not None
    assert matched.username == "only"
    assert "ambiguous" not in info


def test_best_match_skips_users_without_face_encoding():
    rng = np.random.default_rng(0)
    target = rng.random(128)
    users = [
        _FakeUser("no_face", None),
        _FakeUser("u2", target + rng.normal(0, 0.01, 128)),
    ]
    matched, info = _best_match_among(users, target)
    assert matched.username == "u2"


# ── /auth/verify integration ─────────────────────────────────────────

_FAKE_B64 = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


def test_verify_picks_closest_when_multiple_candidates_share_pin(client, db_session):
    """同 PIN 兩個 enrolled user、提交的臉接近 u2 → 應該登入 u2、不該因 DB 順序中 u1。"""
    from models import User
    rng = np.random.default_rng(0)
    target = rng.random(128)
    # u1 比 u2 更接近 target — 不對，反過來：u2 接近 target、u1 隨機
    u1 = User(username="bm_u1")
    u1.set_password("1234")
    u1_enc = rng.random(128)  # 距離 target ~4.6
    u1.set_face_encoding(u1_enc)
    u2 = User(username="bm_u2")
    u2.set_password("1234")
    u2_enc = target + rng.normal(0, 0.01, 128)  # 接近 target
    u2.set_face_encoding(u2_enc)
    db_session.add_all([u1, u2])
    db_session.commit()

    # 提交圖 → mock face_recognition.face_encodings 回 target
    with patch("face_recognition.face_locations", return_value=[(0, 0, 100, 100)]), \
         patch("face_recognition.face_encodings", return_value=[target]):
        res = client.post("/auth/verify", json={"pin": "1234", "face_image": _FAKE_B64})
    assert res.get_json()["status"] == "ok"

    # u2 被 login（透過 session 確認）
    with client.session_transaction() as sess:
        assert sess.get("_user_id") == str(u2.id)

    # cleanup
    db_session.delete(u1)
    db_session.delete(u2)
    db_session.commit()


def test_verify_rejects_when_two_enrolled_users_are_ambiguous(client, db_session):
    """同 PIN 兩個 enrolled user 臉太像 → close call、整批拒、回 face_mismatch。"""
    from models import User
    rng = np.random.default_rng(0)
    target = rng.random(128)
    u1 = User(username="bm_amb_u1")
    u1.set_password("1234")
    u1.set_face_encoding(target + rng.normal(0, 0.01, 128))
    u2 = User(username="bm_amb_u2")
    u2.set_password("1234")
    u2.set_face_encoding(target + rng.normal(0, 0.01, 128))
    db_session.add_all([u1, u2])
    db_session.commit()

    with patch("face_recognition.face_locations", return_value=[(0, 0, 100, 100)]), \
         patch("face_recognition.face_encodings", return_value=[target]):
        res = client.post("/auth/verify", json={"pin": "1234", "face_image": _FAKE_B64})
    assert res.get_json()["status"] == "face_mismatch"

    db_session.delete(u1)
    db_session.delete(u2)
    db_session.commit()


def test_verify_returns_face_not_found_when_no_face_detected(client, test_user):
    """face_encodings 回空 → 早退 face_not_found。"""
    with patch("face_recognition.face_locations", return_value=[]), \
         patch("face_recognition.face_encodings", return_value=[]):
        res = client.post("/auth/verify", json={"pin": "1234", "face_image": _FAKE_B64})
    assert res.get_json()["status"] == "face_not_found"
