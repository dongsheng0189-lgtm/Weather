"""hub_watchdog：偵測 gevent hub 被同步工作卡住。

背景：dlib(face_recognition) 這類 C 擴充不讓出 gevent hub。一旦在 request handler
內直接呼叫，整個 worker 的所有 greenlet 都停擺 → 全站無回應（incident 2026-05-09
20:00，停 10+ 分鐘）。從外部只看得到「連不上」，看不出是誰卡的。

這個測試確保偵測器真的會響 —— 裝了卻不會叫的監控比沒裝更糟。
"""
import time

import pytest

gevent = pytest.importorskip("gevent")

from hub_watchdog import start  # noqa: E402


def test_fires_when_hub_is_blocked_by_sync_work():
    """time.sleep() 在 gevent 下會卡住整個 hub —— 偵測器必須抓到並回報時長。"""
    events = []
    stop = start(on_block=lambda d, s: events.append((d, s)),
                 threshold=0.3, interval=0.05)
    try:
        gevent.sleep(0.2)          # 讓 heartbeat 先跑起來
        time.sleep(1.0)            # ← 模擬 dlib：真的把 hub 卡住 1 秒
        gevent.sleep(0.3)          # 讓偵測器有機會回報
    finally:
        stop()

    assert events, "hub 被卡 1 秒卻沒有回報 —— 偵測器沒作用"
    blocked_for, stack = events[0]
    assert blocked_for >= 0.3, f"回報的阻塞時間 {blocked_for:.2f}s 不合理"
    assert isinstance(stack, str) and stack, "應附上當下堆疊以指認兇手"


def test_quiet_when_hub_is_healthy():
    """正常運作時不能亂叫，否則 log 會被雜訊淹沒。"""
    events = []
    stop = start(on_block=lambda d, s: events.append((d, s)),
                 threshold=0.5, interval=0.05)
    try:
        for _ in range(10):
            gevent.sleep(0.05)     # 乖乖讓出 hub
    finally:
        stop()

    assert not events, f"hub 健康卻誤報 {len(events)} 次"


def test_reports_once_per_episode_not_per_check():
    """一次長阻塞只回報一次 —— 不要每個檢查週期都刷一行。"""
    events = []
    stop = start(on_block=lambda d, s: events.append((d, s)),
                 threshold=0.2, interval=0.05)
    try:
        gevent.sleep(0.2)
        time.sleep(1.2)            # 遠超過 threshold 的多個檢查週期
        gevent.sleep(0.3)
    finally:
        stop()

    assert len(events) == 1, f"單次阻塞回報了 {len(events)} 次，會洗版"


def test_silent_until_hub_actually_starts_running():
    """worker 開機時 hub 可能還沒開始跑，心跳自然落後 —— 那不是阻塞，不可誤報。

    模擬：start() 之後完全不讓 hub 跑（不呼叫任何 gevent.sleep），只用真實時間
    等待超過 threshold。此時 heartbeat greenlet 一次都還沒執行過。
    """
    events = []
    stop = start(on_block=lambda d, s: events.append((d, s)),
                 threshold=0.3, interval=0.05)
    try:
        time.sleep(0.8)            # 真實時間流逝，但 hub 從未被排程過
    finally:
        stop()

    assert not events, "hub 尚未啟動就誤報阻塞，開機時會洗一堆假警報"


def test_default_report_is_lock_free_and_lands_in_log(tmp_path, monkeypatch):
    """預設回報必須能從真 OS thread 安全執行。

    monkey.patch_all() 後 logging 的 RLock 會變成 gevent 鎖，從非 greenlet 的
    真執行緒去搶就會踩到 "crossing thread boundaries is not allowed"（prod
    2026-05-09 實際發生過）。所以預設回報改用 os.write，不經 logging。
    """
    import hub_watchdog

    logfile = tmp_path / "app.log"
    monkeypatch.setattr(hub_watchdog, "_LOG_PATH", str(logfile))

    # 從真 OS thread 呼叫 —— 正是 prod 裡的執行情境
    from gevent.monkey import get_original
    Thread = get_original("threading", "Thread")
    err = []
    t = Thread(target=lambda: _safe(hub_watchdog._default_on_block, err))
    t.start()
    t.join(5)

    assert not err, f"預設回報在真執行緒中拋錯：{err}"
    content = logfile.read_text(encoding="utf-8")
    assert "hub_watchdog" in content and "8.0 秒" in content
    assert "假堆疊內容" in content, "堆疊沒被寫進去，事後就指認不出兇手"


def test_detects_block_after_monkey_patch_all():
    """prod 是 monkey.patch_all() 之後才啟動 watchdog —— 必須在那個狀態下有效。

    patch 之後 time.sleep 變成 gevent 的版本。檢查迴圈跑在一個沒有 hub 的
    真 OS thread 上，呼叫它會讓迴圈永遠不前進 —— 監控看起來裝好了，實際上
    一次都不會叫（2026-08-14 實測踩到，單元測試因為沒 patch 而全綠）。

    在子行程執行：monkey.patch_all() 會汙染整個測試 session。
    """
    import subprocess
    import sys
    import os

    script = """
from gevent import monkey; monkey.patch_all()
import sys; sys.path.insert(0, %r)
import gevent, time
from gevent.monkey import get_original
from hub_watchdog import start

events = []
stop = start(on_block=lambda d, s: events.append(d), threshold=0.3, interval=0.05)
gevent.sleep(0.2)
get_original("time", "sleep")(1.0)     # 真的卡住 hub
gevent.sleep(0.5)
stop()
print("FIRED" if events else "MISSED")
""" % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    r = subprocess.run([sys.executable, "-c", script],
                       capture_output=True, text=True, timeout=120)
    assert "FIRED" in r.stdout, (
        f"monkey patch 下 watchdog 沒觸發。stdout={r.stdout!r} stderr={r.stderr[-2000:]!r}")


def test_block_report_is_shipped_to_monitor(tmp_path, monkeypatch):
    """hub 被卡住時證據必須離開容器，否則重啟就沒了。"""
    import hub_watchdog
    import monitor_ship

    monkeypatch.setattr(hub_watchdog, "_LOG_PATH", str(tmp_path / "app.log"))
    shipped = []
    monkeypatch.setattr(monitor_ship, "ship_event",
                        lambda *a: shipped.append(a) or True)

    hub_watchdog._default_on_block(12.5, "stack: face_locations")

    assert shipped, "阻塞事件沒有送到 monitor"
    kind, summary, detail = shipped[0]
    assert kind == "hub_block"
    assert "12.5" in summary
    assert "face_locations" in detail


def _safe(fn, errbox):
    try:
        fn(8.0, "假堆疊內容")
    except BaseException as e:  # noqa: BLE001 - 測試要抓所有失敗
        errbox.append(repr(e))


def test_stop_is_idempotent():
    """重複 stop() 不該爆 —— worker 關閉路徑上不容許意外例外。"""
    stop = start(on_block=lambda d, s: None, threshold=1.0, interval=0.05)
    stop()
    stop()
