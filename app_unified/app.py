import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

_TW_TZ = ZoneInfo("Asia/Taipei")


class _TaipeiFormatter(logging.Formatter):
    """讓 %(asctime)s 印台灣時間，與部署容器的 TZ 解耦（Zeabur 預設 UTC）。"""
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=_TW_TZ)
        return dt.strftime(datefmt) if datefmt else dt.strftime("%Y-%m-%d %H:%M:%S")


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_TaipeiFormatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))

# Workaround: Zeabur runtime log viewer 在這個 service 卡 stale 不更新（postgres 等別的 service log 正常）。
# 多寫一份到 /tmp/app.log，debug 時進「指令」shell `tail -f /tmp/app.log` 看。
# 10MB 自動 rotate 留兩份備份，避免無限長大；container restart 時 /tmp 自動清空。
import logging.handlers
_file_handler = logging.handlers.RotatingFileHandler(
    "/tmp/app.log", maxBytes=10 * 1024 * 1024, backupCount=2
)
_file_handler.setFormatter(_TaipeiFormatter("%(asctime)s %(name)s %(levelname)s: %(message)s"))

logging.basicConfig(level=logging.WARNING, handlers=[_handler, _file_handler], force=True)

from datetime import timedelta
from flask import Flask, redirect, url_for, render_template, request, session, jsonify
from flask_login import current_user, logout_user
from config import Config
from extensions import db, login_manager, limiter, socketio
from models import User


