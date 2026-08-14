# Weather GPS Fast Load Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 weather 頁面加 localStorage stale-while-revalidate cache、開頁面瞬間 render 上次資料、背景 fetch 新資料 swap，並把 GPS timeout 從 8s 降到 3s。

**Architecture:** 純 frontend 修改（`templates/weather/index.html` 單檔），無 backend / schema / dependency 變動。Backend `weather/routes.py` 跟 server WeatherCache 不動（仍 5min TTL）。

**Tech Stack:** Vanilla JS（webapp frontend 不用 framework）、localStorage、CSS、pytest（markup smoke test）。

**Spec:** `docs/superpowers/specs/2026-05-25-weather-gps-fast-load.md`

**Branch:** `fix/weather-gps-fast-load`（已開）

**Testing 注意**：webapp 無 frontend test framework（無 jest/playwright），functional 驗證走 [[feedback_local_test_before_push]] 流程（`bash scripts/run_local.sh` → `/dev`）。Pytest 只能寫 markup smoke test（驗 HTML 渲染含預期 markers），無法驗 JS runtime 行為。

---

## Task 1: Cache helper functions + Staleness chip CSS

**Files:**
- Modify: `templates/weather/index.html`（加 CSS class + JS helpers in IIFE）

- [ ] **Step 1: 加 `.staleness-chip` CSS**

在 `<style>` 區（line 130 附近、`.aqi-pm25` rule 後）加：

```css
.staleness-chip { display: inline-block; font-size: 0.75rem; opacity: 0.6;
                  margin-left: 0.5rem; padding: 0.1rem 0.4rem;
                  border-radius: 0.5rem; background: rgba(255,255,255,0.1);
                  transition: opacity 0.3s; }
.staleness-chip.error { color: #ff6b6b; opacity: 0.9; background: rgba(255,107,107,0.15); }
.weather-hero { transition: opacity 0.3s; }
.weather-hero.refreshing { opacity: 0.95; }
```

- [ ] **Step 2: 加 cache helper functions 到 IIFE 內部（在 `_applyWeatherBg` 之前）**

```javascript
var CACHE_KEY = 'weather_last_location';
var CACHE_MAX_AGE_MS = 7 * 24 * 3600 * 1000;  // 7 天
var DISTANCE_THRESHOLD_DEG = 0.5;  // ≈ 55km @ 24°N

function loadCache() {
  try {
    var raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    var c = JSON.parse(raw);
    if (!c || !c.lat || !c.lon || !c.weatherData || !c.cachedAt) return null;
    if (Date.now() - c.cachedAt > CACHE_MAX_AGE_MS) return null;
    return c;
  } catch (e) {
    return null;
  }
}

function saveCache(lat, lon, weatherData, aqiData) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({
      lat: lat, lon: lon,
      weatherData: weatherData,
      aqiData: aqiData,
      cachedAt: Date.now()
    }));
  } catch (e) {}
}

function formatStaleness(ageMs) {
  var min = Math.floor(ageMs / 60000);
  if (min < 5) return '';
  if (min < 60) return min + ' 分鐘前 · 更新中…';
  var hr = Math.floor(min / 60);
  if (hr < 24) return hr + ' 小時前 · 更新中…';
  var day = Math.floor(hr / 24);
  return day + ' 天前 · 更新中…';
}

function isFarFromCache(newLat, newLon, cacheLat, cacheLon) {
  return Math.abs(newLat - cacheLat) > DISTANCE_THRESHOLD_DEG ||
         Math.abs(newLon - cacheLon) > DISTANCE_THRESHOLD_DEG;
}
```

- [ ] **Step 3: 在 hero-section 城市名旁加 staleness chip element**

找到 `<div id="city-name"...>` 的位置（在 hero-section 內），改成：

```html
<span id="city-name"></span><span id="staleness-chip" class="staleness-chip" style="display:none"></span>
```

並加上 hero-section 的 className 處理（找到 `heroSection.classList.add('show');`、後面加邏輯讓 refreshing 狀態可控）。具體實作在 Task 3 一起做。

- [ ] **Step 4: Commit**

```bash
git add templates/weather/index.html
git commit -m "feat(weather): cache helpers + staleness chip CSS (F4 task 1)"
```

---

## Task 2: GPS timeout 8s → 3s

**Files:**
- Modify: `templates/weather/index.html`（一行修改）

