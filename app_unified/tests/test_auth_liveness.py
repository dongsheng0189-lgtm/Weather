"""Liveness detection helpers — pose classification, texture variance,
multi-frame liveness check, best-match user picker.

純函式測試（不依賴 face_recognition library，user 真實照片透過 mock
landmarks 模擬）。Texture variance 用真實 numpy + PIL（PIL 已是現有 deps）。
"""
import io
import numpy as np
import pytest
from PIL import Image


# ── classify_pose ────────────────────────────────────────────────────────────

def _make_landmarks(nose_x: float, left_eye_x: float, right_eye_x: float):
    """生 landmarks dict、只填 pose 判定需要的 keys。y 座標填 0 不影響 x 比較。"""
    return {
        "nose_tip": [(nose_x, 0)] * 5,
        "left_eye": [(left_eye_x, 0)] * 6,
        "right_eye": [(right_eye_x, 0)] * 6,
    }


def test_classify_pose_center_when_nose_between_eyes():
    from auth.liveness import classify_pose
    # 鼻子在兩眼中間：left_eye_x=200, right_eye_x=100, mid=150, nose=150
    lm = _make_landmarks(nose_x=150, left_eye_x=200, right_eye_x=100)
    assert classify_pose(lm) == "C"


def test_classify_pose_right_when_nose_shifted_toward_image_left():
    """User 頭轉向自己的右邊 → 鼻子在 image 上偏向左側（offset 為負）。"""
    from auth.liveness import classify_pose
    # left_eye_x=200, right_eye_x=100, mid=150, eye_dist=100
    # nose=120 → offset=-30 → normalized=-0.3 < -0.15 threshold
    lm = _make_landmarks(nose_x=120, left_eye_x=200, right_eye_x=100)
    assert classify_pose(lm) == "R"


def test_classify_pose_left_when_nose_shifted_toward_image_right():
    """User 頭轉向自己的左邊 → 鼻子在 image 上偏向右側（offset 為正）。"""
    from auth.liveness import classify_pose
    # nose=180 → offset=+30 → normalized=+0.3 > +0.15 threshold
    lm = _make_landmarks(nose_x=180, left_eye_x=200, right_eye_x=100)
    assert classify_pose(lm) == "L"


def test_classify_pose_unclear_when_landmarks_missing():
    from auth.liveness import classify_pose
    assert classify_pose({}) == "unclear"
    assert classify_pose({"nose_tip": []}) == "unclear"
    # 缺一隻眼
    assert classify_pose({"nose_tip": [(100, 0)], "left_eye": [(150, 0)]}) == "unclear"


def test_classify_pose_unclear_when_eyes_overlap():
    """雙眼座標幾乎重疊（人臉太遠/側臉太深）→ 比例失準、回 unclear。"""
    from auth.liveness import classify_pose
    lm = _make_landmarks(nose_x=150, left_eye_x=150, right_eye_x=150.3)
    assert classify_pose(lm) == "unclear"


# ── texture_variance ─────────────────────────────────────────────────────────

def _img_bytes_from_array(arr: np.ndarray, fmt: str = "JPEG") -> bytes:
    img = Image.fromarray(arr)
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.save(buf, format="JPEG", quality=85)
    else:
        img.save(buf, format="PNG")
    return buf.getvalue()


def test_texture_variance_high_for_random_noise():
    """random noise 充滿高頻邊緣 → variance 高。"""
    from auth.liveness import texture_variance
    rng = np.random.default_rng(42)
    arr = rng.integers(0, 256, (200, 200, 3), dtype=np.uint8)
    v = texture_variance(_img_bytes_from_array(arr))
    assert v > 100, f"random noise 應該高 variance, got {v}"


def test_texture_variance_low_for_solid_color():
    """純色 image（PNG lossless）→ edge 全 0 → variance ≈ 0。
    用 PNG 而非 JPEG：JPEG block compression 會在純色塊邊界產生人為紋理。
    """
    from auth.liveness import texture_variance
    arr = np.full((200, 200, 3), 128, dtype=np.uint8)
    v = texture_variance(_img_bytes_from_array(arr, fmt="PNG"))
    assert v < 5, f"純色應該接近 0 variance, got {v}"


# ── check_multi_frame_liveness ───────────────────────────────────────────────