def _safe_ddl(sql, label=""):
    """在獨立 SAVEPOINT 內跑一句 schema 修復語句，讓單句失敗不會污染整批。

    PostgreSQL 特性：同一個 transaction 裡任一句 SQL 出錯，整個 tx 立刻進入
    aborted 狀態，之後每句都回 'current transaction is aborted, commands ignored'。
    單純 try/except 只吞掉 Python 例外、卻沒 rollback，PG 端 tx 仍是 poisoned，
    導致後面幾十句 DDL 全被連坐，最後 commit 一個 aborted tx = 整批 ROLLBACK。

    這裡用 begin_nested()（SAVEPOINT）把每句隔離：失敗只回滾該句的 savepoint、
    外層 tx 照常存活，批次可以繼續；成功的句子仍留在外層 tx，等最後一次 commit
    一起落地。回傳 True=成功、False=已被吞掉的失敗（同時印診斷 log）。

    注意：本機 dev SQLite 不會跑到呼叫端（被 `'postgresql' in URI` gate 住），
    但 SQLite 也支援 SAVEPOINT，故本函式邏輯可用 SQLite 單元測試驗證。
    """
    tag = label or " ".join(sql.split())[:70]
    try:
        with db.session.begin_nested():
            db.session.execute(db.text(sql))
        return True
    except Exception as e:  # noqa: BLE001 — schema 修復本就 best-effort
        print(f"[schema-repair] skip: {tag} -> {type(e).__name__}: {e}", flush=True)
        return False


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    # Session 硬性過期：35 分鐘（涵蓋私人筆記筆電 30 分鐘 + 緩衝）
    # 真正的 idle 強制由 _enforce_session_idle 依路徑判定（5 分鐘 / 30 分鐘）
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=35)

    db.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)
    socketio.init_app(app, cors_allowed_origins=app.config["ALLOWED_ORIGINS"], async_mode="gevent")

    @app.errorhandler(429)
    def _handle_rate_limited(e):
        # flask-limiter 預設回 HTML → fetch/XHR 端 r.json() 解析 HTML 會噴
        # "unexpected token <"。對 fetch/XHR（Accept: json 或 Sec-Fetch-Dest 非 document）
        # 改回 JSON，讓前端能顯示「操作太頻繁」而非「連線失敗」。
        accept = request.headers.get("Accept", "")
        dest = request.headers.get("Sec-Fetch-Dest", "")
        wants_json = "application/json" in accept or (dest and dest != "document")
        if wants_json:
            return jsonify({"error": "rate_limited",
                            "message": "操作太頻繁，請稍候幾秒再試"}), 429
        return e

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Date formatting filter for templates (handles both datetime and legacy str)
    # 後端統一存 naive UTC，模板顯示前轉台灣時間
    from tw_time import to_tw

    @app.template_filter("fmt_date")
    def fmt_date(value, fmt="%Y-%m-%d"):
        if not value:
            return ""
        if isinstance(value, str):
            return value[:10]
        return to_tw(value).strftime(fmt)

    @app.template_filter("fmt_datetime")
    def fmt_datetime(value, fmt="%Y-%m-%d %H:%M"):
        if not value:
            return ""
        if isinstance(value, str):
            return value[:16]
        return to_tw(value).strftime(fmt)

    @app.template_filter("highlight")
    def highlight_keyword(text, keyword):
        """把 keyword 在 text 裡的所有出現包成 <mark> 標籤（case-insensitive）。

        XSS-safe：先 escape text + escape keyword，再用 escape 後的 keyword 去
        match escape 後的 text。Substitution 只插 <mark>...</mark>，內容是
        match 來的 escape 後字串，無 user-controlled raw HTML 注入路徑。
        回 Markup so Jinja 不再 escape 一次（pattern 不會 match 到 <mark> 本身、
        所以也不會被巢狀包覆）。
        """
        if not keyword or text is None or text == "":
            # No keyword or empty text — pass through (Jinja auto-escapes)
            return text
        import re as _re
        from markupsafe import Markup, escape as _escape
        escaped_text = str(_escape(text))
        escaped_keyword = str(_escape(keyword))
        if not escaped_keyword:
            return text
        pattern = _re.compile(_re.escape(escaped_keyword), _re.IGNORECASE)
        highlighted = pattern.sub(
            lambda m: f'<mark class="kw-hl">{m.group(0)}</mark>',
            escaped_text,
        )
        return Markup(highlighted)

    @app.context_processor
    def inject_features():
        from features import user_visible_features, user_has_feature
        from flask_login import current_user
        feats = user_visible_features(current_user) if current_user.is_authenticated else []
        return {
            "visible_features": feats,
            "has_feature": lambda k: user_has_feature(current_user, k),
            "multi_feature": len(feats) >= 2,
        }

    @app.context_processor
    def inject_handover():
        from flask import session
        return {"handover_active": bool(session.get("_handover"))}

    @app.context_processor
    def inject_git_sha():
        # GHA build 時透過 build-arg 注入 GIT_SHA，再由 Dockerfile ENV 帶到 runtime。
        # 本機 / 沒設值時 fallback 顯示 'dev'。只取前 4 碼讓管理頁面好比對。
        sha = os.environ.get("GIT_SHA", "dev")
        return {"git_sha": (sha[:4] if sha else "dev")}

    with app.app_context():
        try:
            print(f"=== DB URI: {app.config['SQLALCHEMY_DATABASE_URI'][:30]}... ===", flush=True)
            db.create_all()
            # 修復 PostgreSQL sequence（避免 duplicate key error）
            if 'postgresql' in app.config['SQLALCHEMY_DATABASE_URI']:
                for table in ['notes', 'users', 'stores', 'note_logs', 'trusted_devices', 'user_logs']:
                    _safe_ddl(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                    )
                # notes.user_id 允許 NULL + FK ON DELETE SET NULL（刪除使用者保留筆記）
                _safe_ddl("ALTER TABLE notes ALTER COLUMN user_id DROP NOT NULL")
                # notes.author_name：建立當下的作者帳號快照
                _safe_ddl("ALTER TABLE notes ADD COLUMN IF NOT EXISTS author_name TEXT")
                # 回填現有筆記的 author_name（只處理尚未填的）
                _safe_ddl("""
                    UPDATE notes SET author_name = users.username
                    FROM users
                    WHERE notes.user_id = users.id AND notes.author_name IS NULL
                """)
                # trusted_devices: 加 client_uid 欄位，拿掉 fingerprint unique，加 client_uid 的 partial unique index
                _safe_ddl("ALTER TABLE trusted_devices ADD COLUMN IF NOT EXISTS client_uid TEXT")
                _safe_ddl("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conname = 'trusted_devices_fingerprint_key'
                        ) THEN
                            ALTER TABLE trusted_devices DROP CONSTRAINT trusted_devices_fingerprint_key;
                        END IF;
                    END $$;
                """)
                _safe_ddl(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_trusted_devices_client_uid "
                    "ON trusted_devices(client_uid) WHERE client_uid IS NOT NULL"
                )
                # notes.note_type：區分一般筆記和確認表單
                _safe_ddl(
                    "ALTER TABLE notes ADD COLUMN IF NOT EXISTS note_type TEXT NOT NULL DEFAULT 'note'"
                )
                # note_attachments.checklist_item_id：附件可以綁定到確認表單項目
                _safe_ddl(
                    "ALTER TABLE note_attachments ADD COLUMN IF NOT EXISTS checklist_item_id INTEGER "
                    "REFERENCES checklist_items(id) ON DELETE CASCADE"
                )
                # notes.locked_by / locked_at：編輯鎖
                _safe_ddl(
                    "ALTER TABLE notes ADD COLUMN IF NOT EXISTS locked_by INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
                _safe_ddl("ALTER TABLE notes ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP")
                _safe_ddl("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_name='notes' AND constraint_name='notes_user_id_fkey'
                        ) THEN
                            ALTER TABLE notes DROP CONSTRAINT notes_user_id_fkey;
                            ALTER TABLE notes ADD CONSTRAINT notes_user_id_fkey
                                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
                        END IF;
                    END $$;
                """)
                # note_logs.user_id → ON DELETE SET NULL
                _safe_ddl("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_name='note_logs' AND constraint_name='note_logs_user_id_fkey'
                        ) THEN
                            ALTER TABLE note_logs DROP CONSTRAINT note_logs_user_id_fkey;
                            ALTER TABLE note_logs ADD CONSTRAINT note_logs_user_id_fkey
                                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
                        END IF;
                    END $$;
                """)
                # note_attachments.user_id：允許 NULL + ON DELETE SET NULL
                _safe_ddl("ALTER TABLE note_attachments ALTER COLUMN user_id DROP NOT NULL")
                _safe_ddl("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_name='note_attachments' AND constraint_name='note_attachments_user_id_fkey'
                        ) THEN
                            ALTER TABLE note_attachments DROP CONSTRAINT note_attachments_user_id_fkey;
                            ALTER TABLE note_attachments ADD CONSTRAINT note_attachments_user_id_fkey
                                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
                        END IF;
                    END $$;
                """)
                # users.features：模組開關 JSON 陣列
                _safe_ddl(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS features TEXT NOT NULL DEFAULT '[\"notes\"]'"
                )
                # users.hidden_stores：每個 super_admin 個人隱藏店家 JSON（NULL=全可視）
                _safe_ddl("ALTER TABLE users ADD COLUMN IF NOT EXISTS hidden_stores TEXT")
                # 私人筆記三張表由 db.create_all() 自動建立（純新增，無需 ALTER）
                # 但後續新增欄位需 ALTER：
                _safe_ddl(
                    "ALTER TABLE private_checklist_items ADD COLUMN IF NOT EXISTS reminder_date DATE"
                )
                # 店務筆記 checklist_items.updated_at：依時間排序（新→舊）
                _safe_ddl("ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP")
                # 既有資料 backfill：updated_at = created_at
                _safe_ddl("UPDATE checklist_items SET updated_at = created_at WHERE updated_at IS NULL")
                # 之後設 NOT NULL（若可行；舊資料已補完）
                _safe_ddl("ALTER TABLE checklist_items ALTER COLUMN updated_at SET NOT NULL")
                # 私人筆記 private_checklist_items.updated_at：依時間排序（新→舊）
                _safe_ddl("ALTER TABLE private_checklist_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP")
                _safe_ddl("UPDATE private_checklist_items SET updated_at = created_at WHERE updated_at IS NULL")
                _safe_ddl("ALTER TABLE private_checklist_items ALTER COLUMN updated_at SET NOT NULL")
                # login_tokens.user_id → ON DELETE CASCADE（刪使用者連帶清 tokens）
                _safe_ddl("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_name='login_tokens' AND constraint_name='login_tokens_user_id_fkey'
                        ) THEN
                            ALTER TABLE login_tokens DROP CONSTRAINT login_tokens_user_id_fkey;
                            ALTER TABLE login_tokens ADD CONSTRAINT login_tokens_user_id_fkey
                                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
                        END IF;
                    END $$;
                """)

                # F2 (2026-05-25): checklist_items 加 item-level lock (locked_by + locked_at)
                _safe_ddl(
                    "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS locked_by INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
                _safe_ddl("ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP")

                # F1 (2026-05-25): notes + checklist_items 加 soft delete (deleted_at + deleted_by)
                _safe_ddl("ALTER TABLE notes ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")
                _safe_ddl(
                    "ALTER TABLE notes ADD COLUMN IF NOT EXISTS deleted_by INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
                _safe_ddl("CREATE INDEX IF NOT EXISTS ix_notes_deleted_at ON notes(deleted_at)")
                _safe_ddl("ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")
                _safe_ddl(
                    "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS deleted_by INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
                _safe_ddl("CREATE INDEX IF NOT EXISTS ix_checklist_items_deleted_at ON checklist_items(deleted_at)")
                # F11: ChecklistItem authorship — creator + last-modifier snapshot
                _safe_ddl(
                    "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS created_by INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
                _safe_ddl("ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS created_by_name TEXT")
                _safe_ddl(
                    "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS updated_by INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
                _safe_ddl("ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS updated_by_name TEXT")
                # 店務筆記到期提醒 reminder_date + 稽核快照（2026-06-15）— notes
                _safe_ddl("ALTER TABLE notes ADD COLUMN IF NOT EXISTS reminder_date DATE")
                _safe_ddl(
                    "ALTER TABLE notes ADD COLUMN IF NOT EXISTS reminder_set_by INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
                _safe_ddl("ALTER TABLE notes ADD COLUMN IF NOT EXISTS reminder_set_by_name TEXT")
                _safe_ddl("ALTER TABLE notes ADD COLUMN IF NOT EXISTS reminder_set_at TIMESTAMP")
                # 同上 — checklist_items
                _safe_ddl("ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS reminder_date DATE")
                _safe_ddl(
                    "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS reminder_set_by INTEGER "
                    "REFERENCES users(id) ON DELETE SET NULL"
                )
                _safe_ddl("ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS reminder_set_by_name TEXT")
                _safe_ddl("ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS reminder_set_at TIMESTAMP")

                # 附件縮圖 thumb_key(2026-07-03）— 3 張 attachment 表
                for _tbl in ("note_attachments", "private_attachments",
                             "announcement_attachments"):
                    _safe_ddl(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS thumb_key TEXT")

                db.session.commit()
            print("=== db.create_all() OK ===", flush=True)
        except Exception as e:
            print(f"=== db.create_all() FAILED: {e} ===", flush=True)
            db.session.rollback()
        # 種子模式由 device blueprint 處理，不再自動建立預設 admin
        # 修正角色：確保 admin 帳號是 super_admin
        admin_user = User.query.filter_by(username="admin").first()
        if admin_user and admin_user.role != "super_admin":
            admin_user.role = "super_admin"
            db.session.commit()
            print(f"=== Fixed: admin -> super_admin ===", flush=True)
        # hirain 如果被誤設為 super_admin，改回 admin
        hirain_user = User.query.filter_by(username="hirain").first()
        if hirain_user and hirain_user.role == "super_admin":
            hirain_user.role = "admin"
            db.session.commit()
            print(f"=== Fixed: hirain -> admin ===", flush=True)

    # Flask 2.x: g is app-context-scoped, not request-scoped.
    # In production each request gets a fresh app context so this is a no-op.
    # In tests with a persistent outer app context, this ensures Flask-Login
    # re-checks the session cookie on every request instead of using a stale
    # g._login_user from a previous request.
    from flask import g as flask_g

    @app.before_request
    def _clear_login_cache():
        flask_g.pop('_login_user', None)

    @app.before_request
    def _enforce_session_idle():
        """伺服器端閒置強制登出。
        max idle 依使用者最近活動路徑動態調整：
        - 桌機（非 mobile UA）且最近曾請求 /mynotes/* → 30 分鐘
          （私人筆記內容長停留輸入；mobile 不享 grace、保守 5 分鐘）
        - 最近曾請求 /vetting/* → 15 分鐘
          （判例 / 社群搜尋閱讀緩衝；mobile/桌機都享、user 明確指定不分裝置）
        - 其他 → 5 分鐘

        各功能 grace 由對應路徑請求推進；grace 期間任何路徑（含 /auth/ping、
        跨頁切到 /weather）都享延長 idle，避免離開該功能後第一個請求就被
        5 分鐘規則踢出。多 grace 同時有效時取最長者。

        僅對已登入使用者作用；靜態資源、SW、登出端點略過。
        """
        if not current_user.is_authenticated:
            return
        p = request.path or ""
        if (p.startswith("/static/") or p == "/sw.js"
                or p.startswith("/api/v1/")  # device 相關 endpoint
                or p == "/auth/logout"):
            return
        ua = (request.headers.get("User-Agent") or "").lower()
        is_mobile = any(k in ua for k in ("iphone", "ipad", "ipod", "android"))
        on_mynotes = (p == "/mynotes" or p.startswith("/mynotes/"))
        on_vetting = (p == "/vetting" or p.startswith("/vetting/"))
        import time
        now = int(time.time())
        if on_mynotes and not is_mobile:
            session["_mynotes_grace_until"] = now + 30 * 60
        if on_vetting:
            session["_vetting_grace_until"] = now + 15 * 60
        in_mynotes_grace = (not is_mobile) and now < session.get("_mynotes_grace_until", 0)
        in_vetting_grace = now < session.get("_vetting_grace_until", 0)
        if in_mynotes_grace:
            max_idle = 30 * 60
        elif in_vetting_grace:
            max_idle = 15 * 60
        else:
            max_idle = 5 * 60
        if session.get("_handover"):
            max_idle = max(max_idle, 15 * 60)   # 交接模式：15 分鐘純滑動
        last = session.get("_last_request_at")
        if last is not None and now - last > max_idle:
            logout_user()
            session.clear()
            return  # 後續 @login_required 會自動 redirect 到 weather
        session["_last_request_at"] = now
        session.permanent = True

    @app.after_request
    def _private_no_store(response):
        """敏感頁面與 API 禁止瀏覽器/中介快取，防止筆記內容留在瀏覽器快取。
        靜態資源、SW、健康檢查除外（需快取才有效率）。"""
        p = request.path or ""
        if (p.startswith("/static/") or p == "/sw.js"
                or p == "/healthz" or p == "/robots.txt"):
            return response
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, proxy-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    # ── 路徑別名：對外短路徑、對內原路徑 ──
    # 目的：Chrome 網址列 / autocomplete 只會看到 /s /n /m /f，看不出功能
    # 實作：(1) 進入時把短路徑換回原路徑讓 Flask 路由；(2) 回應時把 HTML/JSON/JS
    # 裡的原路徑字串替換成短路徑，讓瀏覽器抓到的連結、fetch 都用短路徑。
    PATH_ALIAS = [
        ("/admin", "/s"),
        ("/mynotes", "/m"),   # 需在 /notes 之前（避免 /mynotes 被 /notes 匹配到）
        ("/notes", "/n"),
        ("/vetting", "/v"),
        ("/face", "/f"),
    ]

    _orig_wsgi = app.wsgi_app

    def _path_alias_wsgi(environ, start_response):
        path = environ.get("PATH_INFO", "")
        for long, short in PATH_ALIAS:
            if path == short or path.startswith(short + "/"):
                environ["PATH_INFO"] = long + path[len(short):]
                break
        return _orig_wsgi(environ, start_response)

    app.wsgi_app = _path_alias_wsgi

    def _shorten_urls(text):
        for long, short in PATH_ALIAS:
            # 只替換帶引號的字串（避免誤傷筆記內容中的純文字 URL）
            # 同時涵蓋 ES6 template literal 的反引號
            text = text.replace('"' + long, '"' + short)
            text = text.replace("'" + long, "'" + short)
            text = text.replace('`' + long, '`' + short)
        return text

    @app.after_request
    def _rewrite_to_short_urls(response):
        # HTTP Location header（302/301 重導）
        loc = response.headers.get("Location")
        if loc:
            for long, short in PATH_ALIAS:
                if loc == long or loc.startswith(long + "/"):
                    response.headers["Location"] = short + loc[len(long):]
                    break
        # HTML / JSON / JS 回應內容
        ct = response.content_type or ""
        if (("text/html" in ct or "application/json" in ct
                or "application/javascript" in ct) and response.direct_passthrough is False):
            try:
                data = response.get_data(as_text=True)
                new_data = _shorten_urls(data)
                if new_data != data:
                    response.set_data(new_data)
            except (UnicodeDecodeError, RuntimeError):
                pass
        return response

    from auth.routes import auth_bp
    from face.routes import face_bp
    from notes.routes import notes_bp
    from weather.routes import weather_bp
    from admin.routes import admin_bp
    from device.routes import device_bp
    from mynotes.routes import mynotes_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(face_bp)
    app.register_blueprint(notes_bp)
    app.register_blueprint(weather_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(device_bp)
    app.register_blueprint(mynotes_bp)

    from vetting import vetting_bp
    app.register_blueprint(vetting_bp)

    from icinfo import icinfo_bp
    app.register_blueprint(icinfo_bp)

    # Dev-only login bypass blueprint + outbound HTTP guard. ONLY when
    # FLASK_DEV_MODE=1 — prod never sets this.
    if os.getenv("FLASK_DEV_MODE") == "1":
        from dev.routes import dev_bp
        app.register_blueprint(dev_bp)
        from dev.outbound_guard import install as _install_outbound_guard
        _install_outbound_guard()

    from notes.ws import register_ws_events
    register_ws_events(socketio)

    # Register PWA service worker scope
    @app.route("/sw.js")
    def service_worker():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, "sw.js",
                                   mimetype="application/javascript")

    @app.route("/healthz")
    def healthz():
        return "ok", 200

    @app.route("/")
    def index():
        if os.getenv("APP_MODE") == "notes":
            return redirect(url_for("auth.login"))
        return redirect(url_for("weather.index"))

    @app.route("/camera-test")
    def camera_test():
        return render_template("camera_test.html")

    return app


if __name__ == "__main__":
    app = create_app()
    # 用 socketio.run 而非 app.run：flask_socketio 在 async_mode="gevent" 下
    # 會啟 gevent.pywsgi server，整個 process 跑在 gevent loop 內。這樣
    # 路由 handler 內的 gevent.spawn(...) 才會被 schedule（werkzeug threading
    # 下 spawn 後 request thread 結束、greenlet 掛在死掉的 hub 上、永遠不執行
    # → AI 摘要 task 卡 processing 60 秒被 sweeper 改 任務逾時 ←本機踩到）。
    # debug=True 仍開啟 auto-reload + traceback page。
    from extensions import socketio
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
