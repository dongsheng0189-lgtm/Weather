# Checklist Item-Level Lock — Spec

**Branch**: `feat/checklist-item-lock`（chain on `feat/announcement-media-viewer`）
**Date**: 2026-05-25
**Scope**: 把 checklist 確認表單從「整 note 鎖」改成「per-item 鎖」、解決 100+ item 時一人編輯卡住全部的問題。

## 問題

`Note.locked_by` + `LOCK_TTL_SEC = 5 min` 機制原本設計給 note-level 編輯（title / content / status）。Checklist note（`note_type='checklist'`）有 100+ 個 item、a 編輯 item 1 時整個 note 被鎖 5 分鐘、b 連 item 3 都不能編 — 流程嚴重卡。

## Design

### 1. Schema 變動

`models.py` `ChecklistItem` 加兩欄：

```python
class ChecklistItem(db.Model):
    # ... 現有欄位 ...
    locked_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    locked_at = db.Column(db.DateTime, nullable=True)

    locker = db.relationship("User", foreign_keys=[locked_by])
```

**Migration**: `migrations/versions/012_checklist_item_lock.py`
- `ALTER TABLE checklist_items ADD COLUMN locked_by INTEGER REFERENCES users(id) ON DELETE SET NULL`
- `ALTER TABLE checklist_items ADD COLUMN locked_at TIMESTAMP`
- 既有 row 自動 NULL、無需 backfill
- `db.create_all()` 在 dev SQLite boot 時自動建（既有 pattern）；prod 用 alembic upgrade

### 2. Backend Lock Helpers

`notes/routes.py` 加 `LOCK_TTL_SEC` 沿用（5 min）+ item-level helpers（仿既有 note-level）：

```python
def _item_lock_status(item):
    """回傳 (is_locked_by_other: bool, locker: User|None)"""
    if not item.locked_by or not item.locked_at:
        return False, None
    age_sec = (datetime.utcnow() - item.locked_at).total_seconds()
    if age_sec > LOCK_TTL_SEC:
        return False, None
    if item.locked_by == current_user.id:
        return False, None
    locker = User.query.get(item.locked_by)
    return True, locker


def _acquire_item_lock(item):
    item.locked_by = current_user.id
    item.locked_at = datetime.utcnow()
    # 不 commit、呼叫端控制 transaction


def _ensure_item_lock_for_mutation(item, user_id):
    """Return (ok: bool, locker: User|None). 仿 _ensure_lock_for_mutation 邏輯。"""
    from datetime import datetime
    if not item.locked_by or not item.locked_at:
        item.locked_by = user_id
        item.locked_at = datetime.utcnow()
        return True, None
    if item.locked_by == user_id:
        item.locked_at = datetime.utcnow()
        return True, None
    age_sec = (datetime.utcnow() - item.locked_at).total_seconds()
    if age_sec > LOCK_TTL_SEC:
        item.locked_by = user_id
        item.locked_at = datetime.utcnow()
        return True, None
    locker = User.query.get(item.locked_by)
    return False, locker
```

### 3. Backend API endpoints（新）

```python
@notes_bp.route("/api/items/<int:item_id>/lock/heartbeat", methods=["POST"])
@login_required
def item_lock_heartbeat(item_id):
    item = ChecklistItem.query.get_or_404(item_id)
    # 權限：只允許能 edit 該 note 的 user
    note = item.note
    if not _can_edit_note(note):  # 沿用既有 helper
        abort(403)
    ok, locker = _ensure_item_lock_for_mutation(item, current_user.id)
    if not ok:
        db.session.rollback()
        return jsonify({"status": "locked", "locker": locker.username if locker else None}), 409
    db.session.commit()
    return jsonify({"status": "ok"})


@notes_bp.route("/api/items/<int:item_id>/lock/release", methods=["POST"])
@login_required
def item_lock_release(item_id):
    item = ChecklistItem.query.get_or_404(item_id)
    if item.locked_by == current_user.id:
        item.locked_by = None
        item.locked_at = None
        db.session.commit()
    return jsonify({"status": "ok"})
```

### 4. 既有 PUT/DELETE item 加 item-level lock check

`notes/routes.py` 既有：
- `PUT /api/items/<id>` — 加 `ok, locker = _ensure_item_lock_for_mutation(item, current_user.id); if not ok: return 409`
- `DELETE /api/items/<id>` — 同上
- `is_checked` toggle endpoint — **不**走 item-lock check（toggle 是 quick atomic、應該允許併發）

### 5. `_checklist_item_json` 加 lock 資訊

```python
def _checklist_item_json(item):
    data = { ... 現有欄位 ... }
    is_other_locked, locker = _item_lock_status(item)
    if is_other_locked:
        data["locked_by_username"] = locker.username if locker else None
    return data
```

### 6. Note-level lock 在 checklist mode 行為改動

**`editor.html` checklist mode 進頁不立刻 acquire note lock**：

