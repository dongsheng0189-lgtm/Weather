# 限流取證上報 monitor Implementation Plan

## ✅ 狀態：2026-08-18 全部實作完成，通過全分支審查。**尚未 push、尚未部署。**

commits `4c0bac5..1bdcb3c`（9 個），測試 825 passed / 1 skipped / 1 xfailed。
下方 checkbox 未逐一勾選，以本節為準。

**實作過程中偏離本計畫、以實際程式碼為準的三處**：

1. **Task 4 的 bucket key 改用 `request.url_rule.rule`**（計畫原本寫 `request.path`，
   那是個 bug）。`vetting` 有 `/vetting/whitelist/<int:eid>` 這種帶變數段的限流路由，
   用 raw path 會讓每個不同 eid 變成一個新 bucket → 「每個 path 第一次命中立刻送」
   退化成「每個 429 都送一則」，打穿彙總的核心保護，且 `_buckets` 無上限成長。
   上方 Task 4 Step 4 的程式碼已就地更正。
2. **Task 3 多了一個真正驗證非同步送達的測試**，並把原本假通過的
   `test_async_variant_never_raises_without_gevent_hub` 改名為
   `test_async_variant_is_noop_when_not_configured`（它先刪 env，根本沒碰到 spawn）。
3. **新增 `tests/conftest.py` 的 autouse fixture** `_reset_ratelimit_forensics`：
   重置全域彙總器並 stub 掉寄送函式。原因：429 log 測試沒 patch 寄送，
   在有 `.env.local` 的 shell 跑 pytest 會**對線上 monitor 真的送出事件**。
   `tests/test_monitor_ship_from_request.py` 用 marker 退出（它就是要測真寄送）。

**部署後的第一件事**見文末「部署後的驗證」；判讀方式見 spec 的「驗證（prod）」——
**要看 `path=/auth/verify` 的事件，不是「有沒有任何事件」**。

---

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 429 命中時，把「不含個資」的彙總事件推到 monitor，用來確認 `/auth/verify` 的 20/min 是否真的被打爆。

**Architecture:** app-unified 的既有 429 handler 呼叫一個純邏輯彙總器（決定要不要送 + 把 IP 降維成雜湊），再透過 `gevent.spawn` 背景送到 monitor 的 `/ingest`。monitor 端只需把 `rate_limit` 加進 kind 白名單，沿用既有 `Event` 表，不改 schema。

**Tech Stack:** Flask 3、flask-limiter 4.1.1、gevent、既有 `monitor_ship.py`、pytest

**Spec:** `docs/superpowers/specs/2026-08-18-ratelimit-forensics-design.md`

## Global Constraints

- **絕不送真實 IP 進 monitor。** `monitor/mon_events` 沒有欄位加密，其成立前提是「結構上不含個資」（`monitor/models.py:7`）。送出的 payload 只能有雜湊與層數。
- **既有 `monitor_ship.ship_event()` 一行都不能改。** 它的 unpatched-socket 規則由 `app_unified/tests/test_monitor_ship.py` 守著，且是 hub 卡死時唯一的上報路徑。新功能一律走新函式。
- **請求路徑上絕不可使用 unpatched socket。** 從 greenlet 呼叫未被 patch 的 `connect`/`recv` 會阻塞整個 gevent hub 最多 3 秒——正是本次調查在追的故障。
- **監控失敗絕不影響服務。** 所有寄送路徑吞掉例外、回 `False`。
- **monitor 與 `app_unified` 是 0 cross-import。** monitor 的 Docker build context 只有 `monitor/`，必須自給自足。
- 本地測試指令一律從 repo 根目錄起算：app-unified 用 `cd app_unified && ../venv/bin/python -m pytest`，monitor 用 `cd monitor && python -m pytest`。

## File Structure

