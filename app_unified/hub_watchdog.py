"""偵測 gevent hub 被同步工作卡住，並指認卡住它的程式碼。

背景：dlib(face_recognition)、大型 numpy 運算這類 C 擴充不會讓出 gevent hub。
在 request handler 內直接呼叫，整個 worker 的所有 greenlet 都會停擺 —— 包含
/healthz，於是 K8s liveness probe 失敗、pod 被重啟（incident 2026-05-09 20:00，
停 10+ 分鐘）。從外部只看得到「連不上」，看不出兇手是誰。

作法（兩段式，缺一不可）：
  1. 一個 greenlet 每 `interval` 秒更新心跳時間戳。hub 被卡住時它跑不動，
     時間戳就會停在原地。
  2. 一個**真正的 OS thread**（不是 greenlet，否則會跟著一起被卡住）每
     `interval` 秒檢查心跳落後多久。超過 `threshold` 就回報，並抓取當下
     各執行緒的堆疊 —— 那就是正在霸佔 hub 的呼叫。

註：若卡住 hub 的 C 程式碼連 GIL 都不放，檢查執行緒也會排不到，此時堆疊會在
GIL 釋放的瞬間才抓到、未必精準；但阻塞「時長」的量測仍然正確，配合 access log
的 %(D)s 請求耗時就足以指認。
"""
import os
import sys
import threading
import time
import traceback

_MAX_STACK_FRAMES = 25
# 跟 app.py 的 RotatingFileHandler 同一個檔；Zeabur log viewer 對此 service
# 曾卡 stale，stdout 未必看得到，所以另外直接落地一份。
_LOG_PATH = os.environ.get("HUB_WATCHDOG_LOG", "/tmp/app.log")


def _original_sleep():
    """取回未被 patch 的 time.sleep。

    monkey.patch_all() 之後 time.sleep 是 gevent 版本，會讓出給當前 hub。
    檢查迴圈跑在一個沒有 hub 的真 OS thread 上，呼叫它會讓迴圈永遠不前進 ——
    監控看起來裝好了卻一次都不會叫（2026-08-14 實測踩到）。
    """
    try:
        from gevent.monkey import get_original
        return get_original("time", "sleep")
    except (ImportError, AttributeError):
        return time.sleep


def _real_thread(target):
    """真正的 OS thread，且不經過 threading.Thread。

    兩個都要避開：
    - `threading.Thread`：monkey.patch_all() 後它是 greenlet 包裝，檢查執行緒
      會跟被監控的 hub 一起卡死，監控直接失效。
    - `get_original("threading", "Thread")`：拿回原始 class 仍不夠。它的
      `start()` 會等新執行緒 set 一個 `threading.Event`，而那個 Event **已經
      被 patch 成 gevent 版本** —— 真 OS thread 去 set 主 hub 的 gevent 物件，
      正是 "crossing thread boundaries"。實測會隨機掛死在 start()（2026-08-14）。

    `_thread.start_new_thread` 沒有這層 Event 握手，直接建立 OS thread 就返回。
    """
    def _guarded():
        try:
            target()
        except BaseException:      # 裸執行緒沒有預設例外處理，靜靜死掉最糟
            traceback.print_exc()

    try:
        from gevent.monkey import get_original
        start_new_thread = get_original("_thread", "start_new_thread")
    except (ImportError, AttributeError):
        import _thread
        start_new_thread = _thread.start_new_thread
    return start_new_thread(_guarded, ())


def _capture_stacks() -> str:
    """抓取所有執行緒當下的堆疊，過濾掉監控自己的框架。"""
    out = []
    try:
        frames = sys._current_frames()
    except Exception as e:  # pragma: no cover - 理論上不會發生
        return f"<無法取得堆疊: {e!r}>"

    me = threading.current_thread().ident
    names = {t.ident: t.name for t in threading.enumerate()}
    for tid, frame in frames.items():
        if tid == me:
            continue  # 監控執行緒自己的堆疊沒有價值
        stack = traceback.format_stack(frame, limit=_MAX_STACK_FRAMES)
        out.append(f"--- thread {names.get(tid, '?')} ({tid}) ---\n" + "".join(stack))
    return "\n".join(out) if out else "<沒抓到其他執行緒堆疊>"


