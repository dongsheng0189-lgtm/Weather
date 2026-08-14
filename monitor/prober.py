"""定時探測 app-unified，把結果落地。

跑在一般 OS thread（monitor 沒有 gevent，不需要特別處理）。
gunicorn workers=1 —— 多個 worker 會各跑一份，取樣就重複了。
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from models import db, ProbeSample
from probe_core import probe

_log = logging.getLogger(__name__)


def _targets() -> list:
    """回傳 [(target 名稱, url)]。沒設就跳過該 target。

    public 那筆不可省略：只探內網 origin 時，DNS / Cloudflare 邊緣故障會被
    回報成「一切正常」，而使用者正連不上。
    """
    out = []
    for name, env in (("public", "PROBE_PUBLIC_URL"),
                      ("origin", "PROBE_ORIGIN_URL")):
        url = os.environ.get(env, "").strip()
        if url:
            out.append((name, url))
    return out


def run_once(app) -> list:
    samples = []
    for name, url in _targets():
        u = urlparse(url)
        use_tls = u.scheme == "https"
        port = u.port or (443 if use_tls else 80)
        try:
            r = probe(u.hostname, u.path or "/", port=port, use_tls=use_tls,
                      timeout=float(os.environ.get("PROBE_TIMEOUT", "15")))
        except Exception:
            # 探測炸掉不能讓迴圈死掉 —— 監控停擺比監控報錯更糟
            _log.exception("probe failed for %s", name)
            continue
        s = ProbeSample(
            ts=datetime.now(timezone.utc), target=name,
            verdict=r["verdict"], code=r["code"],
            t_dns=r["t_dns"], t_conn=r["t_conn"], t_tls=r["t_tls"],
            t_ttfb=r["t_ttfb"], t_total=r["t_total"], cf_ray=r["cf_ray"],
            app_pid=(r["app"] or {}).get("pid"),
            app_uptime_s=(r["app"] or {}).get("uptime_s"),
            err=r["err"])
        db.session.add(s)
        samples.append(s)
    db.session.commit()
    return samples


def sweep_old(app, days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = ProbeSample.query.filter(ProbeSample.ts < cutoff).delete()
    db.session.commit()
    return n


def start_prober(app) -> threading.Thread:
    interval = float(os.environ.get("PROBE_INTERVAL_SECONDS", "20"))
    retention = int(os.environ.get("RETENTION_DAYS", "14"))

    def loop():
        last_sweep = 0.0
        while True:
            started = time.monotonic()
            try:
                with app.app_context():
                    run_once(app)
                    if started - last_sweep > 3600:
                        sweep_old(app, retention)
                        last_sweep = started
            except Exception:
                _log.exception("prober loop iteration failed")
            time.sleep(max(1.0, interval - (time.monotonic() - started)))

    t = threading.Thread(target=loop, daemon=True, name="prober")
    t.start()
    return t