- [ ] **Step 1: 改 getCurrentPosition timeout**

找到（約 line 500）：

```javascript
{ timeout: 8000, maximumAge: 300000 }
```

改成：

```javascript
{ timeout: 3000, maximumAge: 300000 }
```

- [ ] **Step 2: Commit**

```bash
git add templates/weather/index.html
git commit -m "fix(weather): GPS timeout 8s -> 3s (F4 task 2)"
```

---

## Task 3: Cache-first 開頁面 flow 重寫

**Files:**
- Modify: `templates/weather/index.html`（重寫 IIFE 末段 `waitForFP(function(){...})`）

- [ ] **Step 1: 加 `renderStalenessChip(cacheAge, isError)` 顯示函式**

加在 helper functions 後（Task 1 加的位置之後）：

```javascript
function renderStalenessChip(cacheAge, isError) {
  var chip = document.getElementById('staleness-chip');
  if (!chip) return;
  if (cacheAge == null) {  // 隱藏
    chip.style.display = 'none';
    chip.classList.remove('error');
    return;
  }
  if (isError) {
    chip.textContent = '更新失敗 · 顯示為快取';
    chip.classList.add('error');
    chip.style.display = 'inline-block';
    return;
  }
  var text = formatStaleness(cacheAge);
  if (!text) {
    chip.style.display = 'none';
    chip.classList.remove('error');
    return;
  }
  chip.textContent = text;
  chip.classList.remove('error');
  chip.style.display = 'inline-block';
}
```

- [ ] **Step 2: 加 `refreshFromCachedLocation(cache)` 背景 fetch swap 函式**

```javascript
function refreshFromCachedLocation(cache) {
  var weatherPromise = fetchWithTimeout('/weather/api/weather?lat=' + cache.lat + '&lon=' + cache.lon, {
    headers: {
      'X-Device-FP': window.__deviceFP || '',
      'X-Device-UID': window.__deviceUID || ''
    }
  }, 10000).then(function(r) { return r.json(); });

  var aqiPromise = fetchWithTimeout('/weather/api/air_quality?lat=' + cache.lat + '&lon=' + cache.lon, {}, 6000)
    .then(function(r) { return r.json(); })
    .catch(function() { return null; });

  return Promise.all([weatherPromise, aqiPromise]).then(function(results) {
    var weather = results[0];
    var aqi = results[1];
    if (!weather || weather.error) {
      renderStalenessChip(Date.now() - cache.cachedAt, true);
      return;
    }
    renderWeatherCard(weather);
    if (aqi && aqi.list && aqi.list[0]) renderAQI(aqi.list[0]);
    renderStalenessChip(null);  // hide chip after swap
    saveCache(cache.lat, cache.lon, weather, aqi);
  }).catch(function() {
    renderStalenessChip(Date.now() - cache.cachedAt, true);
  });
}
```

- [ ] **Step 3: 重寫 `waitForFP` callback 主流程**

把原本（line 495-505）的：

```javascript
waitForFP(function() {
  if ('geolocation' in navigator) {
    navigator.geolocation.getCurrentPosition(
      function(pos) { loadWeatherByCoords(pos.coords.latitude, pos.coords.longitude); },
      function() { fallbackToIP(); },
      { timeout: 3000, maximumAge: 300000 }
    );
  } else {
    fallbackToIP();
  }
});
```

替換成：

```javascript
waitForFP(function() {
  var cache = loadCache();

  if (cache) {
    // 1. 立刻 render cache (instant UI)
    renderWeatherCard(cache.weatherData);
    if (cache.aqiData && cache.aqiData.list && cache.aqiData.list[0]) {
      renderAQI(cache.aqiData.list[0]);
    }
    renderStalenessChip(Date.now() - cache.cachedAt, false);

    // 2. 背景 refresh + GPS 並行
    refreshFromCachedLocation(cache);

    if ('geolocation' in navigator) {
      navigator.geolocation.getCurrentPosition(
        function(pos) {
          if (isFarFromCache(pos.coords.latitude, pos.coords.longitude, cache.lat, cache.lon)) {
            // 跨城市 → 用新位置重 fetch
            loadWeatherByCoords(pos.coords.latitude, pos.coords.longitude);
          }
          // else: cache 位置夠近、refreshFromCachedLocation 已涵蓋
        },
        function() {},  // GPS 失敗：cache 已用、refreshFromCachedLocation 已用 cache lat/lon
        { timeout: 3000, maximumAge: 300000 }
      );
    }
    return;
  }

  // 無 cache → 走原 flow（同現況、但 timeout 3s）
  if ('geolocation' in navigator) {
    navigator.geolocation.getCurrentPosition(
      function(pos) { loadWeatherByCoords(pos.coords.latitude, pos.coords.longitude); },
      function() { fallbackToIP(); },
      { timeout: 3000, maximumAge: 300000 }
    );
  } else {
    fallbackToIP();
  }
});
```

