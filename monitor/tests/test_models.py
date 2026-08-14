from datetime import datetime, timezone


def test_probe_sample_roundtrip(app):
    from models import db, ProbeSample
    s = ProbeSample(ts=datetime.now(timezone.utc), target="public",
                    verdict="ttfb_hang", t_conn=0.05, t_ttfb=15.0,
                    t_total=15.1, err="no response byte")
    db.session.add(s)
    db.session.commit()
    got = ProbeSample.query.one()
    assert got.target == "public"
    assert got.verdict == "ttfb_hang"
    assert got.code is None


def test_event_stores_full_stack_without_truncation(app):
    """堆疊是指認兇手的唯一線索，不能被截斷。"""
    from models import db, Event
    stack = "x" * 20000
    e = Event(ts=datetime.now(timezone.utc), source="app-unified",
              kind="hub_block", severity="warning",
              summary="hub blocked 42.0s", detail=stack)
    db.session.add(e)
    db.session.commit()
    assert len(Event.query.one().detail) == 20000
