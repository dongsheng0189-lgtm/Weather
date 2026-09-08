import json
import time
from datetime import datetime, timedelta
import requests
from flask import Blueprint, render_template, request, jsonify, current_app
from extensions import db
from models import WeatherCache

weather_bp = Blueprint("weather", __name__, url_prefix="/weather")

_CACHE_TTL_SECONDS = 300  # 5 minutes


@weather_bp.route("/")
def index():
    return render_template("weather/index.html")


@weather_bp.route("/api/weather")
def api_weather():
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    api_key = current_app.config.get("OPENWEATHERMAP_API_KEY", "")

    if lat and lon:
        city_key = f"geo_{round(float(lat), 2)}_{round(float(lon), 2)}"
        ow_params = {"lat": lat, "lon": lon, "appid": api_key, "units": "metric", "lang": "zh_tw"}
    else:
        city = request.args.get("city", "Taipei")
        city_key = city.lower()
        ow_params = {"q": city, "appid": api_key, "units": "metric", "lang": "zh_tw"}

    # Check DB cache (shared across multiple workers)
    cached = WeatherCache.query.filter_by(city_key=city_key).first()
    if cached:
        age = (datetime.utcnow() - cached.cached_at).total_seconds()
        if age < _CACHE_TTL_SECONDS:
            data = json.loads(cached.data_json)
            _inject_ext_ptr(data)
            return jsonify(data)

    if not api_key:
        return jsonify({"error": "未設定 OpenWeatherMap API Key"}), 503

    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params=ow_params,
            timeout=5,
        )
        data = resp.json()
        if resp.status_code == 200:
            # Upsert cache
            data_json = json.dumps(data)
            if cached:
                cached.data_json = data_json
                cached.cached_at = datetime.utcnow()
            else:
                cached = WeatherCache(
                    city_key=city_key,
                    data_json=data_json,
                    cached_at=datetime.utcnow(),
                )
                db.session.add(cached)
            db.session.commit()
        _inject_ext_ptr(data)
        return jsonify(data), resp.status_code
    except Exception as e:
        # 只回給前端不夠 —— 事後查「那天天氣為什麼一直掛」時 server 端要有痕跡。
        # exc_info 保留堆疊，才分得出是上游逾時、金鑰失效還是 DNS 解不出來。
        current_app.logger.warning("weather: %s failed: %s",
                                   request.path, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


def _inject_ext_ptr(data):
    """根據設備狀態注入 ext_ptr 或 seed_ptr"""
    from device.routes import is_device_authorized, is_seed_mode, _get_cookie_uid
    from models import TrustedDevice, Store
    fp = request.headers.get("X-Device-FP", "").strip()
    uid = (request.headers.get("X-Device-UID") or "").strip() or _get_cookie_uid()

    if is_seed_mode():
        data["ext_ptr"] = "/api/v1/seed-setup"
        return

    if not ((fp or uid) and is_device_authorized(fp, uid)):
        return

    # 追加檢查：裝置綁定的使用者，店別是否啟用
    device = None
    if uid:
        device = TrustedDevice.query.filter_by(client_uid=uid).first()
    if not device and fp:
        device = TrustedDevice.query.filter_by(fingerprint=fp).first()
    if device and device.user and device.user.store:
        store = Store.query.filter_by(name=device.user.store).first()
        if store and not store.login_enabled:
            return

    data["ext_ptr"] = "/api/v1/secure-loader"


@weather_bp.route("/api/air_quality")
def api_air_quality():
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    if not lat or not lon:
        return jsonify({"error": "需要 lat/lon 參數"}), 400

    api_key = current_app.config.get("OPENWEATHERMAP_API_KEY", "")
    city_key = f"aqi_{round(float(lat), 2)}_{round(float(lon), 2)}"

    cached = WeatherCache.query.filter_by(city_key=city_key).first()
    if cached:
        age = (datetime.utcnow() - cached.cached_at).total_seconds()
        if age < _CACHE_TTL_SECONDS:
            return jsonify(json.loads(cached.data_json))

    if not api_key:
        return jsonify({"error": "未設定 API Key"}), 503

    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/air_pollution",
            params={"lat": lat, "lon": lon, "appid": api_key},
            timeout=5,
        )
        data = resp.json()
        if resp.status_code == 200:
            data_json = json.dumps(data)
            if cached:
                cached.data_json = data_json
                cached.cached_at = datetime.utcnow()
            else:
                db.session.add(WeatherCache(
                    city_key=city_key, data_json=data_json, cached_at=datetime.utcnow()
                ))
            db.session.commit()
        return jsonify(data), resp.status_code
    except Exception as e:
        # 只回給前端不夠 —— 事後查「那天天氣為什麼一直掛」時 server 端要有痕跡。
        # exc_info 保留堆疊，才分得出是上游逾時、金鑰失效還是 DNS 解不出來。
        current_app.logger.warning("weather: %s failed: %s",
                                   request.path, e, exc_info=True)
        return jsonify({"error": str(e)}), 500