既有 `editor.html` (Task block) 有：
```js
fetch('/notes/api/' + NOTE_ID + '/lock/heartbeat', { method: 'POST' })  // 進頁立刻 heartbeat (acquire)
setInterval(..., 60000)  // 每 60s heartbeat
```

改成 conditional：
```js
if (EDITOR_TYPE !== 'checklist') {
  // note-level lock 流程（既有）
  fetch('/notes/api/' + NOTE_ID + '/lock/heartbeat', { method: 'POST' });
  setInterval(...);
}
// checklist mode 不跑 note-level lock 進頁 acquire
```

**Server-side `_ensure_lock_for_mutation` 仍對 note-level mutation 生效**（save title / status / category / 新增 item / 刪除 item 不需要 — 但有 acquire 行為）：
- Title / store / status / category PUT：第一次 mutation 才 acquire 整 note lock、5 min hold（會擋別人 save 那些 field、但**不**擋 item edit、因為 item-level lock 是分開的）
- 新增 item（POST /api/<note_id>/items）：**不**走 note-level lock check（atomic create、無 conflict）
- 刪除 item（DELETE /api/items/<id>）：走 item-level lock check（別人正編就擋）

### 7. Frontend list render: lock 視覺

`templates/notes/editor.html` 內 `renderChecklistItems()`（行 ~1030 附近）：

```javascript
items.forEach(function(item) {
  var div = document.createElement('div');
  div.className = 'cl-item';
  if (item.locked_by_username) {
    div.classList.add('locked-by-other');
    div.dataset.lockedBy = item.locked_by_username;
  }
  // ... 既有 render ...
});
```

CSS（加在 editor.html 的 `<style>` 區）：

```css
.cl-item.locked-by-other { opacity: 0.5; cursor: not-allowed; position: relative; }
.cl-item.locked-by-other > * { pointer-events: none; }  /* 內部 button/input 不接事件 */
.cl-item.locked-by-other::after {
  content: "🔒 " attr(data-locked-by) " 編輯中";
  position: absolute; top: 4px; right: 8px;
  font-size: 0.75rem; color: #fff;
  background: rgba(150, 50, 50, 0.85);
  padding: 2px 8px; border-radius: 12px;
  pointer-events: none;  /* pseudo-element 本來就不接事件 */
}
```

**設計**：item 本身 cursor + opacity + chip 視覺、內部 button/input 用 `> * { pointer-events: none }` 禁止觸發；外層 `.cl-item` 仍可接 click → 走 toast handler（見 §10）。

### 8. Frontend modal flow: 編輯 item dialog

`openEditItemDialog(item)` 改成 async：

```javascript
async function openEditItemDialog(item) {
  // 先 acquire item lock
  const r = await fetch('/notes/api/items/' + item.id + '/lock/heartbeat', { method: 'POST' });
  if (r.status === 409) {
    const data = await r.json();
    showToast(`${data.locker || '他人'} 編輯中、5 分鐘無動作後可接手`);
    return;
  }
  if (!r.ok) {
    showToast('開啟編輯失敗、請重試');
    return;
  }
  // ... 既有 dialog 開啟邏輯 ...
  _itemLockHeartbeatTimer = setInterval(() => {
    fetch('/notes/api/items/' + item.id + '/lock/heartbeat', { method: 'POST' });
  }, 60000);  // 每 60s heartbeat
}

function closeItemDialog() {
  clearInterval(_itemLockHeartbeatTimer);
  if (_currentEditItemId) {
    fetch('/notes/api/items/' + _currentEditItemId + '/lock/release', { method: 'POST' });
  }
  // ... 既有 close 邏輯 ...
}

window.addEventListener('beforeunload', function() {
  if (_currentEditItemId) {
    navigator.sendBeacon('/notes/api/items/' + _currentEditItemId + '/lock/release',
                         new Blob(['{}'], { type: 'application/json' }));
  }
});
```

**Lock 釋放路徑覆蓋**：
- save 後（送出 PUT）→ 後端 atomic commit 完、frontend close dialog → release
- cancel 按鈕 → release
- ESC 鍵 → release
- 關閉 tab / navigate away → sendBeacon release
- 5 分鐘無 heartbeat（user 把 tab 留著但 inactive）→ 後端 TTL 自動視為 expired、別人可接手

### 9. `showToast(message)` helper

如果 editor.html 已有 toast helper、沿用；沒有則加 minimal 版：

```javascript
function showToast(msg) {
  var t = document.createElement('div');
  t.textContent = msg;
  t.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:rgba(50,50,50,.95);color:#fff;padding:.7rem 1.2rem;border-radius:8px;z-index:10000;font-size:.95rem;box-shadow:0 4px 12px rgba(0,0,0,.3);';
  document.body.appendChild(t);
  setTimeout(function() { t.remove(); }, 3000);
}
```