| 檔案 | 責任 |
|---|---|
| `monitor/app.py`（改）| kind 白名單加入 `rate_limit` |
| `monitor/models.py`（改）| 更新模組說明：白名單不再只有 hub_block，但仍不含個資 |
| `monitor/tests/test_ingest.py`（改）| 新增 `rate_limit` 被接受的測試 |
| `app_unified/ratelimit_forensics.py`（新）| **純邏輯**：彙總 429、雜湊來源、決定要不要送、產生 payload。不做 I/O |
| `app_unified/tests/test_ratelimit_forensics.py`（新）| 上者的測試（假時鐘，不碰網路） |
| `app_unified/monitor_ship.py`（改）| 新增 `ship_event_from_request()` 與 `ship_event_from_request_async()` |
| `app_unified/tests/_monitor_capture.py`（新）| 從 `test_monitor_ship.py` 抽出的假 monitor server，兩個測試檔共用 |
| `app_unified/tests/test_monitor_ship.py`（改）| 改成 import 上者，其餘不動 |
| `app_unified/tests/test_monitor_ship_from_request.py`（新）| 新寄送函式的測試 |
| `app_unified/app.py`（改）| 429 handler 接線（`record` → `spawn` 寄送）|
| `app_unified/tests/test_ratelimit_client_ip_logging.py`（改）| 補接線測試 |
| `CLAUDE.md`（改）| 更新 `/ingest` 白名單敘述、補上兩個寄送函式限制相反的規則 |

---

### Task 1: monitor 端接受 `rate_limit` kind

**Files:**
- Modify: `monitor/app.py:15`
- Modify: `monitor/models.py:5-9`
- Modify: `CLAUDE.md:176`
- Test: `monitor/tests/test_ingest.py`

**Interfaces:**
- Consumes: 無（本 task 獨立）
- Produces: monitor `/ingest` 接受 `{"kind": "rate_limit", "summary": str, "detail": str}` 並寫入 `mon_events`

- [ ] **Step 1: 寫失敗測試**

在 `monitor/tests/test_ingest.py` 末尾加：

```python
def test_accepts_rate_limit_and_persists(client, app):
    """限流取證事件（2026-08-18）。只帶雜湊與層數，不含 IP。"""
    r = _post(client, {"kind": "rate_limit", "severity": "warning",
                       "summary": "rate limited: /auth/verify ×17, 3 clients, pid=42",
                       "detail": "window: 63.2s since last report\n"
                                 "clients: a3f1c2=14, 7b09de=2, 1e4405=1\n"
                                 "xff_hops: 2"})
    assert r.status_code == 201
    with app.app_context():
        from models import Event
        e = Event.query.one()
        assert e.kind == "rate_limit"
        assert "a3f1c2=14" in e.detail
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd monitor && python -m pytest tests/test_ingest.py::test_accepts_rate_limit_and_persists -v`
Expected: FAIL — `assert 400 == 201`（kind 不在白名單）

- [ ] **Step 3: 加進白名單**

`monitor/app.py:14-15` 改成：

```python
# 白名單：只收診斷事件。擴大範圍前必須先補欄位加密（見 models.py 說明）。
# rate_limit（2026-08-18）：429 命中的彙總。送的是來源雜湊與 XFF 層數，不含 IP。
ALLOWED_KINDS = {"hub_block", "rate_limit"}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd monitor && python -m pytest tests/test_ingest.py -v`
Expected: 全數 PASS（含既有的 `test_unknown_kind_rejected`——白名單機制本身沒被拆掉）

- [ ] **Step 5: 更新兩處文件敘述**

`monitor/models.py:5-9` 的模組 docstring 改成：

```python
不做欄位加密：本服務只收白名單內的診斷事件（hub_block 的阻塞秒數 + 程式碼堆疊、
rate_limit 的命中次數 + 來源雜湊），結構上不含個資。日後若要擴大收一般 app log，
**必須先補加密**。新增 kind 前先確認它送的東西同樣不含個資。
```

`CLAUDE.md:176` 那行改成：

```markdown
**`/ingest` 只收白名單 kind**（`hub_block`、`rate_limit`），送的資料結構上不含個資
（後者只帶來源雜湊與 XFF 層數，不帶 IP），因此不做欄位加密。日後要擴大收一般 app log，
**必須先補 `EncryptedText`**。
```

- [ ] **Step 6: 跑 monitor 全套測試**

Run: `cd monitor && python -m pytest -q`
Expected: 全數 PASS

