# Weather GPS Fast Load — Spec

**Branch**: `fix/weather-gps-fast-load`
**Date**: 2026-05-25
**Scope**: 純 frontend（`templates/weather/index.html`）+ 無 backend / schema 改動

## 問題

User 開 webapp（weather 頁面為主入口）等很久才看到天氣資料。Worst case timeline：

- `waitForFP`：50-200ms（device fingerprint 計算）
- `getCurrentPosition({ timeout: 8000 })`：**0-8s**（GPS 室內訊號弱會等到 timeout）
- 並行 fetch（weather / aqi / nominatim）：500-2000ms

加總 worst case **~10 秒**全空白等待。Spinner 期間無任何進度資訊，user 體感「卡住」。

## Root Cause

1. `getCurrentPosition` timeout 設 **8 秒太長**，室內 / 弱訊號 / GPS 權限未授予的場景每次都等到 timeout 才 fallback
2. 完全無 cache，每次開啟都要重抓 GPS + 重 fetch（即使員工每天同地點上下班）
3. 無 progressive UI feedback，user 不知道在做什麼

## Design

### Cache：localStorage stale-while-revalidate

```js
localStorage['weather_last_location'] = {
  lat: 24.1477,
  lon: 120.6736,
  weatherData: { ... full OWM weather response ... },
  aqiData: { ... full OWM air_pollution response ... },
  cachedAt: 1748147200000  // unix ms
}
```

**Cache TTL**：7 天。超過 7 天棄用，走原 flow（重抓 GPS + fresh fetch）。

### 開頁面 flow

```
1. waitForFP（同現況、~200ms）
2. 讀 localStorage cache
   ├─ 無 cache 或 > 7 天        → 走原 flow（getCurrentPosition timeout 3s）
   └─ 有 valid cache (< 7 天)
       ├─ 立刻 renderWeatherCard + renderAQI（瞬間有畫面）
       ├─ 加 staleness chip（見下）
       └─ 並行背景任務：
          ├─ getCurrentPosition({ timeout: 3000 })  ← 從 8s 降到 3s
          └─ 用 cache 的 lat/lon fetch 新 weather + aqi
3. 背景任務完成：
   ├─ 新 weather/aqi 回來 → swap render + 移除 staleness chip + opacity 0.3s transition fade-in
   ├─ GPS 抓到新 lat/lon 且 abs(lat - cache.lat) > 0.5 或 abs(lon - cache.lon) > 0.5（≈ 55km @ 24°N）→ 用新位置重 fetch
   ├─ GPS 失敗 → 不影響（已用 cache lat/lon fetch、結果照樣 swap）
   └─ Fetch 失敗 → 不 swap、chip 改成「更新失敗 · 顯示為快取」（紅字、user 知道為何 chip 沒消）
4. 寫回 localStorage（最新 lat/lon + weatherData + aqiData + cachedAt = now）
```

### Staleness chip

| Cache age | 顯示 |
|---|---|
| 0-5 分鐘 | 無 chip（資料夠新、user 無感） |
| 5-60 分鐘 | `5 分鐘前 · 更新中…` |
| 1-24 小時 | `3 小時前 · 更新中…` |
| 1-7 天 | `2 天前 · 更新中…` |

Chip 位置：城市名旁邊小字。Swap 完成後 chip 消失。

### GPS timeout 從 8s 降到 3s

`{ timeout: 8000, maximumAge: 300000 }` → `{ timeout: 3000, maximumAge: 300000 }`

理由：
- 室內 / 弱訊號 / 權限未授予場景，原本要等 8s 才 fallback IP；3s 已足夠判斷
- 有 cache 場景：user 不會感覺等待（已 instant render）
- 無 cache 場景：worst case 從 ~10s 縮到 ~5s（3s GPS + 2s IP fallback + 2s weather）

### Progressive UI text

Spinner 旁加小字（無 cache 場景才顯示）：

- 階段 1：「正在抓取位置…」
- 階段 2（GPS 抓到後）：「正在抓取天氣…」

有 cache 場景：spinner 不顯示（已立刻 render）、只有右上角 chip 標示「更新中…」。

### 不改動的部分

- 「天」字 `#tap-target` click handler 不加視覺提示（99% 情境 ext_ptr loader 在 user 動手前已就緒、加提示反而呈現「卡一下」感）
- `waitForFP` 邏輯不動（FP 計算本身 < 200ms，3s timeout 是 fallback、實務不會用到）
- IP fallback (`fallbackToIP`) 邏輯不動（只是更早被觸發）
- Backend `weather/routes.py` 不動（server cache TTL 仍 5min）

## 邊界情境

| 情境 | 處理 |
|---|---|
| User 跨城市移動（台中 → 台北）| 瞬間顯示「台中 25°C · 2 天前」chip，1-2 秒後 GPS 抓到台北位置 + fetch 新天氣 + swap 成「台北 18°C」 |
| User 完全離線 + 有 cache | render cache、background fetch 失敗 → chip 改成「更新失敗 · 顯示為快取」紅字。可接受：user 至少有上次資料 |
| User 完全離線 + 無 cache | 走原 flow → fetch 失敗 → 顯示「無法載入天氣資料」（同現況） |
| GPS 權限拒絕 + 有 cache | render cache + 背景用 cache lat/lon fetch 新天氣（不重抓 GPS） |
| GPS 抓到的位置跟 cache 差 < 50km | 不重 fetch（weather 視為同一區、省 API call） |
| GPS 抓到的位置跟 cache 差 > 50km | 用新 GPS 位置重 fetch、swap 成新城市 |
| User 連續快速開頁面 | localStorage 同步操作、無 race condition |
| cache 結構解析失敗（手動清過 / 格式變動）| try/catch 包住、解析失敗就當無 cache 處理 |

## Capacity

- localStorage：~2KB／device（weatherData + aqiData JSON）
- 對 prod CPU/RAM/PG：**0 影響**（純 frontend）
- 對 OWM API call 量：**輕微降低**（server side 5min cache 仍有效，frontend 不會減少 server call 次數，但有 cache 場景 user 看到畫面後 background fetch 仍走、總量不變）

## 不做的事（YAGNI）

- 不做 GPS + IP 並行 race（複雜度高、加上 cache 後 user 體感已經夠快）
- 不做 cache 加密（OWM weather data 非敏感）
- 不做跨 tab cache 同步（BroadcastChannel）— user 通常只開一個 tab
- 不做 weather 預測 / 歷史對比 — scope 外
- 不改 backend server cache TTL（5min 對 OWM rate limit 已足夠）

## 測試策略

純 frontend、無 backend test 改動。實機測試（按 [[feedback_local_test_before_push]]）：

1. **首次開**（清 localStorage）：應該走原 flow + 看到 progressive text + 7s 內顯示
2. **5min 內再開**：瞬間 render、無 chip、background swap 看不出變化
3. **1h 後再開**：瞬間 render + chip「1 小時前 · 更新中…」+ 1-2s 後 swap + chip 消失
4. **GPS 拒絕 + 有 cache**：瞬間 render cache、不重抓 GPS
5. **跨城市**（手動改 localStorage lat/lon 模擬）：瞬間顯示舊城市 + chip 過時 + 1-2s 後 swap 成新城市

## 部署

純 frontend 改動、無 schema migration、無 dependency 變動。GHA Docker build 完成後 Zeabur Redeploy 即可生效。User 第一次 deploy 後開啟頁面仍走原 flow（無 cache）、第二次起 cache 命中。
