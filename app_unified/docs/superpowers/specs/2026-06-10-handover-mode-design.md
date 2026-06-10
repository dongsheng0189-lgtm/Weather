# 交接模式 + 快速鎖 Design（Spec A）

> Date: 2026-06-10 · Module: `auth` / 全站(`base.html`) · Status: approved（待 user review spec）

## Goal

店務筆記/確認表單交接班時，常需邊看手機邊討論超過 5 分鐘 idle 上限而被踢出重登，很困擾。新增**交接模式**：admin 以上的人一鍵把 idle 上限從 5 分鐘延長到 **15 分鐘（純滑動，有互動就 reset）**，並提供**立即鎖定（快速鎖）**與**結束交接**兩顆按鈕；兼顧資料安全（人離開 15 分鐘沒互動仍自動鎖、隨時可一鍵鎖死）。

## Context（現況，已驗證）

- **前端**（`templates/base.html`）：5 分鐘無互動（mousemove/mousedown/keydown/touchstart/scroll 會 reset）→ 蓋黑幕 + `/auth/logout`；可由 `window.IDLE_LIMIT_MS`（毫秒）覆寫上限。鎖屏/切走時預蓋黑幕防偷看。
- **後端**（`app.py:_enforce_session_idle`，`before_request`）：依最近活動路徑動態 `max_idle` —— `/mynotes`(桌機)→30 分、`/vetting`→15 分、其他→5 分；每 request 更新 `session['_last_request_at']`，`PERMANENT_SESSION_LIFETIME=35min`。grace 用 session flag（如 `_mynotes_grace_until`）實作。
- **權限**：`User.is_admin()` 回 True 給 `admin` + `super_admin`（= 本案「admin 以上」）。
- **context processor**：`app.py:103 inject_features` 注入 template 全域變數（`has_feature`/`multi_feature` 等）。
- **nav**（base.html）：兩套（桌機 `.nav-links` + 手機 `.nav-dropdown`），admin 專屬連結用 `{% if current_user.is_admin() %}` 包（管理 / 人臉設定）。

## 範圍決策（已與 user 拍板）

| 項目 | 決定 |
|---|---|
| 延長時長 | idle 上限 5 分 → **15 分鐘，純滑動**（有互動 reset，無總時長硬上限） |
| 安全姿態 | 受控延長（B）：admin+ 才能開、全程顯眼 banner、可隨時手動鎖/結束、15 分無互動仍自動鎖 |
| 啟用權限 | **admin 以上**（`is_admin()`） |
| 啟用鈕位置 | **右上 nav**（admin+ 才顯示），沿用既有 admin 連結 pattern |
| 啟用後 UI | 全站頂部黃色 banner +「🔒 立即鎖定」+「結束」 |
| 🔒 立即鎖定 | 關交接 + **立即登出**（要重 PIN+人臉） |
| 結束 | 只關交接、**留在登入狀態**、idle 回 5 分鐘 |
| 不做 | B（店家 off 即時踢人）— 另案 |

## 設計

### A. 後端（`app.py` + `auth/routes.py`）

1. **`_enforce_session_idle`**：在算出既有 grace 的 `max_idle` 後，加一句：
   ```python
   if session.get("_handover"):
       max_idle = max(max_idle, 15 * 60)
   ```
   （與其他 grace 取最長；純滑動由既有「每 request 更新 `_last_request_at`」達成。）

2. **新 endpoint（`auth` blueprint）**：
   - `POST /auth/handover/start`：`@login_required`；`if not current_user.is_admin(): return 403`；`session["_handover"] = True`；`session["_last_request_at"] = int(time.time())`；回 `{"status":"ok"}`。
   - `POST /auth/handover/end`：`@login_required`；`session.pop("_handover", None)`；`session["_last_request_at"] = int(time.time())`；回 `{"status":"ok"}`。（任何登入者可結束自己的；不需 admin。）

3. **context processor**（`app.py`，新增或併入既有）：注入 `handover_active = bool(session.get("_handover"))` 給 template。

> 立即鎖定走既有 `/auth/logout`（會 `session.clear()`，`_handover` 一併清掉）。15 分鐘無互動 → 前端蓋黑幕 + `/auth/logout` → 同樣清掉。新登入的 session 不帶 `_handover`。

