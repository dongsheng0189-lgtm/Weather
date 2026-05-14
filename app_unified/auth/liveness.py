"""Face-recognition 安全強化 helpers — pose 判定、紋理 variance、多幀
liveness 檢查、best-match user 選取。

設計動機（2026-05-14）：
- 同 PIN 多人 + first-match-wins 會誤認 → 改 best-match across all candidates
- face_recognition lib 沒 liveness、印刷照片可繞 → 多幀變化檢查 + 紋理 variance
- 不裝新套件：純 PIL + numpy（PIL 已是 deps）

純函式、可獨立 unit test。
"""
import io
from typing import Iterable

import numpy as np
from PIL import Image, ImageFilter


# ── pose classification ──────────────────────────────────────────────────────

# Nose 相對雙眼中心的水平偏移、除以兩眼距離正規化。
# < -threshold: nose 偏向 image 左（user 頭轉向自己的右）→ "R"
# > +threshold: nose 偏向 image 右（user 頭轉向自己的左）→ "L"
# |offset| < threshold → "C" (中)
_POSE_THRESHOLD = 0.15
# 兩眼間距太小（人臉太遠 / 側臉太深）→ 比例不可靠
_MIN_EYE_DISTANCE_PX = 5


def classify_pose(landmarks: dict | None) -> str:
    """依 face_landmarks 判斷頭部 yaw 方向。

    Args:
        landmarks: face_recognition.face_landmarks(img)[i] 的 dict，要有
                   "nose_tip"、"left_eye"、"right_eye" 三個 keys，值為
                   list[(x, y)]。

    Returns:
        'L' / 'C' / 'R' / 'unclear'（user POV：'L'=左轉、'R'=右轉、'C'=正面）
    """
    if not landmarks:
        return "unclear"
    nose_pts = landmarks.get("nose_tip") or []
    left_eye = landmarks.get("left_eye") or []
    right_eye = landmarks.get("right_eye") or []
    if not (nose_pts and left_eye and right_eye):
        return "unclear"

    nose_x = sum(p[0] for p in nose_pts) / len(nose_pts)
    left_eye_x = sum(p[0] for p in left_eye) / len(left_eye)
    right_eye_x = sum(p[0] for p in right_eye) / len(right_eye)
    eye_dist = abs(left_eye_x - right_eye_x)
    if eye_dist < _MIN_EYE_DISTANCE_PX:
        return "unclear"

    mid_eye_x = (left_eye_x + right_eye_x) / 2
    offset = (nose_x - mid_eye_x) / eye_dist
    if offset < -_POSE_THRESHOLD:
        return "R"
    if offset > +_POSE_THRESHOLD:
        return "L"
    return "C"


# ── texture variance（紋理檢測、擋印刷照片）────────────────────────────

def texture_variance(img_bytes: bytes) -> float:
    """以 PIL.FIND_EDGES filter + numpy variance 估算高頻紋理強度。

    印刷品（紙張 / 螢幕翻拍）的高頻紋理 variance 顯著低於真實皮膚（毛孔、
    細紋、油光反射）。閾值見 check_multi_frame_liveness。

    Args:
        img_bytes: JPEG / PNG bytes。

    Returns:
        float variance value（單位 = pixel intensity² in edge map）。

    Note: 裁掉 1px 外邊：FIND_EDGES 在影像邊界把 off-image 當 0、會在純色
    上製造人為紋理污染統計。
    """
    img = Image.open(io.BytesIO(img_bytes)).convert("L")  # grayscale
    edges = np.asarray(img.filter(ImageFilter.FIND_EDGES), dtype=np.float64)
    if edges.shape[0] > 2 and edges.shape[1] > 2:
        edges = edges[1:-1, 1:-1]
    return float(np.var(edges))


# ── multi-frame liveness ─────────────────────────────────────────────────────