- [ ] **Step 7: Commit**

```bash
git add monitor/app.py monitor/models.py monitor/tests/test_ingest.py CLAUDE.md
git commit -m "feat(monitor): /ingest 白名單加入 rate_limit kind"
```

---

### Task 2: 彙總核心 `ratelimit_forensics.py`

**Files:**
- Create: `app_unified/ratelimit_forensics.py`
- Test: `app_unified/tests/test_ratelimit_forensics.py`

**Interfaces:**
- Consumes: 無
- Produces:
  - `HitAggregator(salt: str = "", window_seconds: float = 60.0)`
  - `HitAggregator.record(path: str, remote_addr: str | None, xff: str | None, now: float) -> dict | None`
    回傳 `{"kind": "rate_limit", "summary": str, "detail": str}` 或 `None`（同一視窗內）
  - `default_aggregator() -> HitAggregator`（module singleton，salt 取自 `SECRET_KEY`）

- [ ] **Step 1: 寫失敗測試**

建立 `app_unified/tests/test_ratelimit_forensics.py`：

```python
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
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_ratelimit_forensics.py -q`
Expected: 收集階段就 FAIL — `ModuleNotFoundError: No module named 'ratelimit_forensics'`

- [ ] **Step 3: 寫實作**

建立 `app_unified/ratelimit_forensics.py`：