def _default_on_block(blocked_for: float, stack: str) -> None:
    """從真 OS thread 回報，所以刻意**不用 logging 模組**。

    monkey.patch_all() 之後 threading.RLock 會被換成 gevent 的鎖，而 logging 的
    handler 內部就靠 RLock。從一個非 greenlet 的真執行緒去搶 gevent 鎖，正是
    "using gevent objects from multiple threads ... crossing thread boundaries
    is not allowed" 那個錯誤（prod 2026-05-09 出現過）。監控自己去引爆它太諷刺。

    改成 os.write 直接寫 fd：沒有鎖、沒有 gevent 物件，任何執行緒都安全。
    同時寫 stderr（Zeabur log）與 /tmp/app.log（Zeabur log viewer 曾卡 stale 的備援）。
    """
    msg = (f"\n[hub_watchdog] {time.strftime('%Y-%m-%d %H:%M:%S')} "
           f"gevent hub 被卡住 {blocked_for:.1f} 秒 —— 期間所有請求(含 /healthz)無回應。\n"
           f"當下堆疊：\n{stack}\n").encode("utf-8", "replace")
    try:
        os.write(2, msg)
    except OSError:
        pass
    try:
        fd = os.open(_LOG_PATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, msg)
        finally:
            os.close(fd)
    except OSError:
        pass

    # 推一份到 monitor —— /tmp/app.log 會隨容器重啟清空，而「卡死→被 SIGKILL
    # →重啟」正是我們在追的劇本，最重要的那份 log 最容易消失。
    # ship_event 內部用 unpatched socket，本函式在真 OS thread 上執行。
    try:
        from monitor_ship import ship_event
        ship_event("hub_block",
                   f"gevent hub 被卡住 {blocked_for:.1f} 秒",
                   stack)
    except Exception:
        pass


def start(on_block=None, threshold: float = 5.0, interval: float = 1.0):
    """啟動監控。回傳 stop()（可重複呼叫）。

    on_block(blocked_for: float, stack: str)：偵測到阻塞時呼叫一次。
    threshold：心跳落後超過幾秒才算阻塞。
    interval：心跳與檢查的週期。
    """
    import gevent

    on_block = on_block or _default_on_block
    # live=False 直到 heartbeat 真的跑過一次。worker 開機時 hub 可能還沒開始
    # 排程，心跳自然落後 —— 那是「還沒開始」不是「被卡住」，不能誤報。
    state = {"beat": time.monotonic(), "running": True,
             "reported": False, "live": False}

    def _heartbeat():
        while state["running"]:
            state["beat"] = time.monotonic()
            state["live"] = True
            gevent.sleep(interval)

    sleep = _original_sleep()

    def _check():
        while state["running"]:
            sleep(interval)  # unpatched sleep：真 OS thread 沒有 hub 可讓出
            if not state["running"]:
                break
            if not state["live"]:
                continue  # hub 還沒開始跑，落後不算阻塞
            lag = time.monotonic() - state["beat"]
            if lag > threshold:
                # 一次阻塞只回報一次，避免每個週期都刷一行洗版
                if not state["reported"]:
                    state["reported"] = True
                    try:
                        on_block(lag, _capture_stacks())
                    except Exception:  # 監控自己壞掉不該影響服務
                        traceback.print_exc()
            else:
                state["reported"] = False

    beater = gevent.spawn(_heartbeat)
    _real_thread(_check)

    def stop():
        if state["running"]:
            state["running"] = False
            beater.kill(block=False)

    return stop