def test_multi_frame_liveness_pass_with_natural_variation():
    """3 幀同人不同姿勢 + texture 都合格 → pass。
    noise std 0.02 → 128-d 向量差異 norm ≈ 0.226（落在 STATIC=0.05、
    SAME_PERSON=0.45 之間、模擬同人微動）。
    """
    from auth.liveness import check_multi_frame_liveness
    rng = np.random.default_rng(0)
    base = rng.random(128)
    frame_encodings = [base, base + rng.normal(0, 0.02, 128), base + rng.normal(0, 0.025, 128)]
    texture_vars = [200.0, 250.0, 220.0]
    ok, reason = check_multi_frame_liveness(frame_encodings, texture_vars)
    assert ok, f"應該 pass, 原因: {reason}"


def test_multi_frame_liveness_fail_when_identical_frames():
    """3 幀 byte-identical（同張靜態照片）→ 拒。"""
    from auth.liveness import check_multi_frame_liveness
    rng = np.random.default_rng(0)
    enc = rng.random(128)
    frame_encodings = [enc, enc, enc]
    texture_vars = [200.0, 200.0, 200.0]
    ok, reason = check_multi_frame_liveness(frame_encodings, texture_vars)
    assert not ok and "static" in reason.lower()


def test_multi_frame_liveness_fail_when_different_person():
    """3 幀差異太大、像不同人 → 拒。"""
    from auth.liveness import check_multi_frame_liveness
    rng = np.random.default_rng(1)
    frame_encodings = [rng.random(128), rng.random(128), rng.random(128)]
    texture_vars = [200.0, 200.0, 200.0]
    ok, reason = check_multi_frame_liveness(frame_encodings, texture_vars)
    assert not ok and "person" in reason.lower()


def test_multi_frame_liveness_fail_when_low_texture():
    """有一幀紋理太低（印刷品 / 過曝）→ 拒。"""
    from auth.liveness import check_multi_frame_liveness
    rng = np.random.default_rng(0)
    base = rng.random(128)
    frame_encodings = [base, base + rng.normal(0, 0.05, 128), base + rng.normal(0, 0.08, 128)]
    texture_vars = [200.0, 3.0, 220.0]  # 第二幀爆掉
    ok, reason = check_multi_frame_liveness(frame_encodings, texture_vars)
    assert not ok and "texture" in reason.lower()


# ── best_match_user ──────────────────────────────────────────────────────────

class _FakeUser:
    def __init__(self, username, encoding):
        self.username = username
        self._encoding = encoding

    @property
    def face_encoding(self):
        return self._encoding


def test_best_match_picks_lowest_average_distance():
    """3 個候選 user、選平均 face_distance 最低、< 0.45 threshold。"""
    from auth.liveness import best_match_user
    rng = np.random.default_rng(0)
    target = rng.random(128)
    users = [
        _FakeUser("u1", rng.random(128)),       # 隨機、距離大
        _FakeUser("u2", target + rng.normal(0, 0.015, 128)),  # 接近 target、< threshold
        _FakeUser("u3", rng.random(128)),       # 隨機、距離大
    ]
    frame_encodings = [target, target + rng.normal(0, 0.01, 128), target]
    matched, info = best_match_user(users, frame_encodings, threshold=0.45)
    assert matched is not None
    assert matched.username == "u2"
    assert info["best_dist"] < 0.45


def test_best_match_rejects_when_all_above_threshold():
    """所有候選的平均距離都太遠 → 全拒。"""
    from auth.liveness import best_match_user
    rng = np.random.default_rng(0)
    users = [
        _FakeUser("u1", rng.random(128)),
        _FakeUser("u2", rng.random(128)),
    ]
    frame_encodings = [rng.random(128), rng.random(128), rng.random(128)]
    matched, info = best_match_user(users, frame_encodings, threshold=0.45)
    assert matched is None


def test_best_match_rejects_ambiguous_when_two_users_too_close():
    """兩個候選都 < threshold 且差距 < ambiguous_margin → 拒（避免誤認雙胞胎/相似員工）。"""
    from auth.liveness import best_match_user
    rng = np.random.default_rng(0)
    target = rng.random(128)
    # 兩個 user 編碼幾乎相同（pin collision + similar face）
    users = [
        _FakeUser("u1", target + rng.normal(0, 0.01, 128)),
        _FakeUser("u2", target + rng.normal(0, 0.01, 128)),
    ]
    frame_encodings = [target, target, target]
    matched, info = best_match_user(users, frame_encodings, threshold=0.45,
                                    ambiguous_margin=0.05)
    assert matched is None
    assert info.get("ambiguous") is True