```python
"""429 取證：把限流命中彙總成可送往 monitor 的事件。

本模組**不做任何 I/O**——只負責「決定要不要送」與「把資料降維成不含個資的形狀」，
寄送由呼叫端（app.py 的 429 handler）負責。這樣核心邏輯可以完全用假時鐘測。

為什麼不送真實 IP：monitor 的 mon_events 沒有欄位加密，其前提是「結構上不含個資」
（見 monitor/models.py）。我們要回答的問題只有「是不是同一個來源」與「XFF 有幾層」，
雜湊與計數就夠，不必破壞那個前提。

為什麼第一次命中就立刻送：若等滿視窗才送，20:00 爆一批、之後整晚沒有 429 的話，
那批會永遠卡在緩衝區——而那正是我們唯一想看到的事件。詳見
docs/superpowers/specs/2026-08-18-ratelimit-forensics-design.md。
"""
import hashlib
import os
from collections import Counter

WINDOW_SECONDS = 60.0


def _hop_count(xff):
    """X-Forwarded-For 的層數。日後 ProxyFix 的 x_for 就照這個數字設。"""
    if not xff:
        return 0
    return len([p for p in xff.split(",") if p.strip()])


class HitAggregator:
    """每個 path 一組計數；每個 path 每個視窗最多回報一次。

    gevent 是協作式排程，下面的讀改寫之間不會被搶佔，因此不需要鎖。
    """

    def __init__(self, salt: str = "", window_seconds: float = WINDOW_SECONDS):
        self._salt = salt
        self._window = window_seconds
        self._buckets = {}

    def _client_hash(self, remote_addr):
        raw = "{}|{}".format(self._salt, remote_addr or "(none)")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]

    def record(self, path, remote_addr, xff, now):
        """記一次 429。回傳要送出的 payload，或 None（同視窗內只累計）。"""
        bucket = self._buckets.get(path)
        if bucket is None:
            bucket = {"count": 0, "clients": Counter(),
                      "hops": set(), "last_ship": None}
            self._buckets[path] = bucket

        bucket["count"] += 1
        bucket["clients"][self._client_hash(remote_addr)] += 1
        bucket["hops"].add(_hop_count(xff))

        last_ship = bucket["last_ship"]
        if last_ship is not None and (now - last_ship) < self._window:
            return None

        payload = _format(path, bucket, now, last_ship)
        bucket["count"] = 0
        bucket["clients"] = Counter()
        bucket["hops"] = set()
        bucket["last_ship"] = now
        return payload


def _format(path, bucket, now, last_ship):
    clients = bucket["clients"]
    n = len(clients)
    summary = "rate limited: {} ×{}, {} client{}, pid={}".format(
        path, bucket["count"], n, "s" if n != 1 else "", os.getpid())
    if last_ship is None:
        window_line = "window: first report"
    else:
        window_line = "window: {:.1f}s since last report".format(now - last_ship)
    clients_line = "clients: " + ", ".join(
        "{}={}".format(h, c) for h, c in clients.most_common())
    hops_line = "xff_hops: " + ",".join(str(h) for h in sorted(bucket["hops"]))
    return {"kind": "rate_limit",
            "summary": summary[:500],
            "detail": "\n".join([window_line, clients_line, hops_line])}


_default = None


def default_aggregator():
    """全 process 共用一個。salt 用 SECRET_KEY：固定值 → 雜湊跨重啟、跨 worker 可比對。"""
    global _default
    if _default is None:
        _default = HitAggregator(salt=os.environ.get("SECRET_KEY", ""))
    return _default
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_ratelimit_forensics.py -q`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add app_unified/ratelimit_forensics.py app_unified/tests/test_ratelimit_forensics.py
git commit -m "feat(auth): 429 彙總器（雜湊來源、不含個資、第一次命中即回報）"
```

---

### Task 3: 請求路徑專用的寄送函式

**Files:**
- Create: `app_unified/tests/_monitor_capture.py`
- Modify: `app_unified/tests/test_monitor_ship.py`（只改 import，其餘不動）
- Modify: `app_unified/monitor_ship.py`（**只新增**，既有函式一行不改）
- Create: `app_unified/tests/test_monitor_ship_from_request.py`
- Modify: `CLAUDE.md`（hub_watchdog 段落補一條規則）

**Interfaces:**
- Consumes: 無
- Produces:
  - `monitor_ship.ship_event_from_request(kind: str, summary: str, detail: str = "") -> bool`
  - `monitor_ship.ship_event_from_request_async(kind: str, summary: str, detail: str = "") -> None`

- [ ] **Step 1: 把假 monitor server 抽成共用 helper**

建立 `app_unified/tests/_monitor_capture.py`，內容從 `tests/test_monitor_ship.py` 的 `_capture_server()` **原封不動**搬過來（含它用到的 `import socket` / `import threading`），並加上模組 docstring：

```python
"""測試用的假 monitor server：收一個 HTTP 請求就停，回傳 (port, received)。

兩個測試檔共用（test_monitor_ship.py 與 test_monitor_ship_from_request.py），
命名比照 tests/_r2_fixture.py 的慣例：底線開頭 = 共用 helper，不是測試檔。
"""
```

搬完後在 `tests/test_monitor_ship.py` 頂端加：

```python
from tests._monitor_capture import _capture_server
```

並刪掉該檔內原本的 `_capture_server` 定義。

- [ ] **Step 2: 跑既有測試確認搬移沒弄壞東西**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_monitor_ship.py -q`
Expected: 全數 PASS（純搬移，行為不變）

- [ ] **Step 3: Commit 這次純搬移**

```bash
git add app_unified/tests/_monitor_capture.py app_unified/tests/test_monitor_ship.py
git commit -m "refactor(tests): 假 monitor server 抽成 tests/_monitor_capture.py 供共用"
```

- [ ] **Step 4: 寫失敗測試**

建立 `app_unified/tests/test_monitor_ship_from_request.py`：