- [ ] **Step 4: 改 `loadWeatherByCoords` 末段加 saveCache**

找到 `loadWeatherByCoords` 的 `Promise.allSettled([...]).then(function(results) {...})`，在 `if (aqiData && aqiData.list && aqiData.list[0]) renderAQI(aqiData.list[0]);` 後加：

```javascript
      saveCache(lat, lon, weather, aqiData);
```

也在 `renderStalenessChip(null);` 隱藏 chip：

```javascript
      saveCache(lat, lon, weather, aqiData);
      renderStalenessChip(null);
```

- [ ] **Step 5: Commit**

```bash
git add templates/weather/index.html
git commit -m "feat(weather): cache-first flow + background swap + cross-city detection (F4 task 3)"
```

---

## Task 4: Progressive UI text（無 cache 場景）

**Files:**
- Modify: `templates/weather/index.html`（loading-block 文字 + 切換邏輯）

- [ ] **Step 1: 改 loading-block markup**

找到（在 hero-section 之前、約 line 158-160）：

```html
<div id="loading-block" class="loading">載入中…</div>
```

改成：

```html
<div id="loading-block" class="loading"><span id="loading-text">正在抓取位置…</span></div>
```

- [ ] **Step 2: 加 `setLoadingText(text)` 函式**

加在 helper functions 區：

```javascript
function setLoadingText(text) {
  var el = document.getElementById('loading-text');
  if (el) el.textContent = text;
}
```

- [ ] **Step 3: 在「無 cache」分支用 setLoadingText**

在 Task 3 的「無 cache → 走原 flow」分支，把：

```javascript
if ('geolocation' in navigator) {
    navigator.geolocation.getCurrentPosition(
      function(pos) { loadWeatherByCoords(pos.coords.latitude, pos.coords.longitude); },
      function() { fallbackToIP(); },
      { timeout: 3000, maximumAge: 300000 }
    );
  } else {
    fallbackToIP();
  }
```

改成：

```javascript
if ('geolocation' in navigator) {
    setLoadingText('正在抓取位置…');
    navigator.geolocation.getCurrentPosition(
      function(pos) {
        setLoadingText('正在抓取天氣…');
        loadWeatherByCoords(pos.coords.latitude, pos.coords.longitude);
      },
      function() {
        setLoadingText('正在抓取天氣…');
        fallbackToIP();
      },
      { timeout: 3000, maximumAge: 300000 }
    );
  } else {
    setLoadingText('正在抓取天氣…');
    fallbackToIP();
  }
```

- [ ] **Step 4: Commit**

```bash
git add templates/weather/index.html
git commit -m "feat(weather): progressive loading text for no-cache flow (F4 task 4)"
```

---

## Task 5: Pytest markup smoke tests

**Files:**
- Create: `tests/test_weather_cache_markup.py`

- [ ] **Step 1: Write failing test file**

```python
"""Markup smoke tests for F4 weather GPS fast load (純驗 template 渲染含預期 marker、
不驗 JS runtime 行為 — runtime 行為走 manual verification flow)."""


def test_weather_page_contains_staleness_chip(client):
    """Cache UX 的 staleness chip element 必須在 template 裡。"""
    res = client.get('/weather/')
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'staleness-chip' in body, "staleness chip element / class 缺失"


def test_weather_page_gps_timeout_3s(client):
    """GPS timeout 必須是 3000 而非舊的 8000。"""
    res = client.get('/weather/')
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'timeout: 3000' in body, "GPS timeout 未降到 3s"
    assert 'timeout: 8000' not in body, "舊的 8s timeout 殘留"


def test_weather_page_localStorage_cache_key(client):
    """Cache key 必須是 spec 定義的 weather_last_location。"""
    res = client.get('/weather/')
    body = res.get_data(as_text=True)
    assert 'weather_last_location' in body, "cache key 缺失"


def test_weather_page_cache_max_age_7days(client):
    """Cache TTL 必須是 7 天（7 * 24 * 3600 * 1000）。"""
    res = client.get('/weather/')
    body = res.get_data(as_text=True)
    assert '7 * 24 * 3600 * 1000' in body, "cache TTL 7 天 expression 缺失"
```

