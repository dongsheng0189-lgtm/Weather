import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import logging

logging.basicConfig(
    stream=sys.stdout,
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    force=True,
)

from datetime import timedelta
from flask import Flask, redirect, url_for, render_template
from config import Config
from extensions import db, login_manager, limiter, socketio
from models import User


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=5)

    db.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)
    socketio.init_app(app, cors_allowed_origins=app.config["ALLOWED_ORIGINS"], async_mode="gevent")

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Date formatting filter for templates (handles both datetime and legacy str)
    @app.template_filter("fmt_date")
    def fmt_date(value, fmt="%Y-%m-%d"):
        if not value:
            return ""
        if isinstance(value, str):
            return value[:10]
        return value.strftime(fmt)

    @app.template_filter("fmt_datetime")
    def fmt_datetime(value, fmt="%Y-%m-%d %H:%M"):
        if not value:
            return ""
        if isinstance(value, str):
            return value[:16]
        return value.strftime(fmt)

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

    with app.app_context():
        try:
            print(f"=== DB URI: {app.config['SQLALCHEMY_DATABASE_URI'][:30]}... ===", flush=True)
            db.create_all()
            # 修復 PostgreSQL sequence（避免 duplicate key error）
            if 'postgresql' in app.config['SQLALCHEMY_DATABASE_URI']:
                for table in ['notes', 'users', 'stores', 'note_logs', 'trusted_devices', 'user_logs']:
                    try:
                        db.session.execute(db.text(
                            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                        ))
                    except Exception:
                        pass
                # notes.user_id 允許 NULL + FK ON DELETE SET NULL（刪除使用者保留筆記）
                try:
                    db.session.execute(db.text("ALTER TABLE notes ALTER COLUMN user_id DROP NOT NULL"))
                except Exception:
                    pass
                # notes.author_name：建立當下的作者帳號快照
                try:
                    db.session.execute(db.text("ALTER TABLE notes ADD COLUMN IF NOT EXISTS author_name TEXT"))
                except Exception:
                    pass
                # 回填現有筆記的 author_name（只處理尚未填的）
                try:
                    db.session.execute(db.text("""
                        UPDATE notes SET author_name = users.username
                        FROM users
                        WHERE notes.user_id = users.id AND notes.author_name IS NULL
                    """))
                except Exception:
                    pass
                # trusted_devices: 加 client_uid 欄位，拿掉 fingerprint unique，加 client_uid 的 partial unique index
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE trusted_devices ADD COLUMN IF NOT EXISTS client_uid TEXT"
                    ))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text("""
                        DO $$
                        BEGIN
                            IF EXISTS (
                                SELECT 1 FROM pg_constraint
                                WHERE conname = 'trusted_devices_fingerprint_key'
                            ) THEN
                                ALTER TABLE trusted_devices DROP CONSTRAINT trusted_devices_fingerprint_key;
                            END IF;
                        END $$;
                    """))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_trusted_devices_client_uid "
                        "ON trusted_devices(client_uid) WHERE client_uid IS NOT NULL"
                    ))
                except Exception:
                    pass
                # notes.note_type：區分一般筆記和確認表單
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS note_type TEXT NOT NULL DEFAULT 'note'"
                    ))
                except Exception:
                    pass
                # note_attachments.checklist_item_id：附件可以綁定到確認表單項目
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE note_attachments ADD COLUMN IF NOT EXISTS checklist_item_id INTEGER "
                        "REFERENCES checklist_items(id) ON DELETE CASCADE"
                    ))
                except Exception:
                    pass
                # notes.locked_by / locked_at：編輯鎖
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS locked_by INTEGER "
                        "REFERENCES users(id) ON DELETE SET NULL"
                    ))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS locked_at TIMESTAMP"
                    ))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text("""
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
                    """))
                except Exception:
                    pass
                # note_logs.user_id → ON DELETE SET NULL
                try:
                    db.session.execute(db.text("""
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
                    """))
                except Exception:
                    pass
                # note_attachments.user_id：允許 NULL + ON DELETE SET NULL
                try:
                    db.session.execute(db.text("ALTER TABLE note_attachments ALTER COLUMN user_id DROP NOT NULL"))
                except Exception:
                    pass
                try:
                    db.session.execute(db.text("""
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
                    """))
                except Exception:
                    pass
                # users.features：模組開關 JSON 陣列
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS features TEXT NOT NULL DEFAULT '[\"notes\"]'"
                    ))
                except Exception:
                    pass
                # 私人筆記三張表由 db.create_all() 自動建立（純新增，無需 ALTER）
                # 但後續新增欄位需 ALTER：
                try:
                    db.session.execute(db.text(
                        "ALTER TABLE private_checklist_items ADD COLUMN IF NOT EXISTS reminder_date DATE"
                    ))
                except Exception:
                    pass
                # login_tokens.user_id → ON DELETE CASCADE（刪使用者連帶清 tokens）
                try:
                    db.session.execute(db.text("""
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
                    """))
                except Exception:
                    pass
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
    app.run(host="0.0.0.0", port=5000, debug=True)
