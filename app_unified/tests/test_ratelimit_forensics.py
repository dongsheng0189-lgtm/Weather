"""429 彙總器：決定要不要送、以及送出去的形狀。

用假時鐘測，完全不碰網路——寄送是呼叫端的事。
最重要的一條是 test_payload_never_contains_raw_ip：monitor 沒有欄位加密，
其前提就是這裡送出去的東西不含個資。
"""
from ratelimit_forensics import HitAggregator


def test_first_hit_reports_immediately():
    """尾端遺失防護：若等滿視窗才送，爆完就沒下文的那批會永遠送不出去。"""
    agg = HitAggregator(salt="s", window_seconds=60.0)
    payload = agg.record("/auth/verify", "10.0.0.5", None, now=100.0)
    assert payload is not None
    assert payload["kind"] == "rate_limit"
    assert "/auth/verify" in payload["summary"]
    assert "×1" in payload["summary"]


def test_second_hit_within_window_is_buffered():
    agg = HitAggregator(salt="s", window_seconds=60.0)
    agg.record("/auth/verify", "10.0.0.5", None, now=100.0)
    assert agg.record("/auth/verify", "10.0.0.5", None, now=130.0) is None


def test_hit_after_window_reports_accumulated_count():
    agg = HitAggregator(salt="s", window_seconds=60.0)
    agg.record("/auth/verify", "10.0.0.5", None, now=100.0)   # 送出（第一則）
    agg.record("/auth/verify", "10.0.0.5", None, now=130.0)   # 累計
    payload = agg.record("/auth/verify", "10.0.0.5", None, now=200.0)
    assert payload is not None
    assert "×2" in payload["summary"], "視窗內累計的次數要一起送出，不是只送 1"


def test_distinct_clients_counted_by_hash():
    agg = HitAggregator(salt="s", window_seconds=60.0)
    agg.record("/auth/verify", "10.0.0.5", None, now=100.0)
    agg.record("/auth/verify", "10.0.0.5", None, now=101.0)
    agg.record("/auth/verify", "10.0.0.9", None, now=102.0)
    payload = agg.record("/auth/verify", "10.0.0.5", None, now=200.0)
    assert "2 clients" in payload["summary"]


def test_single_client_reported_as_singular():
    """distinct clients 恆為 1 就是「額度全體共用」的證據，字面要清楚。"""
    agg = HitAggregator(salt="s", window_seconds=60.0)
    agg.record("/auth/verify", "10.0.0.5", None, now=100.0)
    agg.record("/auth/verify", "10.0.0.5", None, now=101.0)
    payload = agg.record("/auth/verify", "10.0.0.5", None, now=200.0)
    assert "1 client," in payload["summary"]


def test_payload_never_contains_raw_ip():
    agg = HitAggregator(salt="s", window_seconds=60.0)
    payload = agg.record("/auth/verify", "203.0.113.7",
                         "203.0.113.7, 10.1.2.3", now=100.0)
    blob = payload["summary"] + payload["detail"]
    assert "203.0.113.7" not in blob
    assert "10.1.2.3" not in blob


def test_same_ip_same_hash_different_ip_different_hash():
    agg = HitAggregator(salt="s", window_seconds=60.0)
    a = agg.record("/a", "10.0.0.5", None, now=100.0)["detail"]
    b = agg.record("/b", "10.0.0.5", None, now=100.0)["detail"]
    c = agg.record("/c", "10.0.0.9", None, now=100.0)["detail"]
    assert a.split("clients: ")[1] == b.split("clients: ")[1]
    assert a.split("clients: ")[1] != c.split("clients: ")[1]


def test_salt_changes_hash():
    """salt 保密是這個雜湊唯一的安全性來源（IPv4 空間可暴力枚舉）。"""
    p1 = HitAggregator(salt="s1").record("/a", "10.0.0.5", None, now=1.0)
    p2 = HitAggregator(salt="s2").record("/a", "10.0.0.5", None, now=1.0)
    assert p1["detail"] != p2["detail"]


def test_xff_hops_counted():
    agg = HitAggregator(salt="s", window_seconds=60.0)
    payload = agg.record("/auth/verify", "10.0.0.5",
                         "203.0.113.7, 10.1.2.3", now=100.0)
    assert "xff_hops: 2" in payload["detail"]


def test_missing_xff_is_zero_hops():
    agg = HitAggregator(salt="s", window_seconds=60.0)
    payload = agg.record("/auth/verify", "10.0.0.5", None, now=100.0)
    assert "xff_hops: 0" in payload["detail"]


def test_mixed_hop_counts_all_reported():
    """層數不一致本身就是可疑訊號，不能只留最後一個。"""
    agg = HitAggregator(salt="s", window_seconds=60.0)
    agg.record("/auth/verify", "10.0.0.5", None, now=100.0)
    agg.record("/auth/verify", "10.0.0.5", "203.0.113.7, 10.1.2.3", now=110.0)
    payload = agg.record("/auth/verify", "10.0.0.5", None, now=200.0)
    assert "xff_hops: 0,2" in payload["detail"]


def test_paths_aggregated_separately():
    """/auth/verify 與 /auth/change-pin 各有各的限流，混在一起會看不出是哪條爆了。"""
    agg = HitAggregator(salt="s", window_seconds=60.0)
    agg.record("/auth/verify", "10.0.0.5", None, now=100.0)
    payload = agg.record("/auth/change-pin", "10.0.0.5", None, now=101.0)
    assert payload is not None, "另一條路徑的第一次命中應該自己送一則"
    assert "/auth/change-pin" in payload["summary"]


def test_first_report_labelled_in_detail():
    agg = HitAggregator(salt="s", window_seconds=60.0)
    payload = agg.record("/auth/verify", "10.0.0.5", None, now=100.0)
    assert "window: first report" in payload["detail"]


def test_later_report_states_elapsed():
    agg = HitAggregator(salt="s", window_seconds=60.0)
    agg.record("/auth/verify", "10.0.0.5", None, now=100.0)
    payload = agg.record("/auth/verify", "10.0.0.5", None, now=163.2)
    assert "63.2s since last report" in payload["detail"]


def test_summary_fits_event_column():
    """mon_events.summary 是 String(500)，超過會被 /ingest 截斷。"""
    agg = HitAggregator(salt="s", window_seconds=60.0)
    payload = agg.record("/" + "x" * 900, "10.0.0.5", None, now=100.0)
    assert len(payload["summary"]) <= 500
