"""
Dev-only blueprint: login bypass UI for local functional testing.

ONLY registered when FLASK_DEV_MODE=1 (set in .env.local).
Never registered in prod — app.py guards the import + register call.

URLs:
  GET  /dev               — list all seeded users with one-click login buttons
  GET  /dev/login/<name>  — log in as that user via Flask-Login session, redirect /s/r
  GET  /dev/logout        — logout + back to /dev
"""
import os
from flask import Blueprint, redirect, url_for, session, abort, render_template_string
from flask_login import login_user, logout_user, current_user

from models import User, Store


dev_bp = Blueprint("dev", __name__, url_prefix="/dev")


def _guard():
    if os.getenv("FLASK_DEV_MODE") != "1":
        abort(404)


_LIST_TPL = """<!doctype html>
<html lang="zh-TW"><head><meta charset="utf-8">
<title>Dev Login</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }
  h1 { color: #c0392b; }
  .warn { background: #fff3cd; border: 1px solid #ffc107; padding: .75rem 1rem; border-radius: 6px; margin-bottom: 1rem; }
  .who { background: #d1ecf1; padding: .5rem 1rem; border-radius: 6px; margin-bottom: 1rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: .5rem .75rem; border-bottom: 1px solid #eee; text-align: left; }
  th { background: #f7f7f7; }
  a.btn { background: #3498db; color: white; padding: .35rem .75rem; border-radius: 4px; text-decoration: none; font-size: .85rem; }
  a.btn:hover { background: #2980b9; }
  a.logout { background: #95a5a6; }
</style></head>
<body>
<h1>🛠 Dev Login</h1>
<div class="warn"><strong>本機開發專用</strong>。FLASK_DEV_MODE=1 才註冊、prod 永遠看不到。</div>
{% if current_user.is_authenticated %}
<div class="who">目前登入：<strong>{{ current_user.username }}</strong>（{{ current_user.role }}/{{ current_user.store or '無店' }}）
  &nbsp;<a class="btn logout" href="{{ url_for('dev.logout') }}">登出</a>
  &nbsp;<a class="btn" href="{{ url_for('weather.index') }}">回首頁</a>
</div>
{% endif %}
<table>
  <tr><th>帳號</th><th>角色</th><th>店</th><th>Features</th><th>動作</th></tr>
  {% for u in users %}
  <tr>
    <td>{{ u.username }}</td>
    <td>{{ u.role }}</td>
    <td>{{ u.store or '—' }}</td>
    <td style="color:#888;font-size:.8rem;">{{ u.get_features() | join(', ') }}</td>
    <td><a class="btn" href="{{ url_for('dev.login_as', username=u.username) }}">登入</a></td>
  </tr>
  {% endfor %}
</table>
<p style="margin-top:2rem;color:#888;font-size:.85rem;">PIN 全部是 1234（如果要走正式 /auth/verify 測登入流程）</p>
</body></html>
"""


@dev_bp.route("/")
def index():
    _guard()
    users = User.query.order_by(User.role.desc(), User.username).all()
    return render_template_string(_LIST_TPL, users=users)


@dev_bp.route("/login/<username>")
def login_as(username):
    _guard()
    u = User.query.filter_by(username=username).first()
    if not u:
        abort(404)
    # Logout any current user first so session is clean
    if current_user.is_authenticated:
        logout_user()
    login_user(u)
    session.permanent = True
    import time
    session["_last_request_at"] = int(time.time())
    return redirect(url_for("auth.silent_redirect"))


@dev_bp.route("/logout")
def logout():
    _guard()
    logout_user()
    return redirect(url_for("dev.index"))
