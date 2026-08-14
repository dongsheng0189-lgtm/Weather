"""檢視頁：單一密碼登入，呈現異常摘要 / 事件 / 取樣。

只有一個讀者，所以不做使用者系統。但未登入不得看到內容 —— 堆疊會揭露
內部路徑。
"""
import hmac
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import redirect, render_template, request, session, url_for

from models import Event, ProbeSample

TW = timezone(timedelta(hours=8))


def _login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


def _detect_restarts(samples) -> list:
    """依時間序找 app_pid 變動 —— 每次變動代表上一個 worker 沒了。"""
    out, prev = [], {}
    for s in sorted(samples, key=lambda x: x.ts):
        if s.app_pid is None:
            continue
        old = prev.get(s.target)
        if old is not None and old != s.app_pid:
            out.append({"ts": s.ts, "target": s.target,
                        "old_pid": old, "new_pid": s.app_pid})
        prev[s.target] = s.app_pid
    return out


def register_views(app):
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            expected = os.environ.get("MONITOR_PASSWORD", "")
            given = request.form.get("password", "")
            if expected and hmac.compare_digest(given, expected):
                session["authed"] = True
                return redirect(url_for("dashboard"))
            return render_template("login.html", error="密碼錯誤"), 401
        return render_template("login.html", error=None)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @_login_required
    def dashboard():
        hours = int(request.args.get("hours", "24"))
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        samples = (ProbeSample.query.filter(ProbeSample.ts >= since)
                   .order_by(ProbeSample.ts.desc()).limit(2000).all())
        events = (Event.query.filter(Event.ts >= since)
                  .order_by(Event.ts.desc()).limit(200).all())

        bad = [s for s in samples if s.verdict not in ("ok", "slow")]
        counts = {}
        for s in bad:
            counts[s.verdict] = counts.get(s.verdict, 0) + 1

        return render_template(
            "dashboard.html", hours=hours, samples=samples[:300],
            events=events, counts=counts, bad_total=len(bad),
            total=len(samples), restarts=_detect_restarts(samples),
            tw=TW)