### 10. List click handler

`renderChecklistItems()` 內、為 lock-by-other item 加 click handler（要在 `pointer-events: none` 之前 attach、或 wrap 在 chip 上）：

```javascript
if (item.locked_by_username) {
  div.addEventListener('click', function(e) {
    e.preventDefault();
    e.stopPropagation();
    showToast(item.locked_by_username + ' 編輯中、5 分鐘無動作後可接手');
  }, true);  // capture phase 接住、配合 §7 CSS 內部 button 不接事件
}
```

## 邊界情境

| 情境 | 處理 |
|---|---|
| User A 點開 item 1 → 5 分鐘內 user A reload 頁面 | reload 後 list 顯示 item 1 lockedByOther=false（自己持鎖、`_item_lock_status` 排除）；user A 重新點開正常 |
| User A 點開 item 1 → 4 分鐘後 user B 點開 | B 看到 item 1 locked、無法點開、toast 提示 |
| User A 點開 item 1 → 關 tab 沒 release | TTL 5 min 後 B 可接手；B reload list 看到 unlock；點開 acquire 成功 |
| User A 編 item 1、user B 刪除 item 1 | B 的 DELETE 被 409 擋住（item-level lock check） |
| User A 編 item 1、user B 改 note title | A 跟 B 互不干擾（A 只 hold item lock、B 觸發 note-level lock 才 acquire note） |
| User A 改 note title（hold note lock）、user B 想改 status | B 的 PUT 被 409 擋住（既有 note-level lock）— 既有行為、不在 F2 scope |
| User A 編 item、user B 同時新增 item | 兩個都成功（新增是 atomic create、無 conflict） |
| 既有 ChecklistItem row（無 lock 欄位）| Migration 後新欄位 NULL、`_item_lock_status` 拿到 None 自動視為未鎖 |
| Toggle is_checked（勾完成）| 不走 lock check（即使別人在編、勾選仍 OK；既有行為） |

## Capacity

- 新增 2 columns / `checklist_items` table、每 row +12 bytes（INT FK + TIMESTAMP）；prod ChecklistItem 估 < 10K row → < 120KB total、無 PG 壓力
- API call 量：每個 user 編 item 時每 60s 一次 heartbeat、5 用戶平均 1 編輯 = 5/min API call → 微不足道
- 對 2 CPU / 8GB RAM prod 影響：0

## 測試策略

### Backend pytest (`tests/test_checklist_item_lock.py`)

仿 `tests/test_notes_lock.py` 結構、~10 條 test：

1. `_ensure_item_lock_for_mutation` 三種狀態（acquire / refresh / reject）
2. heartbeat endpoint 3 種 response（200 acquire / 200 refresh / 409 reject + locker name）
3. release endpoint（200 normal / 200 noop when not mine）
4. PUT `/api/items/<id>` 帶 lock check（self 過 / other 409）
5. DELETE `/api/items/<id>` 帶 lock check（同上）
6. Toggle is_checked 不受 lock 影響
7. 過期 lock 自動讓出（locked_at > 5 min 前）
8. `_checklist_item_json` 含 `locked_by_username` field

### Markup smoke test

`test_shared_fullscreen_viewer.py` 模式、加 `test_checklist_item_lock_markup.py`：
- editor template 含 `.locked-by-other` CSS class
- editor template 含 `showToast` function
- editor template checklist mode 不發出 note-level lock heartbeat 字串（檢查 conditional 寫對）

### Manual verification（user）

不需要 R2、本機 SQLite 跑 checklist note：
1. 開兩個 incognito tab、用兩個 user 同時進同一 checklist note 編輯頁
2. Tab A 點開 item 1 → Tab B reload → item 1 灰底 + 🔒 + 名字 chip
3. Tab B 點 item 1 → toast「XXX 編輯中…」彈出
4. Tab B 點 item 3 → 正常開 dialog（不被擋）
5. Tab A 關 dialog（save / cancel）→ Tab B reload → item 1 unlock
6. Tab A 開 dialog → 5 分鐘不動 → Tab B reload → item 1 unlock（TTL 過期）
7. Tab A 編 item 1、Tab B 試 delete item 1 → 失敗 + 提示

## YAGNI 排除

- 不做「強制釋放鎖」按鈕給 admin（既有 note-level 有、item-level 流量小、用 TTL 5 min 等就好）
- 不做 WebSocket 即時 lock 狀態同步（reload 看更新就夠、太多即時推送增加 ws.py 負擔）
- 不改 toggle is_checked behavior（仍 atomic、無 lock check）
- 不做 lock 視覺對「自己持鎖」也標示（自己鎖了就讓人覺得正在編、不需要再標）

## 部署

1. GHA Docker build
2. Zeabur Redeploy（會觸發 alembic upgrade、新加 2 columns）
3. 既有 ChecklistItem row 新 lock 欄位自動 NULL、不影響現存資料
