"""LazyAppProxy /healthz fast-path: ensures readiness probe survives slow create_app.

Background: cold start runs db.create_all + ~30 PG migrations + heavy blueprint
imports (face_recognition, sentence_transformers) that take 30+ seconds. During
that window the Gunicorn worker can't accept any request, so Zeabur readiness
probe times out and kills the pod in a restart loop.

Fix: wsgi_lazy.LazyAppProxy answers GET /healthz with 200 OK immediately while
real Flask init runs in a background OS thread (gevent threadpool, so the gevent
hub stays free to handle the probe).

Note: tests do NOT call monkey.patch_all() because pytest itself uses threading
internally. We're testing LazyAppProxy as a plain Python unit; gevent.threadpool
works the same with or without monkey patching (it always uses real OS threads).
"""
import threading
import time

import pytest

from wsgi_lazy import LazyAppProxy


class _FakeFlaskApp:
    """Stand-in for a real Flask app — minimal WSGI callable + attribute exposure."""

    config = {"TESTING": True}

    def __call__(self, environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"flask response"]


def _wsgi_call(wsgi_app, path):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(wsgi_app({"PATH_INFO": path, "REQUEST_METHOD": "GET"}, start_response))
    captured["body"] = body
    return captured


def test_healthz_responds_immediately_while_factory_blocks():
    """The whole point: /healthz must NOT wait for create_app to finish.

    If this regresses, Zeabur readiness probe will time out during cold start
    and the pod will be killed in a restart loop (the bug we're fixing).
    """
    factory_started = threading.Event()
    factory_release = threading.Event()

    def slow_factory():
        factory_started.set()
        # Simulate the real cold start: 30+ seconds of DB migrations.
        # We block until the test releases us so we can test mid-init behavior.
        factory_release.wait(timeout=10)
        return _FakeFlaskApp()

    proxy = LazyAppProxy(slow_factory)

    # Make sure the factory is actively running (i.e. mid-init).
    assert factory_started.wait(timeout=2), "factory thread never started"

    # /healthz must return 200 OK immediately even while factory is blocked.
    t0 = time.time()
    result = _wsgi_call(proxy, "/healthz")
    elapsed = time.time() - t0

    assert result["status"] == "200 OK", f"got {result['status']}"
    assert result["body"] == b"ok"
    assert elapsed < 0.5, f"/healthz took {elapsed:.2f}s — fast-path is broken"

    factory_release.set()


def test_non_healthz_path_waits_for_init_then_delegates():
    """Other paths must wait for create_app to finish, then go to the real Flask app."""
    proxy = LazyAppProxy(lambda: _FakeFlaskApp())

    result = _wsgi_call(proxy, "/some-path")
    assert result["status"] == "200 OK"
    assert result["body"] == b"flask response"


def test_factory_exception_returns_503_on_healthz_so_k8s_restarts():
    """If create_app crashes, /healthz must signal unhealthy (5xx) so the K8s
    liveness probe fails and the pod is restarted. Previous behavior always
    returned 200 to "keep the debugging window open"; in production that
    leaves a permanently broken pod alive instead of letting K8s recreate it."""

    def bad_factory():
        raise RuntimeError("simulated create_app failure")

    proxy = LazyAppProxy(bad_factory)

    deadline = time.time() + 2
    while time.time() < deadline and not proxy._init_done.is_set():
        time.sleep(0.05)
    assert proxy._init_done.is_set(), "factory thread never finished"

    healthz = _wsgi_call(proxy, "/healthz")
    assert healthz["status"].startswith("503"), (
        f"after init failure /healthz must signal unhealthy, got {healthz['status']}"
    )

    other = _wsgi_call(proxy, "/anything")
    assert other["status"].startswith("500"), f"got {other['status']}"
    assert b"simulated create_app failure" in other["body"]


def test_healthz_delegates_to_flask_after_init_done():
    """Once Flask is initialized, /healthz must hit the real Flask app's /healthz
    route — not the fast-path. If Flask hub is later blocked (e.g. dlib in a
    greenlet), the delegation hangs, K8s liveness probe times out, pod restarts.

    Previous behavior: fast-path always returned 200 → K8s thought pod healthy
    even when Flask hung → 10+ min outage waiting for gunicorn timeout=600
    (incident 2026-05-09 20:00)."""

    class HealthzAwareFakeApp:
        config = {"TESTING": True}

        def __call__(self, environ, start_response):
            if environ.get("PATH_INFO") == "/healthz":
                start_response("200 OK", [("Content-Type", "text/plain")])
                return [b"flask-healthz"]
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"flask response"]

    proxy = LazyAppProxy(lambda: HealthzAwareFakeApp())
    assert proxy._init_done.wait(timeout=2)

    result = _wsgi_call(proxy, "/healthz")
    assert result["status"] == "200 OK"
    assert result["body"] == b"flask-healthz", (
        f"post-init /healthz must delegate to Flask (body=flask-healthz), "
        f"not return fast-path body=ok. Got: {result['body']}"
    )


def test_attribute_access_delegates_to_real_flask_app_after_init():
    """gunicorn.conf.py and elsewhere may do `app.config[...]` / `app.app_context()`.
    Proxy must transparently forward attribute access once Flask app is loaded."""
    proxy = LazyAppProxy(lambda: _FakeFlaskApp())

    # Wait for init.
    assert proxy._init_done.wait(timeout=2)
    assert proxy.config == {"TESTING": True}


def test_concurrent_healthz_during_slow_init():
    """Multiple greenlets hitting /healthz at once must all get 200 — no lock contention
    with the factory thread. Zeabur probe can fire while gunicorn is still warming up."""
    factory_release = threading.Event()

    def slow_factory():
        factory_release.wait(timeout=5)
        return _FakeFlaskApp()

    proxy = LazyAppProxy(slow_factory)
    time.sleep(0.05)  # let factory thread enter wait

    results = []

    def hit_healthz():
        results.append(_wsgi_call(proxy, "/healthz"))

    threads = [threading.Thread(target=hit_healthz) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    assert len(results) == 10
    for r in results:
        assert r["status"] == "200 OK"
        assert r["body"] == b"ok"

    factory_release.set()
