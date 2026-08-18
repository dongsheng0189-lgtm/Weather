# 限流取證：確認 `/auth/verify` 的 20/min 是否真的被打爆

**日期**：2026-08-18
**模組**：`app_unified/`（429 handler + 寄送函式）、`monitor/`（新增一個 kind）
**Repo**：`webapp`
**相關**：`2026-05-08-monitor-service-design.md`（monitor 本體）、
`2026-08-14-minimal-monitor-service.md`（實際落地的縮減版）

## 動機

2026-08-17 在程式面證實了 `/auth/verify` 的限流有兩個缺陷（詳見 memory
`ratelimit-shared-across-users`），本次已再對現行程式碼複驗：

1. **額度全體共用**：`extensions.py:11` 用 `key_func=get_remote_address`，
   app 跑在 Zeabur ingress 後面，而全 repo grep 不到任何 `ProxyFix`（零命中）
   → `request.remote_addr` 恆為 ingress IP，所有人共用同一個計數桶。
2. **Redis storage 從未生效**：`config.py:58` 設的是 `RATELIMIT_STORAGE_URL`，
   但 flask-limiter 4.1.1 只認 `RATELIMIT_STORAGE_URI`
   （`flask_limiter/constants.py:25`，已在 venv 內確認）；且 `extensions.py:14`
   建構子已寫死 `storage_uri="memory://"`，建構子參數優先。
   → 計數是 per-worker 記憶體、2 個 worker 各一份、重啟歸零。

兩個缺陷**方向相反**：(1) 收緊（共用一桶），(2) 放寬（2 桶 + 重啟歸零）。
實際行為是「全體共用約 40/min、隨機分流」。這也是為什麼修的時候三步必須一起上——
只修 (2) 會讓額度從 40/min 掉回 20/min 並失去重啟這個意外的洩壓閥。

2026-08-17 Zeabur 回覆確認 ingress（Caddy）**沒有** per-IP 限流，把「尖峰登入失敗」
的嫌疑推回我們自己的 app。但目前的證據只到「這個限流是共用的」，**尚未證實使用者
真的撞到它**。

**本設計不修限流，只取證。** 先確認會不會爆，再決定要不要動。

## 要回答的問題

| 問題 | 判讀依據 |
|---|---|
| 限流到底有沒有被觸發過？ | monitor 上有沒有出現 `rate_limit` 事件。**這是主要問題。** |
| 額度是不是全體共用？ | 事件裡的 `distinct clients` 是否恆為 1 |
| 日後修 ProxyFix 時 `x_for` 要設幾？ | 事件裡的 `xff_hops` |

## 範圍

- `app_unified/app.py`：既有 429 handler 補 log + 彙總計數 + 觸發寄送
- `app_unified/auth/routes.py`：既有 `verify: called` log 補 `remote_addr` / `xff`
- `app_unified/monitor_ship.py`：新增 `ship_event_from_request()`
- `monitor/app.py`：`ALLOWED_KINDS` 加入 `rate_limit`
- 測試：守住上述 log 與寄送行為

## 不在範圍

- **不修限流本身**（ProxyFix、換 key、storage URI）——那是拿到數據後的第二趟
- **不改 monitor 的 schema**：沿用既有 `Event` 的 `summary` / `detail`
- **不加欄位加密**：見下節，送出的資料結構上不含個資，維持 monitor 現有前提
- **不定期送 verify 取樣**：429 若從未出現，假設即被推翻，`x_for` 也就不需要；
  真要查，`/tmp/app.log` 的 `verify: called` 那行 grep 得到
- **不做主動 alert**：沿用 monitor 既有取捨，要看時自己開 dashboard

## 設計

### 1. 送什麼：雜湊 + 層數，不送 IP

`monitor/models.py` 的模組說明立了一條規矩：

> 不做欄位加密：本服務只收 hub_block 這類診斷事件……結構上不含個資。
> 日後若要擴大收一般 app log，**必須先補加密**。

`X-Forwarded-For` 帶使用者端真實 IP，直接送進去會破壞這個前提。但本案**不需要
知道是誰**——只需要知道「是不是同一個來源」與「有幾層」。因此送出前先降維：

```
kind:    rate_limit
summary: rate limited: /auth/verify ×17 in 60s, 3 distinct clients (pid=42)
detail:  clients: a3f1c2=14, 7b09de=2, 1e4405=1
         xff_hops: 2
```

- `a3f1c2` = `remote_addr` 加 salt 後的短雜湊（取 6 hex）。相同來源→相同雜湊，
  但無法回推 IP。salt 用 `SECRET_KEY`：它是固定的環境變數，所以雜湊值**跨重啟、
  跨 worker 都穩定**，可以比對「20:00 那批和 12:00 那批是不是同一個來源」、
  以及「兩個 worker 看到的是不是同一個 ingress IP」。
  （IPv4 空間小、可暴力枚舉，安全性靠 salt 保密；`SECRET_KEY` 若輪替，雜湊值會變，
  屆時新舊事件不可比對。）
- `xff_hops` = `X-Forwarded-For` 的逗號分隔層數；沒有該標頭時為 `0`。
- `pid` 用來分辨兩個 worker 各自的彙總。

**取捨（已與使用者確認）**：事後想知道「到底是哪個 IP」時拿不回來。本案不需要。

### 2. 怎麼送：與 `ship_event` 限制相反的新函式

