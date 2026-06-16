"""Vetting blueprint routes — name+ID 表單 → NPA captcha 接力 → 三來源整合結果。"""
import base64
import json
import logging
import os
import secrets
import time

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user
from requests.utils import dict_from_cookiejar, add_dict_to_cookiejar

from crypto import encrypt, decrypt
from extensions import limiter
from features import user_has_feature
from vetting import judicial, mjib, npa
from vetting.errors import RateLimited, Forbidden
from vetting.http_client import throttle_sleep, make_session

logger = logging.getLogger(__name__)
vetting_bp = Blueprint("vetting", __name__, url_prefix="/vetting")

# 每次查詢的 state（NPA cookies/tokens/jwt + 已查好的司法院/MJIB 結果）。
# prod 是 gunicorn 2 workers：start / submit 兩步可能落在不同 worker，state 一定要
# 跨 worker 共用，否則 submit 端那個 worker 找不到 query_id → 間歇「查詢逾時」
# （feedback_in_memory_state_workers）。→ 優先存 Redis（prod 已有，SocketIO 在用）；
# 沒 Redis（dev / 沒裝）→ fallback process-local dict（dev 單 worker，行為照舊）。
_QUERY_TTL = 300  # 5 分鐘
_REDIS_PREFIX = "vet:state:"
_QUERY_STATE = {}  # process-local fallback（dev / 無 Redis）


def _make_redis():
    """同 extensions 的 URL 解析（內網優先）；拿不到/裝不起來 → None（走 dict fallback）。"""
    url = (os.environ.get("REDIS_URI")
           or os.environ.get("REDIS_CONNECTION_STRING")
           or os.environ.get("REDIS_URL"))
    if not url:
        return None
    try:
        import redis
        # from_url 不會立刻連線；加 timeout 避免 Redis 掛掉時把 request 拖死
        return redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
    except Exception as e:  # pragma: no cover - 環境相關
        logger.warning("vetting: redis init failed, fallback to process-local state: %s", e)
        return None


_redis = _make_redis()


def _judicial_sort_key(case):
    """sort key：keyword_hits 優先，id_match True 次優先。"""
    return (
        0 if case.get("keyword_hits") else 1,
        0 if case.get("id_match") else 1,
    )


def _serialize_state(state: dict) -> str:
    """live npa_session → cookies dict（JSON-safe），其餘原樣，整包**加密**後給 Redis。
    blob 含被查者姓名/身分證/司法院+MJIB 結果/NPA cookie（PII），落 Redis 一律密文
    （AES-256-GCM、同 EncryptedText 那把 DB_ENCRYPTION_KEY），即使 Redis 持久化也不外洩。"""
    sess = state.get("npa_session")
    cookies = dict_from_cookiejar(sess.cookies) if sess is not None else {}
    return encrypt(json.dumps({
        "cookies": cookies,
        "npa_tokens": state.get("npa_tokens"),
        "npa_jwt": state.get("npa_jwt"),
        "name": state.get("name"),
        "id_no": state.get("id_no"),
        "judicial": state.get("judicial"),
        "mjib": state.get("mjib"),
    }))


def _deserialize_state(raw) -> dict:
    """Redis blob（密文）→ 解密 → entry：cookies 灌回新 session（任一 worker 都重建得出來）。"""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    payload = json.loads(decrypt(raw))
    sess = make_session()
    # add_dict_to_cookiejar 還原成 domain-less cookie（會送給任何 host）。此 session
    # 只拿來對 NPA 自家 URL 做單次 submit_query，所以 NPA session cookie 仍會送達、
    # 無洩漏疑慮；⚠️ 勿把這個重建的 session 拿去打別的 host（會連 NPA cookie 一起送出）。
    add_dict_to_cookiejar(sess.cookies, payload.get("cookies") or {})
    return {
        "npa_session": sess,
        "npa_tokens": payload.get("npa_tokens"),
        "npa_jwt": payload.get("npa_jwt"),
        "name": payload.get("name"),
        "id_no": payload.get("id_no"),
        "judicial": payload.get("judicial"),
        "mjib": payload.get("mjib"),
    }


def _gc_expired():
    now = time.time()
    for qid in list(_QUERY_STATE.keys()):
        if _QUERY_STATE[qid].get("expires_at", 0) < now:
            del _QUERY_STATE[qid]


def _put_state(state: dict) -> str:
    qid = secrets.token_urlsafe(16)
    if _redis is not None:
        try:
            _redis.set(_REDIS_PREFIX + qid, _serialize_state(state), ex=_QUERY_TTL)
            return qid
        except Exception as e:
            logger.warning("vetting: redis put failed, fallback to process state: %s", e)
    # fallback：process-local（dev 單 worker OK）
    state = dict(state)
    state["expires_at"] = time.time() + _QUERY_TTL
    _QUERY_STATE[qid] = state
    _gc_expired()
    return qid


def _take_state(qid: str):
    if _redis is not None:
        try:
            raw = _redis.get(_REDIS_PREFIX + qid)
            return _deserialize_state(raw) if raw is not None else None
        except Exception as e:
            logger.warning("vetting: redis get failed: %s", e)
            return None
    _gc_expired()
    return _QUERY_STATE.get(qid)


def _del_state(qid: str):
    """一次性使用：submit 後清掉。"""
    if _redis is not None:
        try:
            _redis.delete(_REDIS_PREFIX + qid)
            return
        except Exception as e:  # pragma: no cover
            logger.warning("vetting: redis del failed: %s", e)
    _QUERY_STATE.pop(qid, None)


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

    # 操作 log：誰用了人員查詢（不阻擋查詢，失敗只 warn）
    try:
        from extensions import db
        from models import VettingQueryLog
        db.session.add(VettingQueryLog(
            user_id=current_user.id,
            queried_name=name[:50],
        ))
        db.session.commit()
    except Exception as e:
        logger.warning("vetting: failed to write query log: %s", e)

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
        empty_scan = {"keyword_hits": [], "masked_names": [], "masked_ids": [], "id_match": None}
        for r in rows[:15]:  # 上限 15 筆，避免太多詳情頁
            if blocked:
                judicial_results.append({**r, **empty_scan})
                continue
            try:
                scan = judicial.scan_detail(j_sess, r["detail_url"], user_id=id_no)
            except (RateLimited, Forbidden):
                blocked = True
                scan = empty_scan
            except Exception:
                scan = empty_scan
            judicial_results.append({**r, **scan})
            throttle_sleep()
        # 有輸入身分證 → 過濾掉明確不符的（id_match=False）；保留 True 與 None（無 ID 可比對）
        if id_no:
            before = len(judicial_results)
            judicial_results = [j for j in judicial_results if j.get("id_match") is not False]
            logger.info("vetting: judicial id-filter %d → %d (id=%s prefix)",
                        before, len(judicial_results), id_no[:3] + "***" if id_no else "")
        # 排序：命中關鍵字的案件排前面（admin 第一眼看到重點）
        judicial_results.sort(key=_judicial_sort_key)
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

    # 一次性使用：清掉 state（Redis 或 process-local）
    _del_state(qid)

    return jsonify({
        "judicial": entry["judicial"],
        "mjib": entry["mjib"],
        "npa": npa_results,
    })