```python
"""請求路徑上的寄送：與 ship_event 的限制正好相反。

ship_event 由 hub_watchdog 的真 OS thread 呼叫，必須用 unpatched socket。
這個函式由 greenlet 呼叫，**必須用 patched socket** —— 未被 patch 的
connect/recv 會阻塞整個 gevent hub 最多 3 秒，正是我們在追的那種故障。
為了量測阻塞而製造阻塞，不可接受。

兩個函式的限制相反是這段程式碼最容易被後人「順手統一」而踩爆的地方。
"""
import json

import monitor_ship
from tests._monitor_capture import _capture_server


def test_posts_event_to_ingest(monkeypatch):
    port, received = _capture_server()
    monkeypatch.setenv("MONITOR_INGEST_URL", f"http://127.0.0.1:{port}/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "tok")

    ok = monitor_ship.ship_event_from_request(
        "rate_limit", "rate limited: /auth/verify ×3, 1 client, pid=1", "xff_hops: 2")

    assert ok is True
    head, body = received[0]
    assert "Bearer tok" in head
    payload = json.loads(body)
    assert payload["kind"] == "rate_limit"
    assert payload["source"] == "app-unified"
    assert payload["detail"] == "xff_hops: 2"


def test_returns_false_when_not_configured(monkeypatch):
    monkeypatch.delenv("MONITOR_INGEST_URL", raising=False)
    monkeypatch.delenv("MONITOR_INGEST_TOKEN", raising=False)
    assert monitor_ship.ship_event_from_request("rate_limit", "x") is False


def test_never_raises_when_nothing_is_listening(monkeypatch):
    """監控失敗絕不影響服務 —— 這裡跑在請求路徑上，拋例外會變成使用者的 500。"""
    monkeypatch.setenv("MONITOR_INGEST_URL", "http://127.0.0.1:1/ingest")
    monkeypatch.setenv("MONITOR_INGEST_TOKEN", "tok")
    assert monitor_ship.ship_event_from_request("rate_limit", "x") is False


def test_async_variant_never_raises_without_gevent_hub(monkeypatch):
    monkeypatch.delenv("MONITOR_INGEST_URL", raising=False)
    monkeypatch.delenv("MONITOR_INGEST_TOKEN", raising=False)
    monitor_ship.ship_event_from_request_async("rate_limit", "x")   # 不應拋例外


def test_ship_event_still_uses_unpatched_socket():
    """回歸防護：既有 ship_event 的死規則不能因為新增函式而被順手改掉。"""
    import inspect
    src = inspect.getsource(monitor_ship.ship_event)
    assert "_socket_factory" in src
    assert "_getaddrinfo" in src
```

- [ ] **Step 5: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_monitor_ship_from_request.py -q`
Expected: FAIL — `AttributeError: module 'monitor_ship' has no attribute 'ship_event_from_request'`

- [ ] **Step 6: 寫實作**

在 `app_unified/monitor_ship.py` **檔案末尾追加**（既有內容一行不動）：

```python
# ---------------------------------------------------------------------------
# 以下是請求路徑專用的寄送，限制與上面的 ship_event 正好相反 —— 別把兩者合併。
# ---------------------------------------------------------------------------