- [ ] **Step 2: 跑測試確認 4 條全綠**

```bash
cd /home/hirain0126/projects/webapp/app_unified
PYTHONPATH=. pytest tests/test_weather_cache_markup.py -v
```

Expected: 4 passed

如果有 fail：回頭看對應 Task 1-4 是不是漏改、修完再跑。

- [ ] **Step 3: 跑全 suite 確認沒打破其他 test**

```bash
PYTHONPATH=. pytest tests/ -q 2>&1 | tail -10
```

Expected: 0 failed（既有 331 passed 加 4 條新的 = 335 passed）。如果有別的 test fail：先看 fail 訊息是否跟 weather 相關，無關的可能是其他 pre-existing tech debt（按 memory `feedback_local_test_before_push` 跑前要確認 baseline）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_weather_cache_markup.py
git commit -m "test(weather): markup smoke for F4 cache + timeout + chip (F4 task 5)"
```

---

## Task 6: Local manual verification（按 [[feedback_local_test_before_push]]）

**Files:** 無新檔、純手動驗證

- [ ] **Step 1: 跑 local dev server**

```bash
cd /home/hirain0126/projects/webapp/app_unified
bash scripts/run_local.sh
```

Expected: server 起在 http://localhost:5000、log 顯示 dev mode enabled。

- [ ] **Step 2: 開 Chrome、進 /dev 切到任意 seed user 帳號**

URL: `http://localhost:5000/dev` → 點任一 user → 自動登入 → 跳 weather 頁。

- [ ] **Step 3: 情境 1 — 首次開（清 localStorage）**

DevTools → Application → Local Storage → `http://localhost:5000` → 刪 `weather_last_location` → 重新整理頁面。

Expected:
- spinner 顯示「正在抓取位置…」
- GPS 抓到後（或 timeout 3s 後）顯示「正在抓取天氣…」
- 天氣顯示出來（< 7s）
- 城市名旁無 staleness chip

- [ ] **Step 4: 情境 2 — 5 分鐘內再開**

立刻重新整理頁面。

Expected:
- 瞬間 render 天氣（< 100ms 看到畫面）
- 無 spinner、無 chip
- DevTools Network: weather + aqi API 仍打、回傳後悄悄 swap（畫面內容不變）

- [ ] **Step 5: 情境 3 — 模擬 1 小時前 cache**

DevTools Console:

```javascript
var c = JSON.parse(localStorage.getItem('weather_last_location'));
c.cachedAt = Date.now() - 60 * 60 * 1000;  // 1 小時前
localStorage.setItem('weather_last_location', JSON.stringify(c));
location.reload();
```

Expected:
- 瞬間 render 天氣 + 城市名旁顯示「1 小時前 · 更新中…」chip
- 1-2 秒後 chip 消失、weatherData swap（畫面可能略變或不變）

- [ ] **Step 6: 情境 4 — 模擬 fetch 失敗（DevTools throttle offline）**

DevTools Network → Throttling → Offline → 重新整理頁面。

Expected:
- 瞬間 render cache 天氣
- chip 顯示「2 天前 · 更新中…」（或當下 age）
- 1-2 秒後 chip 改成紅字「更新失敗 · 顯示為快取」
- 改回 Online、再重新整理 → 正常 swap

- [ ] **Step 7: 情境 5 — 模擬跨城市（手動改 cache lat/lon）**

DevTools Console:

```javascript
var c = JSON.parse(localStorage.getItem('weather_last_location'));
c.lat = 35.6762; c.lon = 139.6503;  // 東京座標
c.cachedAt = Date.now() - 10 * 60 * 1000;  // 10 分鐘前避免 chip 太顯眼
localStorage.setItem('weather_last_location', JSON.stringify(c));
location.reload();
```