### B. 前端（`templates/base.html`）

1. **延長 idle 連動**：在 idle IIFE 之前注入——
   ```html
   {% if handover_active %}<script>window.IDLE_LIMIT_MS = 15 * 60 * 1000;</script>{% endif %}
   ```
   （既有 IIFE 已支援讀 `window.IDLE_LIMIT_MS` 覆寫；非交接時不設 → 維持 5 分鐘。）

2. **頂部 banner**（`{% if handover_active %}`，放 `<nav>` 之後、`.container` 之前，全站可見）：
   ```html
   <div id="handover-banner" style="background:#fff3cd;border-bottom:2px solid #ffc107;
        padding:.5rem 1rem;display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;
        font-size:.9rem;color:#856404;">
     <span>🤝 <strong>交接模式中</strong>（閒置 15 分鐘才鎖）</span>
     <button type="button" onclick="window.location.href='/auth/logout'"
             style="margin-left:auto;...紅...">
       <!-- 內嵌 alert-circle SVG（驚嘆號），stroke=currentColor → 白色細線 -->
       <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
            style="vertical-align:-2px;margin-right:.3rem;">
         <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/>
         <line x1="12" y1="16" x2="12.01" y2="16"/></svg>關閉模式並登出</button>
     <button type="button" onclick="endHandover()" style="...灰...">結束交接（保持登入）</button>
   </div>
   ```

3. **nav 啟用鈕**（兩套 nav 的 `{% if current_user.is_admin() %}` 區塊內，且 `{% if not handover_active %}` 時才顯示，避免已開還顯示）：
   ```html
   {% if not handover_active %}<a href="#" onclick="startHandover();return false;">🤝 交接模式</a>{% endif %}
   ```

4. **JS**（base.html 內）：
   ```javascript
   function startHandover() {
     if (!confirm('開啟交接模式？閒置上限延長為 15 分鐘，期間請留意手機，可隨時「立即鎖定」。')) return;
     fetch('/auth/handover/start', {method:'POST', headers:{'Content-Type':'application/json'}})
       .then(function(r){ if (r.ok) location.reload(); else alert('開啟失敗（需管理員權限）'); })
       .catch(function(){ alert('網路錯誤'); });
   }
   function endHandover() {
     fetch('/auth/handover/end', {method:'POST', headers:{'Content-Type':'application/json'}})
       .then(function(){ location.reload(); }).catch(function(){ location.reload(); });
   }
   ```

### 流程
1. admin 點 nav「🤝 交接模式」→ confirm → POST start → reload → banner 出現、idle 變 15 分。
2. 交接中積極使用(滑/點)→ 一直延續;放著沒動 15 分 → 自動蓋黑幕登出。
3. 要立刻鎖 → 「🔒 立即鎖定」→ 登出。要繼續用但回正常安全 → 「結束」→ 留在登入、idle 回 5 分。

## 邊界 / 安全
- 非 admin 打 `/auth/handover/start` → 403（後端為準，nav 鈕只是不顯示）。
- 純 session flag，跟著 session 走；登出/清 session 即失效。
- handover 是 per-session（該裝置該登入），不影響其他人/裝置。
- super_admin（store=None）同樣可用。

## 測試（pytest）`tests/test_handover_mode.py`
- admin POST `/auth/handover/start` → 200 + `session['_handover']` True。
- 一般 user POST start → 403、flag 未設。
- 未登入 POST start → 401/302。
- POST `/auth/handover/end` → 200 + flag 清除。
- `_enforce_session_idle`：設 `_handover` 後，距上次活動 6 分鐘（>5、<15）**不**登出；16 分鐘（>15）登出。（用 test client 設 session + 兩次 request 模擬，或直接呼叫 helper。）
- context processor / banner：GET 任一頁，`_handover` session 有設時 body 含 `id="handover-banner"`；admin GET 未開時 nav 含「🤝 交接模式」。
- 前端 banner/idle 連動互動 → 手動測。

## 部署影響
- 動 `app.py`（idle 1 段 + 2 endpoint + context processor）、`base.html`（banner + nav 鈕 + JS + IDLE_LIMIT 連動）。
- **無新 DB 欄位**（純 session）→ 不需 inline schema repair。**無新 static**（inline）→ 不用 bump SW。