def ship_event_from_request(kind: str, summary: str, detail: str = "") -> bool:
    """從**請求路徑上的 greenlet** 送出事件。成功 True，任何失敗 False。

    與 ship_event() 的差別，改動前務必讀懂：

      ship_event()               本函式
      ─────────────────────      ─────────────────────
      呼叫者：真 OS thread        呼叫者：greenlet
      必須 unpatched socket      必須 **patched** socket
      （跨執行緒用 gevent 物件    （未 patch 的 connect/recv 會阻塞整個
        會直接爆掉）               hub 最多 3 秒，等於自己製造故障）

    所以這裡刻意用 urllib（已被 monkey patch → 會正常讓出 hub），
    而**不是**重用上面那套 _socket_factory / _getaddrinfo。
    """
    import urllib.error
    import urllib.request

    url = os.environ.get("MONITOR_INGEST_URL", "").strip()
    token = os.environ.get("MONITOR_INGEST_TOKEN", "").strip()
    if not url or not token:
        return False

    try:
        body = json.dumps({"kind": kind, "severity": "warning",
                           "source": "app-unified",
                           "summary": summary, "detail": detail},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status in (200, 201)
    except Exception:
        return False        # 監控失敗絕不影響服務


def ship_event_from_request_async(kind: str, summary: str, detail: str = "") -> None:
    """背景送出，不讓使用者的請求等待網路往返。

    沒有 gevent（單元測試環境）時退回同步呼叫；該情境下未設定 env 會立刻回
    False，不會真的碰網路。
    """
    try:
        import gevent
        gevent.spawn(ship_event_from_request, kind, summary, detail)
    except Exception:
        try:
            ship_event_from_request(kind, summary, detail)
        except Exception:
            pass
```

- [ ] **Step 7: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_monitor_ship_from_request.py tests/test_monitor_ship.py -q`
Expected: 全數 PASS

- [ ] **Step 8: 在 CLAUDE.md 補上這條規則**

在 CLAUDE.md 的「app-unified 端的配套」段落，三條規則之後加一段：

```markdown
> ⚠ **`monitor_ship` 有兩個寄送函式，限制正好相反，不可合併：**
> `ship_event()` 由 hub_watchdog 的真 OS thread 呼叫，**必須 unpatched socket**；
> `ship_event_from_request()` 由請求路徑上的 greenlet 呼叫，**必須 patched socket**
> ——未被 patch 的 connect/recv 從 greenlet 呼叫會阻塞整個 hub 最多 3 秒。
> 兩者的 docstring 互相指名對方，有測試守著。
```

- [ ] **Step 9: Commit**

```bash
git add app_unified/monitor_ship.py app_unified/tests/test_monitor_ship_from_request.py CLAUDE.md
git commit -m "feat(monitor): 請求路徑專用寄送函式（patched socket + 背景送）"
```

---

### Task 4: 接線 429 handler

**Files:**
- Modify: `app_unified/app.py:136-147`（既有 `_handle_rate_limited`）
- Modify: `app_unified/tests/test_ratelimit_client_ip_logging.py`

**Interfaces:**
- Consumes: `ratelimit_forensics.default_aggregator()`、`monitor_ship.ship_event_from_request_async()`
- Produces: 無（終端接線）

- [ ] **Step 1: 寫失敗測試**

在 `app_unified/tests/test_ratelimit_client_ip_logging.py` 末尾加：

```python
def test_429_ships_aggregated_event_to_monitor(app, monkeypatch):
    """第一次命中就要送出，且送出的東西不含 IP。"""
    import app as app_module

    sent = []
    monkeypatch.setattr(app_module, "ship_event_from_request_async",
                        lambda kind, summary, detail="": sent.append(
                            (kind, summary, detail)))
    # 用全新的彙總器，避免被同一個 process 內其他測試的計數污染
    import ratelimit_forensics
    monkeypatch.setattr(ratelimit_forensics, "_default",
                        ratelimit_forensics.HitAggregator(salt="t"))

    with app.test_request_context(
        '/auth/verify', method='POST',
        headers={'X-Forwarded-For': '203.0.113.7'},
        environ_base={'REMOTE_ADDR': '10.0.0.5'},
    ):
        app.handle_http_exception(TooManyRequests())

    assert len(sent) == 1, '429 沒有推給 monitor'
    kind, summary, detail = sent[0]
    assert kind == 'rate_limit'
    assert '/auth/verify' in summary
    assert '203.0.113.7' not in summary + detail, '真實 IP 不可送進 monitor'
    assert '10.0.0.5' not in summary + detail, '真實 IP 不可送進 monitor'


def test_429_within_window_ships_only_once(app, monkeypatch):
    import app as app_module

    sent = []
    monkeypatch.setattr(app_module, "ship_event_from_request_async",
                        lambda kind, summary, detail="": sent.append(summary))
    import ratelimit_forensics
    monkeypatch.setattr(ratelimit_forensics, "_default",
                        ratelimit_forensics.HitAggregator(salt="t"))

    for _ in range(5):
        with app.test_request_context(
            '/auth/verify', method='POST',
            environ_base={'REMOTE_ADDR': '10.0.0.5'},
        ):
            app.handle_http_exception(TooManyRequests())

    assert len(sent) == 1, '同一視窗內應只送一則，避免尖峰時火上加油'


def test_429_shipping_failure_does_not_break_the_response(app, monkeypatch):
    """監控壞掉不能變成使用者的 500。"""
    import app as app_module

    def boom(*a, **kw):
        raise RuntimeError("monitor down")

    monkeypatch.setattr(app_module, "ship_event_from_request_async", boom)
    import ratelimit_forensics
    monkeypatch.setattr(ratelimit_forensics, "_default",
                        ratelimit_forensics.HitAggregator(salt="t"))

    with app.test_request_context(
        '/auth/verify', method='POST',
        headers={'Accept': 'application/json'},
        environ_base={'REMOTE_ADDR': '10.0.0.5'},
    ):
        resp = app.handle_http_exception(TooManyRequests())

    body, status = resp
    assert status == 429
    assert body.get_json()['error'] == 'rate_limited'
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_ratelimit_client_ip_logging.py -q`
Expected: 新增的三個 FAIL — `AttributeError: <module 'app'> has no attribute 'ship_event_from_request_async'`

- [ ] **Step 3: 在 app.py 頂端加 import**

找到 `app_unified/app.py` 既有的 import 區塊，加入：

```python
import time

from monitor_ship import ship_event_from_request_async
from ratelimit_forensics import default_aggregator
```

（若 `time` 已 import 則不重複。）

- [ ] **Step 4: 改 429 handler**

`app_unified/app.py` 的 `_handle_rate_limited`，在既有那行 log **之後**、
`accept = request.headers.get("Accept", "")` **之前**插入：

```python
        # 彙總後推給 monitor：/tmp/app.log 隨容器重啟清空，尖峰那批證據留不住。
        # 送的是來源雜湊與 XFF 層數，不含 IP —— monitor 沒有欄位加密。
        # bucket key 用路由樣板而非 request.path：像 /vetting/whitelist/<int:eid>
        # 這種帶變數段的限流路由，每個不同 eid 會變成一個新 bucket，
        # 「每個 path 第一次命中立刻送」就會變成每個 429 都送一則。
        try:
            payload = default_aggregator().record(
                request.url_rule.rule if request.url_rule else request.path,
                request.remote_addr,
                request.headers.get("X-Forwarded-For"),
                time.monotonic(),
            )
            if payload:
                ship_event_from_request_async(
                    payload["kind"], payload["summary"], payload["detail"])
        except Exception:
            logging.getLogger("ratelimit").exception("ship rate_limit event failed")
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd app_unified && ../venv/bin/python -m pytest tests/test_ratelimit_client_ip_logging.py -q`
Expected: 6 passed

- [ ] **Step 6: 跑 app-unified 全套測試**

Run: `cd app_unified && ../venv/bin/python -m pytest -q`
Expected: 823 passed, 1 skipped, 1 xfailed
（基準：本計畫撰寫時為 800 passed；本計畫在 app_unified 新增 23 個測試 —— Task 2 的 15、
Task 3 的 5、Task 4 的 3。Task 1 的那個在 monitor 那套，不計入這裡。）

- [ ] **Step 7: Commit**

```bash
git add app_unified/app.py app_unified/tests/test_ratelimit_client_ip_logging.py
git commit -m "feat(auth): 429 命中彙總後推給 monitor"
```

---

## 部署後的驗證（人工，非自動測試）

1. **先確認 ingest 鏈路還通**（monitor 若被刪除重建，服務 ID 會變、寄送靜默失效）。
   進 app-unified 的 Zeabur 指令 shell：

   ```
   python3 -c "import sys;sys.path.insert(0,'/app');import monitor_ship;print(monitor_ship.ship_event('hub_block','驗證','(test)'))"
   ```

   回 `True` 才代表 monitor 上「沒看到事件」等於「真的沒爆」。

2. 20:00 前後開 monitor 檢視頁，照 spec 的判讀表看 `rate_limit` 事件。

---

## Self-Review

- **Spec coverage**：spec 的「送什麼」→ Task 2；「怎麼送」→ Task 3；「彙總」→ Task 2；
  「保留 /tmp/app.log 兩行」→ 已於 commit `081768d` 完成，本計畫不動它；
  「錯誤處理」→ Task 3 Step 4 與 Task 4 Step 1 的失敗測試；
  「測試」六項全數對應；「驗證（prod）」→ 上方部署後驗證段落。
- **Placeholder scan**：無 TBD / TODO；每個改動步驟都附完整程式碼。
- **Type consistency**：`record()` 回傳的 dict 三個 key（`kind`/`summary`/`detail`）在
  Task 2 定義、Task 4 消費，名稱一致；`ship_event_from_request_async(kind, summary, detail)`
  參數順序在 Task 3 定義、Task 4 呼叫，一致。
