from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "weather.index"
login_manager.login_message = "請先登入"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
)

import os
from flask_socketio import SocketIO

# F2 fix: multi-worker broadcast 需要 message queue 否則跨 worker 漏接 ~50%。
# 設 REDIS_URL env (Zeabur Redis marketplace service) → flask-socketio 自動透過
# Redis pub/sub 同步所有 worker、broadcast 全 reliable。
# 沒設 REDIS_URL → fallback 既有單 worker 行為 (dev / 沒裝 Redis 時)、不會 crash。
_redis_url = os.environ.get('REDIS_URL') or None
socketio = SocketIO(message_queue=_redis_url) if _redis_url else SocketIO()