# pair-wise face_distance：對齊既有 auth.routes tolerance=0.45 同基準。
# - 太接近（< STATIC_THRESHOLD）→ byte-identical 靜態照片（合理短時間連拍會有
#   sensor noise + 微動產生 ≥ 0.05 的差異）
# - 太遠（> SAME_PERSON_THRESHOLD）→ 不像同一個人（=既有 tolerance）
_STATIC_THRESHOLD = 0.05
_SAME_PERSON_THRESHOLD = 0.45
# 紋理 variance 最低值：印刷品通常 < 10、自然皮膚 > 100
_MIN_TEXTURE_VAR = 50.0


def check_multi_frame_liveness(
    frame_encodings: list[np.ndarray],
    frame_texture_vars: list[float],
) -> tuple[bool, str]:
    """多幀 liveness 檢查：同人、非靜態、紋理合格。

    Args:
        frame_encodings: 各 frame 的 face_encoding ndarray list（長度 ≥ 2）。
        frame_texture_vars: 對應的 texture_variance() 數值 list。

    Returns:
        (pass, reason) — reason 含 'static' / 'person' / 'texture' 對應子原因。
    """
    if len(frame_encodings) != len(frame_texture_vars) or len(frame_encodings) < 2:
        return False, "insufficient frames"

    # 紋理檢查
    for i, v in enumerate(frame_texture_vars):
        if v < _MIN_TEXTURE_VAR:
            return False, f"texture too low at frame {i} (var={v:.1f})"

    # pair-wise face_distance
    arrs = [np.asarray(e, dtype=np.float64) for e in frame_encodings]
    dists = []
    for i in range(len(arrs)):
        for j in range(i + 1, len(arrs)):
            dists.append(float(np.linalg.norm(arrs[i] - arrs[j])))

    # 全部 pair 都太接近 → 像同一張靜態照片
    if all(d < _STATIC_THRESHOLD for d in dists):
        return False, f"frames look static (max pair dist={max(dists):.3f})"
    # 任一 pair 太遠 → 不像同人
    if any(d > _SAME_PERSON_THRESHOLD for d in dists):
        return False, f"frames look like different person (max pair dist={max(dists):.3f})"

    return True, "ok"


# ── best-match user ──────────────────────────────────────────────────────────

def best_match_user(
    users: Iterable,
    frame_encodings: list[np.ndarray],
    *,
    threshold: float = 0.45,
    ambiguous_margin: float = 0.05,
) -> tuple[object | None, dict]:
    """從候選 users 中選平均 face_distance 最低且 < threshold 的；
    若前兩名差距 < ambiguous_margin 則整批拒（避免誤認雙胞胎 / 妝後妝前 /
    同部門員工互相 mis-identify）。

    取代既有 first-match-wins，避免 PIN collision 候選池順序決定誰中。

    Args:
        users: iterable of User-like objects with `face_encoding` attr.
        frame_encodings: 多幀 face_encoding ndarray（取平均距離抑制單幀雜訊）。
        threshold: 平均距離上限。
        ambiguous_margin: 第一名 vs 第二名距離差小於此值 → 拒。

    Returns:
        (matched_user or None, info_dict)。info_dict 含 'best_dist'、
        'second_dist'（若有）、'ambiguous'。
    """
    frames = [np.asarray(e, dtype=np.float64) for e in frame_encodings]
    if not frames:
        return None, {"reason": "no frames"}

    scored = []
    for u in users:
        enc = getattr(u, "face_encoding", None)
        if enc is None:
            continue
        # User model 把 ndarray 存成 raw bytes（128 float64）；轉回。
        if isinstance(enc, (bytes, bytearray)):
            target = np.frombuffer(enc, dtype=np.float64)
        else:
            target = np.asarray(enc, dtype=np.float64)
        avg_dist = float(np.mean([np.linalg.norm(f - target) for f in frames]))
        scored.append((avg_dist, u))

    if not scored:
        return None, {"reason": "no enrolled users"}

    scored.sort(key=lambda x: x[0])
    best_dist, best_user = scored[0]
    info = {"best_dist": best_dist}

    if best_dist > threshold:
        return None, info  # 太遠、根本沒人像

    if len(scored) >= 2:
        second_dist = scored[1][0]
        info["second_dist"] = second_dist
        if second_dist - best_dist < ambiguous_margin:
            info["ambiguous"] = True
            return None, info

    return best_user, info
