"""/auth/face-pose endpoint + /auth/verify multi-frame path integration。

face_recognition library 在 conftest.py 已被 MagicMock 取代。這裡用
patch 把 face_landmarks / face_encodings / face_locations 改回對應 mock，
驗證 endpoint 邏輯。
"""
from unittest.mock import patch

import numpy as np
import pytest

from auth.routes import _decode_face_image
from tests.conftest import FAKE_ENCODING


_FAKE_B64 = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


# ── /auth/face-pose ──────────────────────────────────────────────────────────

def test_face_pose_missing_image_returns_400(client):
    res = client.post("/auth/face-pose", json={})
    assert res.status_code == 400


def test_face_pose_returns_unclear_when_no_face(client):
    """face_locations 回空 → pose=unclear、encoding=None。"""
    with patch("face_recognition.face_locations", return_value=[]):
        res = client.post("/auth/face-pose", json={"face_image": _FAKE_B64})
    assert res.status_code == 200
    assert res.get_json()["pose"] == "unclear"


def test_face_pose_returns_center_when_landmarks_centered(client):
    """landmarks 顯示鼻子在兩眼中間 → pose=C。"""
    centered = {
        "nose_tip": [(150, 100)] * 5,
        "left_eye": [(200, 90)] * 6,
        "right_eye": [(100, 90)] * 6,
    }
    with patch("face_recognition.face_locations", return_value=[(0, 0, 100, 100)]), \
         patch("face_recognition.face_landmarks", return_value=[centered]), \
         patch("face_recognition.face_encodings", return_value=[FAKE_ENCODING]):
        res = client.post("/auth/face-pose", json={"face_image": _FAKE_B64})
    assert res.status_code == 200
    assert res.get_json()["pose"] == "C"


def test_face_pose_returns_left_when_user_turned_left(client):
    """nose 偏向 image 右 → user 往自己左轉、pose=L。"""
    turned_left = {
        "nose_tip": [(180, 100)] * 5,
        "left_eye": [(200, 90)] * 6,
        "right_eye": [(100, 90)] * 6,
    }
    with patch("face_recognition.face_locations", return_value=[(0, 0, 100, 100)]), \
         patch("face_recognition.face_landmarks", return_value=[turned_left]), \
         patch("face_recognition.face_encodings", return_value=[FAKE_ENCODING]):
        res = client.post("/auth/face-pose", json={"face_image": _FAKE_B64})
    assert res.get_json()["pose"] == "L"


# ── /auth/verify with frames ─────────────────────────────────────────────────

def test_verify_with_frames_succeeds_when_all_checks_pass(client, test_user):
    """3 幀同人不同姿勢 + texture 夠 + best-match 對到 test_user → 登入成功。"""
    rng = np.random.default_rng(0)
    base = np.asarray(FAKE_ENCODING)
    frame_encs = [base, base + rng.normal(0, 0.015, 128), base + rng.normal(0, 0.02, 128)]

    # texture_variance 用真實 PIL：需要真實圖像 bytes。mock face_locations/encodings
    # 即可、texture variance 走真實路徑（fake_b64 內容固定、variance 雖低但可控）。
    # 為了讓 liveness 過、改 mock texture_variance 直接回高值。
    with patch("face_recognition.face_locations", return_value=[(0, 0, 100, 100)]), \
         patch("face_recognition.face_landmarks",
               return_value=[{"nose_tip": [(150, 100)] * 5,
                              "left_eye": [(200, 90)] * 6,
                              "right_eye": [(100, 90)] * 6}]), \
         patch("face_recognition.face_encodings",
               side_effect=[[frame_encs[0]], [frame_encs[1]], [frame_encs[2]]]), \
         patch("auth.liveness.texture_variance", return_value=200.0):
        res = client.post("/auth/verify",
                          json={"pin": "1234",
                                "frames": [_FAKE_B64, _FAKE_B64, _FAKE_B64]})
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_verify_with_frames_rejects_when_liveness_fails(client, test_user):
    """3 幀 byte-identical（靜態照片）→ liveness 拒、回 face_mismatch + reason=liveness。"""
    with patch("face_recognition.face_locations", return_value=[(0, 0, 100, 100)]), \
         patch("face_recognition.face_landmarks",
               return_value=[{"nose_tip": [(150, 100)] * 5,
                              "left_eye": [(200, 90)] * 6,
                              "right_eye": [(100, 90)] * 6}]), \
         patch("face_recognition.face_encodings", return_value=[FAKE_ENCODING]), \
         patch("auth.liveness.texture_variance", return_value=200.0):
        res = client.post("/auth/verify",
                          json={"pin": "1234",
                                "frames": [_FAKE_B64, _FAKE_B64, _FAKE_B64]})
    body = res.get_json()
    assert body["status"] == "face_mismatch"
    assert body.get("reason") == "liveness"


def test_verify_with_frames_rejects_wrong_pin(client, test_user):
    """PIN 不對直接拒、不用跑 liveness。"""
    res = client.post("/auth/verify",
                      json={"pin": "9999",
                            "frames": [_FAKE_B64, _FAKE_B64, _FAKE_B64]})
    assert res.get_json()["status"] == "wrong_password"


def test_verify_frames_takes_precedence_over_face_image(client, test_user):
    """同時送 frames + face_image → 走新版多幀路徑。"""
    rng = np.random.default_rng(0)
    base = np.asarray(FAKE_ENCODING)
    frame_encs = [base, base + rng.normal(0, 0.015, 128), base + rng.normal(0, 0.02, 128)]
    with patch("face_recognition.face_locations", return_value=[(0, 0, 100, 100)]), \
         patch("face_recognition.face_landmarks",
               return_value=[{"nose_tip": [(150, 100)] * 5,
                              "left_eye": [(200, 90)] * 6,
                              "right_eye": [(100, 90)] * 6}]), \
         patch("face_recognition.face_encodings",
               side_effect=[[frame_encs[0]], [frame_encs[1]], [frame_encs[2]]]), \
         patch("auth.liveness.texture_variance", return_value=200.0):
        res = client.post("/auth/verify",
                          json={"pin": "1234",
                                "face_image": _FAKE_B64,
                                "frames": [_FAKE_B64, _FAKE_B64, _FAKE_B64]})
    assert res.get_json()["status"] == "ok"
