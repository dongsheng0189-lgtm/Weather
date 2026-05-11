"""Worker timeout regression (2026-05-10/11).

Background: under gunicorn geventwebsocket worker + monkey.patch_all(), any
native blocking I/O inside the worker (PG socket read/write via psycopg2)
prevents the worker from yielding back to the gevent hub. When a PG
connection goes stale (server-side idle timeout closes it), psycopg2 blocks
in native socket recv waiting on a dead fd → worker can't send heartbeat to
gunicorn master → master SIGKILLs after `timeout=60s`. Repeated across both
workers gives the "WORKER TIMEOUT (pid:N)" cascade observed 2026-05-10 21:15-21:17.

The fix has two parts that MUST both be in place:

1. psycogreen.gevent.patch_psycopg() — monkey-patches psycopg2's wait callback
   so PG socket waits yield to the gevent hub instead of blocking. Must run
   AFTER monkey.patch_all() and BEFORE any psycopg2 connection is opened.

2. SQLAlchemy engine options — pool_pre_ping catches dead connections at
   checkout time, pool_recycle proactively recycles connections before the
   PG server's idle timeout (commonly 8h, but Zeabur PG can be shorter) so
   pre_ping doesn't have to be the only line of defense.

These tests verify both parts at source level — they do not call create_engine
or open a real PG connection (we don't have Postgres in CI).
"""
import pathlib


WSGI_PATH = pathlib.Path(__file__).resolve().parent.parent / "wsgi.py"


def test_wsgi_imports_psycogreen():
    """psycogreen.gevent.patch_psycopg must be imported in wsgi.py.

    Without this, psycopg2 PG I/O blocks the gevent hub natively → worker
    can't notify master → 60s timeout → SIGKILL cascade.
    """
    text = WSGI_PATH.read_text()
    assert "psycogreen" in text, (
        "wsgi.py must import psycogreen — without it PG I/O blocks the "
        "gevent hub and gunicorn SIGKILLs the worker after timeout=60s "
        "(see 2026-05-10 21:15 WORKER TIMEOUT cascade)"
    )


def test_psycogreen_patch_runs_after_monkey_patch_all():
    """psycogreen.gevent.patch_psycopg() must be called AFTER monkey.patch_all().

    Order matters: monkey.patch_all replaces socket/threading; psycogreen
    then re-points psycopg2's wait callback at gevent.socket.wait_read /
    wait_write. Wrong order = patch has no effect.
    """
    text = WSGI_PATH.read_text()
    monkey_idx = text.find("monkey.patch_all()")
    psyco_idx = text.find("patch_psycopg()")
    assert monkey_idx >= 0, "monkey.patch_all() missing from wsgi.py"
    assert psyco_idx >= 0, (
        "psycogreen.gevent.patch_psycopg() not called — PG socket I/O will "
        "block the gevent hub and trigger worker timeout SIGKILL"
    )
    assert psyco_idx > monkey_idx, (
        "patch_psycopg() must be AFTER monkey.patch_all() — otherwise psycopg2's "
        "wait callback is set against the pre-patched socket module and the "
        "gevent integration is silently broken"
    )


def test_db_engine_options_have_pre_ping():
    """pool_pre_ping=True catches dead connections at checkout time.

    Required because Zeabur PG idle-times-out idle connections; without
    pre_ping the next query blocks on a closed socket → worker timeout.
    """
    from config import Config
    opts = Config.SQLALCHEMY_ENGINE_OPTIONS
    assert opts.get("pool_pre_ping") is True, (
        "SQLALCHEMY_ENGINE_OPTIONS must set pool_pre_ping=True so dead PG "
        "connections (PG server idle-timeout closed them) are caught and "
        "recreated on checkout, not on the next query"
    )


def test_db_engine_options_have_pool_recycle():
    """pool_recycle=N proactively recycles connections before PG closes them.

    Belt-and-suspenders with pool_pre_ping: pre_ping costs 1 RTT per checkout
    even on healthy connections, recycle avoids accumulating to the age
    where pre_ping would fail.
    """
    from config import Config
    opts = Config.SQLALCHEMY_ENGINE_OPTIONS
    recycle = opts.get("pool_recycle")
    assert recycle, (
        "SQLALCHEMY_ENGINE_OPTIONS must set pool_recycle=N (seconds) to "
        "proactively recycle connections before PG server's idle timeout "
        "closes them — defense in depth on top of pool_pre_ping"
    )
    assert isinstance(recycle, int) and recycle > 0, (
        f"pool_recycle should be a positive int (seconds), got {recycle!r}"
    )
    # Common PG idle timeout is 8h (28800s); recycle should be well below.
    assert recycle <= 7200, (
        f"pool_recycle={recycle}s is too long — should be <= 2h to safely "
        f"stay under Zeabur PG idle timeout (commonly shorter than 8h default)"
    )
