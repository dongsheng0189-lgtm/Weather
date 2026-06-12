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
# 都沒設 → fallback 既有單 worker 行為 (dev / 沒裝 Redis 時)、不會 crash。
# 優先用內網 service DNS (REDIS_URI / REDIS_CONNECTION_STRING、service-...:6379)，
# 避免 pubsub 流量繞公網 (Zeabur marketplace 的 REDIS_URL 是公網 IP+對外 port、多一跳)。
_redis_url = (os.environ.get('REDIS_URI')
              or os.environ.get('REDIS_CONNECTION_STRING')
              or os.environ.get('REDIS_URL')
              or None)
socketio = SocketIO(message_queue=_redis_url) if _redis_url else SocketIO()
