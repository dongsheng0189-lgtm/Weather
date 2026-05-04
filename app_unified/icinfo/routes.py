"""icinfo blueprint routes — 對外偽裝 /v/aux/c + /v/aux/s.

內部模組仍叫 icinfo（程式碼層）；只有對外 URL、HTML class、JS 變數
全部用中性命名（auxXxx）避免 F12 偵察.
"""
import base64
import logging

from flask import Blueprint, request, jsonify, session, abort
from flask_login import login_required, current_user

from features import user_has_feature
from models import db, IcinfoQueryLog
from icinfo.immigration import fetch_captcha, submit_search
from icinfo.errors import UpstreamError

logger = logging.getLogger(__name__)
icinfo_bp = Blueprint("icinfo", __name__, url_prefix="/vetting/aux")

_SESSION_KEY = "icinfo_jsessionid"


@icinfo_bp.before_request
def _gate():
    if not current_user.is_authenticated:
        abort(404)
    if not user_has_feature(current_user, "vetting"):
        abort(404)


@icinfo_bp.route("/c", methods=["GET"])
@login_required
def get_captcha():
    """GET /v/aux/c — 取驗證碼，存 JSESSIONID 到 Flask session."""
    try:
        jpeg, jsessionid = fetch_captcha()
    except UpstreamError as e:
        logger.warning("icinfo captcha upstream error: %s", e)
        return jsonify({"error": "upstream"}), 200

    session[_SESSION_KEY] = jsessionid
    return jsonify({"image": base64.b64encode(jpeg).decode("ascii")})


@icinfo_bp.route("/s", methods=["POST"])
@login_required
def post_search():
    """POST /v/aux/s — 帶 session 中的 jsessionid + 4 欄位 + captcha 送查詢."""
    jsessionid = session.get(_SESSION_KEY)
    if not jsessionid:
        return jsonify({"error": "captcha_expired"}), 200

    payload = request.get_json(silent=True) or {}
    uino = (payload.get("uino") or "").strip()
    if not uino:
        return jsonify({"error": "invalid_input"}), 200

    safe_payload = {
        "uino": uino,
        "issueDate": payload.get("issueDate") or "",
        "expiryDate": payload.get("expiryDate") or "",
        "serialNo": (payload.get("serialNo") or "").strip(),
        "captcha": (payload.get("captcha") or "").strip(),
    }

    try:
        result = submit_search(safe_payload, jsessionid=jsessionid)
    except UpstreamError as e:
        logger.warning("icinfo search upstream error: %s", e)
        return jsonify({"error": "upstream"}), 200
    finally:
        # 驗證碼一次性：不論成敗都清掉 session jsessionid
        session.pop(_SESSION_KEY, None)

    log = IcinfoQueryLog(
        user_id=current_user.id,
        queried_uino=uino[:10],
        matched=result["matched"],
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({
        "matched": result["matched"],
        "image": result["image"],
        "search_time": result["search_time"],
    })