Expected:
- 瞬間顯示「東京」相關天氣（背景 fetch 東京 weather）
- chip 顯示「10 分鐘前 · 更新中…」
- GPS 抓到台中位置 → 距離 > 0.5 度 → 重 fetch 台中 weather → swap 成台中

- [ ] **Step 8: 情境 6 — 「天」字按 6 下測試**

任一情境 cache render 出畫面後、立刻連續按「天」字 6 次。

Expected:
- 如果開瞬間（< 500ms）按、可能前 1-2 次無反應
- 等 1 秒後按、6 下應該正常 trigger 登入 flow
- 不需要等 weather swap 完成才能按

- [ ] **Step 9: User 拍板「OK 可以上正式環境」才進 Task 7**

如果發現 bug：回頭改、再跑情境 1-6 直到全綠。

---

## Task 7: Push + GHA build + Zeabur Redeploy

按 [[feedback_local_test_before_push]] + [[feedback_branch_workflow]] + [[reference_ghcr_tag_format]]

- [ ] **Step 1: ff-only merge to main**

```bash
cd /home/hirain0126/projects/webapp/app_unified
git checkout main
git pull origin main  # 確認 main 沒人偷推
git merge --ff-only fix/weather-gps-fast-load
```

Expected: Fast-forward、main HEAD 變成 task 4 的 commit。如果 conflict：用 rebase 處理（不直接 merge with merge commit）。

- [ ] **Step 2: Push origin/main**

```bash
git push origin main
```

Expected: GHA 自動 trigger Docker build（main 在 docker.yml trigger 列）。

- [ ] **Step 3: User 自行 monitor GHA build**

URL: https://github.com/stvlin1988-alt/Weather/actions

等 build 綠（~5-10 分鐘）。

- [ ] **Step 4: User Zeabur Redeploy**

Zeabur app 服務 → Redeploy（image tag `:latest`）。

- [ ] **Step 5: Prod 實機驗證**

開 prod URL `https://<prod 網域>/weather` → 重複情境 1, 2, 3, 6（情境 4 模擬 offline 不好做、可以略；情境 5 跨城市可選做）。

Expected: 跟本機行為一致、無 console error。

- [ ] **Step 6: 清 branch**

```bash
git branch -d fix/weather-gps-fast-load
```

Expected: 成功刪除（因為已 merge 進 main）。

---

## Self-Review 對照 spec

| Spec 段落 | 對應 Task |
|---|---|
| Cache structure (`weather_last_location`) | Task 1 step 2、Task 3 step 4 |
| Cache TTL 7 天 | Task 1 step 2 (`CACHE_MAX_AGE_MS`) |
| 開頁面 flow (cache 立刻 render + 並行 background fetch) | Task 3 step 3 |
| Staleness chip (0-5min/5-60min/1-24h/1-7d) | Task 1 step 1 (CSS) + Task 3 step 1 (`renderStalenessChip`) + Task 1 step 2 (`formatStaleness`) |
| Swap + opacity 0.3s transition | Task 1 step 1 (CSS `.staleness-chip transition`) |
| 跨城市 detection (lat/lon delta > 0.5) | Task 1 step 2 (`isFarFromCache`) + Task 3 step 3 |
| GPS 失敗 fallback | Task 3 step 3（有 cache 場景 GPS 失敗 noop、cache 已用） |
| Fetch 失敗 chip 改紅字 | Task 3 step 1 (`renderStalenessChip` with isError) + Task 3 step 2 (`refreshFromCachedLocation`) |
| GPS timeout 8s→3s | Task 2 step 1 + Task 3 step 3 |
| Progressive UI text | Task 4 |
| 「天」字不加視覺提示 | 不做（spec 已明示） |
| Backend / schema 不動 | 整個 plan 都沒 backend 改動 |
| Capacity check | spec 已寫、純 frontend 無 prod 影響 |

**Type consistency check**: `loadCache()` / `saveCache()` / `formatStaleness()` / `isFarFromCache()` / `renderStalenessChip()` / `refreshFromCachedLocation()` / `setLoadingText()` 7 個函式名稱在後續 task 都對齊。

**Placeholder scan**: 無 TODO / TBD / 「similar to」/ 缺 code 的步驟。

---

## 部署完成後的 follow-up

- 更新 `project_webapp.md` 加一段 5/25 F4 deploy 紀錄
- TaskUpdate #4 marked completed、進 F5（編輯筆記可視範圍加大）