`monitor_ship.ship_event()` 刻意使用 **unpatched socket**，因為它的呼叫者是
`hub_watchdog` 的真 OS thread。**不能從請求路徑重用它**：未被 patch 的
`connect`/`recv` 從 greenlet 呼叫會阻塞整個 gevent hub 最多 `_TIMEOUT`（3 秒）——
正是本次調查在追的那種故障。為了量測阻塞而製造阻塞，不可接受。

新增 `ship_event_from_request(kind, summary, detail)`，限制正好相反：

| | `ship_event`（既有，不動） | `ship_event_from_request`（新） |
|---|---|---|
| 呼叫者 | hub_watchdog 真 OS thread | 請求路徑上的 greenlet |
| socket | **必須** unpatched | **必須** patched（否則阻塞 hub） |
| 時機 | 當場同步送（hub 已卡死，等不及） | `gevent.spawn` 背景送，不拖慢回應 |
| 失敗 | 回 False，絕不拋例外 | 同左 |

既有 `ship_event` **一行不動**，CLAUDE.md 記載的那三條 gevent 規則與其測試不受影響。
兩個函式的 docstring 都要互相指名對方，說明為何限制相反——這是最容易被後人「順手
統一」而踩爆的地方。

### 3. 彙總：每 worker 每分鐘最多一則，但第一則立刻送

429 爆發時正是系統最吃緊的時刻，一個 429 一個對外請求等於火上加油。
在 app-unified 端以 module-level 計數器彙總，每 worker 每 60 秒最多送一則；
尖峰也就 2 則/分（2 workers）。

演算法（每次 429 進來時）：

1. 計數器 +1（累計次數、各 client 雜湊次數、xff_hops）
2. 若距上次寄送已超過 60 秒（或從未寄送）→ 立刻拍快照、`gevent.spawn` 送出、
   歸零計數器、記下寄送時間

**為什麼是「先送再等」而不是「等滿一分鐘再送」**：後者有個致命的尾端遺失——
20:00 爆了一批、之後整晚再也沒有 429，那批就永遠卡在緩衝區裡送不出去，
而那正是我們唯一想看到的事件。改成「視窗內第一個 429 立即送」，
**「到底有沒有爆」這個主要問題的答案永遠不會遺失**；尾端最多只損失
最後一個視窗的次數細節（worker 重啟也一樣），那只是精度，不是結論。

gevent 是協作式排程，計數器的讀改寫之間不會被搶佔，不需要鎖；只有寄送那一刻
會讓出 hub，而寄送用的是快照、不是活的計數器。

### 4. 保留 `/tmp/app.log` 那兩行

monitor 給結論（有沒有爆、爆多兇），本地 log 給細節（單筆請求的原始 `remote_addr`
與完整 `xff`）。兩者互補，且本地 log 在 monitor 自己掛掉時仍是兜底。
沿用 monitor spec 既有的 defense-in-depth 取捨。

## 錯誤處理

- 寄送失敗一律吞掉、回 `False`：監控絕不影響服務（沿用 `ship_event` 既有原則）。
- ⚠ **這代表 ingest 失效是靜默的**。CLAUDE.md 已記載 monitor service 若被刪除重建，
  Zeabur 服務 ID 會變、`MONITOR_INGEST_URL` 隨之失效而無任何告警。
  本功能繼承同一個弱點：**monitor 上看不到事件，可能是「沒爆」，也可能是「沒送到」。**
  判讀前先用 CLAUDE.md 記的那行 `monitor_ship.ship_event(...)` 指令確認鏈路還通。
- 未設 `MONITOR_INGEST_URL` / `MONITOR_INGEST_TOKEN`（本地開發）→ 直接不送，不報錯。

## 測試

- `/auth/verify` 的 log 含 `remote_addr` 與原始 `X-Forwarded-For`；無 XFF 時印 `(none)`
- 429 handler 留下含 path / remote_addr / xff 的 log
- 第一個 429 立即觸發寄送（不必等滿視窗）
- 同一視窗內的後續 429 只累計、不再寄送
- 視窗過後的下一個 429 再送一則，且次數是累計值而非 1
- 送出的 summary / detail **不含**原始 IP（正向斷言：拿 `203.0.113.7` 進去，
  斷言它不出現在 payload 裡）
- `ship_event_from_request` 失敗時回 `False` 且不拋例外
- monitor `/ingest` 接受 `kind="rate_limit"`、仍拒絕未知 kind

## 驗證（prod）

部署後於 20:00 前後觀察 monitor 檢視頁：

| 看到什麼 | 結論 |
|---|---|
| 完全沒有 `rate_limit` 事件 | 限流從未被打爆 → 這條線索死亡，尖峰連不上另有原因 |
| 有事件，`distinct clients` 恆為 1 | 證實額度全體共用 → 進行第二趟修法 |
| 有事件，`distinct clients` > 1 | ingress 有傳真實 IP，共用推論有誤，需重新評估 |
| 有事件，看 `xff_hops` | 決定第二趟 `ProxyFix(x_for=?)` |

## 第二趟（本次不做，拿到數據後再議）

1. 裝 `ProxyFix`，`x_for` 照實測的 `xff_hops` 設
2. `/auth/verify` 換不共用的 key（登入前無 `current_user`，需另尋 key）
3. `extensions.py` 拿掉寫死的 `storage_uri="memory://"`，改吃 `RATELIMIT_STORAGE_URI`，
   並同步修正 CLAUDE.md 與 Zeabur env 的 key 名

三步必須同時上線，理由見「動機」末段。
