"""Vetting blueprint routes — name+ID 表單 → NPA captcha 接力 → 三來源整合結果。"""
import base64
import logging
import secrets
import time

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from extensions import limiter
from features import user_has_feature
from vetting import judicial, mjib, npa
from vetting.errors import RateLimited, Forbidden
from vetting.http_client import throttle_sleep, make_session

logger = logging.getLogger(__name__)
vetting_bp = Blueprint("vetting", __name__, url_prefix="/vetting")

# Process-local 暫存：每次查詢的 cookies/tokens + 已查好的 司法院/MJIB 結果
_QUERY_STATE = {}
_QUERY_TTL = 300  # 5 分鐘


def _gc_expired():
    now = time.time()
    for qid in list(_QUERY_STATE.keys()):
        if _QUERY_STATE[qid].get("expires_at", 0) < now:
            del _QUERY_STATE[qid]


def _put_state(state: dict) -> str:
    qid = secrets.token_urlsafe(16)
    state["expires_at"] = time.time() + _QUERY_TTL
    _QUERY_STATE[qid] = state
    _gc_expired()
    return qid


def _take_state(qid: str):
    _gc_expired()
    return _QUERY_STATE.get(qid)


@vetting_bp.before_request
def _gate():
    if current_user.is_authenticated and not user_has_feature(current_user, "vetting"):
        abort(404)


@vetting_bp.route("/")
@login_required
def index():
    return render_template("vetting/index.html")


@vetting_bp.route("/start", methods=["POST"])
@login_required
@limiter.limit("3/minute", key_func=lambda: f"vet-start:{current_user.id}")
def start():
    """1) 取 NPA 驗證碼   2) 同步先打司法院 + MJIB（無 captcha 來源）   3) 暫存後回 captcha image。"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    id_no = (data.get("id") or "").strip()  # 選填，給 NPA 配對更精準
    if not name:
        return jsonify({"error": "missing_params",
                        "message": "姓名必填"}), 400

    # ── NPA：拿首頁 token + JWT + captcha ──────────────
    try:
        npa_session = make_session()
        npa_tokens, _ = npa.fetch_home_state(npa_session)
        npa_jwt = npa.fetch_auth_token(npa_session, npa_tokens)
        captcha_bytes = npa.fetch_captcha_bytes(npa_session)
    except (RateLimited, Forbidden) as e:
        logger.warning("vetting: NPA blocked: %s", e)
        return jsonify({"error": "rate_limited",
                        "message": "警政署系統暫時限流，請 5 分鐘後再試"}), 429
    except Exception as e:
        logger.warning("vetting: NPA prep failed: %s", e)
        return jsonify({"error": "npa_unavailable",
                        "message": "警政署系統暫時無法連線"}), 502

    # ── 司法院：同步搜尋（無 captcha） ─────────────────
    judicial_results = []
    try:
        j_sess = make_session()
        j_state, _ = judicial.fetch_home_state(j_sess)
        rows = judicial.submit_search(j_sess, j_state, name)
        blocked = False
        for r in rows[:15]:  # 上限 15 筆，避免太多詳情頁
            if blocked:
                judicial_results.append({**r, "keyword_hits": [], "masked_names": []})
                continue
            try:
                scan = judicial.scan_detail(j_sess, r["detail_url"])
            except (RateLimited, Forbidden):
                blocked = True
                scan = {"keyword_hits": [], "masked_names": []}
            except Exception:
                scan = {"keyword_hits": [], "masked_names": []}
            judicial_results.append({**r, **scan})
            throttle_sleep()
    except Exception as e:
        logger.warning("vetting: judicial failed: %s", e)
        judicial_results = None  # 標示為查詢失敗

    # ── MJIB：同步抓 + 過濾 ──────────────────────────
    try:
        mjib_results = mjib.search_by_name(name)
    except Exception as e:
        logger.warning("vetting: MJIB failed: %s", e)
        mjib_results = None

    # 暫存 NPA 必要 state + 已查好的兩段結果 + 使用者輸入
    qid = _put_state({
        "npa_session": npa_session,
        "npa_tokens": npa_tokens,
        "npa_jwt": npa_jwt,
        "name": name,
        "id_no": id_no,
        "judicial": judicial_results,
        "mjib": mjib_results,
    })

    return jsonify({
        "query_id": qid,
        "captcha_b64": base64.b64encode(captcha_bytes).decode("ascii"),
    })


@vetting_bp.route("/submit", methods=["POST"])
@login_required
@limiter.limit("1/minute", key_func=lambda: f"vet-submit:{current_user.id}")
def submit():
    """送 NPA 驗證碼 → 整合三段結果回前端。"""
    data = request.get_json(silent=True) or {}
    qid = data.get("query_id")
    captcha_text = (data.get("captcha") or "").strip()
    if not qid or not captcha_text:
        return jsonify({"error": "missing_params"}), 400

    entry = _take_state(qid)
    if not entry:
        return jsonify({"error": "query_expired",
                        "message": "查詢逾時（5 分鐘），請重新開始"}), 410

    # NPA 送出
    try:
        npa_results = npa.submit_query(
            entry["npa_session"], entry["npa_tokens"], entry["npa_jwt"],
            entry["name"], entry["id_no"], captcha_text,
        )
    except ValueError as e:
        if str(e) == "captcha_invalid":
            return jsonify({"error": "captcha_invalid",
                            "message": "驗證碼錯誤，請重整圖片再試"}), 400
        return jsonify({"error": "npa_failed", "message": str(e)}), 502
    except (RateLimited, Forbidden) as e:
        logger.warning("vetting: NPA submit blocked: %s", e)
        return jsonify({"error": "rate_limited",
                        "message": "警政署系統暫時限流，請 5 分鐘後再試"}), 429
    except Exception as e:
        logger.warning("vetting: NPA submit failed: %s", e)
        npa_results = None

    # 一次性使用：清掉 state
    _QUERY_STATE.pop(qid, None)

    return jsonify({
        "judicial": entry["judicial"],
        "mjib": entry["mjib"],
        "npa": npa_results,
    })
