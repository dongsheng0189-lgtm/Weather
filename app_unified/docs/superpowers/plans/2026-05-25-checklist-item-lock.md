# Checklist Item-Level Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ChecklistItem 加 `locked_by` + `locked_at`、新 item-level heartbeat/release API、編輯時只鎖該 item、其他 user 可平行編其他 item / 新增 / 勾完成。WebSocket 即時同步 lock 狀態（< 1s）。

**Architecture:** DB-backed per-item lock（沿用 note-level 5min TTL pattern），既有 5 處 note-level mutation 中 3 處改成 item-level（新增 atomic、PATCH/DELETE 走 item-lock），attachment 跟 note 整體編輯仍 note-level。WebSocket 沿用既有 `note-<id>` room + 加 `op: 'il'` event。

**Tech Stack:** Flask + SQLAlchemy + flask-socketio + Alembic migration + vanilla JS。

**Spec:** `docs/superpowers/specs/2026-05-25-checklist-item-lock.md`

**Branch:** `feat/checklist-item-lock`（已開、chain on `feat/announcement-media-viewer`）

**現況關鍵 line 號**（從 grep notes/routes.py / models.py）：
- `models.py:166` ChecklistItem class
- `notes/routes.py:90` `LOCK_TTL_SEC = 5 * 60`
- `notes/routes.py:93` `_ensure_lock_for_mutation` (note-level)
- `notes/routes.py:1318` `_checklist_item_json`
- `notes/routes.py:1361` `create_checklist_item` (POST、含 `_ensure_lock_for_mutation` 要拿掉)
- `notes/routes.py:1390` `update_checklist_item` (PATCH、要換成 item-lock for text、is_checked 不檢)
- `notes/routes.py:1435` `delete_checklist_item` (DELETE、要換成 item-lock)
- `templates/notes/editor.html:88-93` lock-banner（要加 `{% if editor_type != 'checklist' %}`)
- `templates/notes/editor.html:434-456` note-level heartbeat / release（要加 conditional）

---

## Task 1: Schema + Migration

**Files:**
- Modify: `models.py:166-183` (ChecklistItem class)
- Create: `migrations/versions/012_checklist_item_lock.py`

- [ ] **Step 1: 在 ChecklistItem 加 locked_by + locked_at + relationship**

`models.py` 找到 `class ChecklistItem(db.Model):` 區，在 `checked_by_name = db.Column(...)` 後 + `created_at` 之前加：

```python
    locked_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    locked_at = db.Column(db.DateTime, nullable=True)
```

並在 `checker = db.relationship(...)` 後加：

```python
    locker = db.relationship("User", foreign_keys=[locked_by])
```

- [ ] **Step 2: 寫 migration 012**

```python
"""Add checklist_items.locked_by + locked_at for item-level lock (F2)

Revision ID: 012
Revises: 011
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa


revision = '012'
down_revision = '011'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('checklist_items',
        sa.Column('locked_by', sa.Integer(), nullable=True))
    op.add_column('checklist_items',
        sa.Column('locked_at', sa.DateTime(), nullable=True))
    op.create_foreign_key(
        'fk_checklist_items_locked_by',
        'checklist_items', 'users',
        ['locked_by'], ['id'],
        ondelete='SET NULL'
    )


def downgrade():
    op.drop_constraint('fk_checklist_items_locked_by', 'checklist_items', type_='foreignkey')
    op.drop_column('checklist_items', 'locked_at')
    op.drop_column('checklist_items', 'locked_by')
```

- [ ] **Step 3: 跑既有 test 確認 db.create_all() (dev SQLite) 能起來**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/test_notes_lock.py -q 2>&1 | tail -3
```

Expected: 全綠（既有 lock test 不該打破、新欄位 NULL 預設）。

- [ ] **Step 4: Commit**

```bash
git add models.py migrations/versions/012_checklist_item_lock.py
git -c commit.gpgsign=false commit -m "feat(model): ChecklistItem add locked_by + locked_at (F2 task 1)

migration 012、ondelete SET NULL、既有 row 自動 NULL 無需 backfill。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Backend item-level lock helpers

**Files:**
- Modify: `notes/routes.py`（在 `_ensure_lock_for_mutation` after、約 line 115 後加新 helpers）

- [ ] **Step 1: 寫 4 個 helper function**

在 `notes/routes.py` line 115 (`_ensure_lock_for_mutation` 結束) 之後加：

```python
# ══════════ Item-level lock (F2 spec) ══════════

def _item_lock_status(item):
    """回傳 (is_locked_by_other, locker_user_or_None)。
    判斷邏輯跟 note-level _lock_status 對等。"""
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
    """無條件 acquire；呼叫端負責 commit。"""
    item.locked_by = current_user.id
    item.locked_at = datetime.utcnow()


def _ensure_item_lock_for_mutation(item, user_id):
    """Return (ok, locker)。仿 _ensure_lock_for_mutation 邏輯。
    - 無鎖 → 立即 acquire 給 user_id
    - 自己持鎖 → refresh locked_at
    - 過期鎖 → 自動讓出 + acquire 給 user_id
    - 別人持新鎖 → (False, locker)
    呼叫端負責 commit / rollback。"""
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


def _broadcast_item_lock(item, locked_by_user=None, deleted=False):
    """廣播 item lock 狀態變更給 note-<id> room (排除 sender)。
    locked_by_user=None 代表解鎖；deleted=True 通知 client 移除 DOM。"""
    from extensions import socketio
    socketio.emit('r', {
        'op': 'il',
        'note_id': item.note_id,
        'item_id': item.id,
        'locked_by_id': locked_by_user.id if locked_by_user else None,
        'locked_by_username': locked_by_user.username if locked_by_user else None,
        'deleted': deleted,
    }, to=f'note-{item.note_id}', include_self=False)
```

- [ ] **Step 2: Commit**

```bash
git add notes/routes.py
git -c commit.gpgsign=false commit -m "feat(notes): item-level lock helpers (F2 task 2)

_item_lock_status / _acquire_item_lock / _ensure_item_lock_for_mutation /
_broadcast_item_lock — 仿 note-level pattern + 加 socketio 'il' broadcast。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 新 API endpoints `/api/items/<id>/lock/heartbeat` + `/lock/release`

**Files:**
- Modify: `notes/routes.py`（在 helpers 後、create_checklist_item 之前加 2 endpoints）

- [ ] **Step 1: 寫 heartbeat + release endpoint**

加在 `notes/routes.py` line 1359 (`@notes_bp.route("/api/<int:note_id>/items", methods=["POST"])`) 之前：

```python
@notes_bp.route("/api/items/<int:item_id>/lock/heartbeat", methods=["POST"])
@login_required
def item_lock_heartbeat(item_id):
    item = ChecklistItem.query.get(item_id)
    if not item:
        return jsonify({"status": "error", "message": "項目不存在"}), 404
    note = _get_note_for_edit(item.note_id)
    if not note:
        return jsonify({"status": "error", "message": "無權限"}), 403
    # 判斷是否第一次 acquire（要 broadcast）vs refresh（不 broadcast）
    was_unlocked = (not item.locked_by) or (item.locked_by != current_user.id and
        (not item.locked_at or
         (datetime.utcnow() - item.locked_at).total_seconds() > LOCK_TTL_SEC))
    ok, locker = _ensure_item_lock_for_mutation(item, current_user.id)
    if not ok:
        db.session.rollback()
        return jsonify({"status": "locked",
                        "locker": locker.username if locker else None}), 409
    db.session.commit()
    if was_unlocked:
        _broadcast_item_lock(item, locked_by_user=current_user)
    return jsonify({"status": "ok"})


@notes_bp.route("/api/items/<int:item_id>/lock/release", methods=["POST"])
@login_required
def item_lock_release(item_id):
    item = ChecklistItem.query.get(item_id)
    if not item:
        return jsonify({"status": "ok"})  # idempotent
    if item.locked_by == current_user.id:
        item.locked_by = None
        item.locked_at = None
        db.session.commit()
        _broadcast_item_lock(item, locked_by_user=None)
    return jsonify({"status": "ok"})
```

- [ ] **Step 2: Commit**

```bash
git add notes/routes.py
git -c commit.gpgsign=false commit -m "feat(notes): item lock heartbeat + release API (F2 task 3)

POST /api/items/<id>/lock/heartbeat: acquire/refresh/409
POST /api/items/<id>/lock/release: idempotent unlock
第一次 acquire 才 broadcast 'il'、refresh 不發。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 既有 3 個 item endpoint 改 lock check + 1 個拿掉

**Files:**
- Modify: `notes/routes.py:1361,1390,1435`

- [ ] **Step 1: `create_checklist_item` (line 1361) 拿掉 note-lock check**

找到 `def create_checklist_item(note_id):` block，刪除這段：

```python
    ok, locker = _ensure_lock_for_mutation(note, current_user.id)
    if not ok:
        return jsonify({"status": "error",
                        "message": f"目前由 {locker.username if locker else '其他人'} 編輯中"}), 409
```

新增 item 改成 atomic INSERT、不檢 note-lock（兩人同時新增都成功）。

- [ ] **Step 2: `update_checklist_item` (line 1390) 換成 item-lock for text edit**

找到 `def update_checklist_item(item_id):` block，把：

```python
    ok, locker = _ensure_lock_for_mutation(note, current_user.id)
    if not ok:
        return jsonify({"status": "error",
                        "message": f"目前由 {locker.username if locker else '其他人'} 編輯中"}), 409
    data = request.get_json(silent=True) or {}
```

改成：

```python
    data = request.get_json(silent=True) or {}
    # F2: text 改動需要 item-level lock；只 toggle is_checked 不檢（atomic、允許併發）
    is_text_edit = "text" in data and (data.get("text") or "").strip() != item.text
    if is_text_edit:
        ok, locker = _ensure_item_lock_for_mutation(item, current_user.id)
        if not ok:
            db.session.rollback()
            return jsonify({"status": "error",
                            "message": f"{locker.username if locker else '他人'} 編輯中"}), 409
```

- [ ] **Step 3: `update_checklist_item` save 後 release item-lock + broadcast**

繼續同 function、在 `db.session.commit()` (line 1429) 之前加：

```python
    if is_text_edit:
        item.locked_by = None
        item.locked_at = None
```

在 `db.session.commit()` 之後加：

```python
    if is_text_edit:
        _broadcast_item_lock(item, locked_by_user=None)
```

- [ ] **Step 4: `delete_checklist_item` (line 1435) 換成 item-lock**

找到 `def delete_checklist_item(item_id):` block，把：

```python
    ok, locker = _ensure_lock_for_mutation(note, current_user.id)
    if not ok:
        return jsonify({"status": "error",
                        "message": f"目前由 {locker.username if locker else '其他人'} 編輯中"}), 409
```

改成：

```python
    ok, locker = _ensure_item_lock_for_mutation(item, current_user.id)
    if not ok:
        db.session.rollback()
        return jsonify({"status": "error",
                        "message": f"{locker.username if locker else '他人'} 編輯中、無法刪除"}), 409
```

- [ ] **Step 5: `delete_checklist_item` 删除前 broadcast deleted**

在 `db.session.commit()` (line 1456) 之前抓 item 資訊：

```python
    item_id_for_broadcast = item.id
    note_id_for_broadcast = item.note_id
```

在 `db.session.commit()` 之後加（item 已 delete、用一個 stub）：

```python
    # broadcast item 已刪、client 移除 DOM
    from extensions import socketio
    socketio.emit('r', {
        'op': 'il',
        'note_id': note_id_for_broadcast,
        'item_id': item_id_for_broadcast,
        'locked_by_id': None,
        'locked_by_username': None,
        'deleted': True,
    }, to=f'note-{note_id_for_broadcast}', include_self=False)
```

- [ ] **Step 6: Commit**

```bash
git add notes/routes.py
git -c commit.gpgsign=false commit -m "refactor(notes): item endpoints use item-level lock (F2 task 4)

- POST /api/<id>/items: 拿掉 note-lock check、改 atomic INSERT
- PATCH /api/items/<id>: text 改動走 item-lock、save 後 release + broadcast；
  is_checked toggle 不檢 lock
- DELETE /api/items/<id>: 走 item-lock check + broadcast deleted

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `_checklist_item_json` 加 lock 資訊給 frontend render

**Files:**
- Modify: `notes/routes.py:1318` (`_checklist_item_json` function)

- [ ] **Step 1: 加 locked_by_username field**

找到 `def _checklist_item_json(item):` (line 1318)、在 return dict 內加：

```python
def _checklist_item_json(item):
    data = {
        # ... 既有 fields ...
    }
    # F2: 加 lock 狀態給 frontend render lock chip
    is_other_locked, locker = _item_lock_status(item)
    if is_other_locked:
        data["locked_by_username"] = locker.username if locker else None
    else:
        data["locked_by_username"] = None
    return data
```

實際 edit 用 minimal diff：在原 return statement 之前加上述兩行 + 在 dict 內加 `"locked_by_username": ...` field。

- [ ] **Step 2: Commit**

```bash
git add notes/routes.py
git -c commit.gpgsign=false commit -m "feat(notes): _checklist_item_json 加 locked_by_username (F2 task 5)

讓 frontend render 拿到 lock 狀態畫 chip。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Frontend list render 加 lock 視覺 + CSS + click handler

**Files:**
- Modify: `templates/notes/editor.html`（CSS 區 + checklist render JS 區）

- [ ] **Step 1: 加 `.locked-by-other` CSS + showToast helper CSS**

在 `templates/notes/editor.html` 的 `<style>` 區（line ~80 附近、`.cl-item` rules 旁邊）加：

```css
.cl-item.locked-by-other { opacity: 0.5; cursor: not-allowed; position: relative; }
.cl-item.locked-by-other > * { pointer-events: none; }
.cl-item.locked-by-other::after {
  content: "🔒 " attr(data-locked-by) " 編輯中";
  position: absolute; top: 4px; right: 8px;
  font-size: 0.75rem; color: #fff;
  background: rgba(150, 50, 50, 0.85);
  padding: 2px 8px; border-radius: 12px;
  pointer-events: none;
}
.cl-toast {
  position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
  background: rgba(50,50,50,.95); color: #fff;
  padding: .7rem 1.2rem; border-radius: 8px;
  z-index: 10000; font-size: .95rem;
  box-shadow: 0 4px 12px rgba(0,0,0,.3);
}
```

- [ ] **Step 2: 加 showToast helper function**

在 IIFE 內 helper 區（`function _setupNoteWsRoom()` 附近）加：

```javascript
function showToast(msg) {
  var t = document.createElement('div');
  t.className = 'cl-toast';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(function() { t.remove(); }, 3000);
}
```

- [ ] **Step 3: 在 renderChecklistItems 內加 lock 視覺 + click handler**

Grep 找到 `renderChecklistItems` 或類似 function（`grep -n "renderChecklist\|cl-item" templates/notes/editor.html`）。在每個 `var div = document.createElement('div'); div.className = 'cl-item';` 之後、最後 `list.appendChild(div)` 之前加：

```javascript
  if (item.locked_by_username) {
    div.classList.add('locked-by-other');
    div.dataset.lockedBy = item.locked_by_username;
    div.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopPropagation();
      showToast(item.locked_by_username + ' 編輯中、5 分鐘無動作後可接手');
    }, true);  // capture phase
  }
```

- [ ] **Step 4: Commit**

```bash
git add templates/notes/editor.html
git -c commit.gpgsign=false commit -m "feat(notes/editor): item lock 視覺 + chip + toast (F2 task 6)

cl-item.locked-by-other CSS：opacity 0.5 + cursor not-allowed + 🔒 chip。
新加 showToast helper、renderChecklistItems 加 lock visual + click handler。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend modal flow — heartbeat / release for item edit

**Files:**
- Modify: `templates/notes/editor.html`（`openEditItemDialog` + `closeItemDialog` + unload handler）

- [ ] **Step 1: 加 module-level lock state vars**

在 IIFE 內、checklist 區附近加：

```javascript
var _currentEditItemId = null;
var _itemLockHeartbeatTimer = null;
```

- [ ] **Step 2: `openEditItemDialog(item)` 改成 async + 加 lock acquire**

Grep 找到 `function openEditItemDialog`（`grep -n "openEditItemDialog" templates/notes/editor.html`）。在 function 開頭加 lock acquire：

```javascript
async function openEditItemDialog(item) {
  // F2: 先 acquire item lock 才開 dialog
  try {
    var r = await fetch('/notes/api/items/' + item.id + '/lock/heartbeat', { method: 'POST' });
    if (r.status === 409) {
      var data = await r.json();
      showToast((data.locker || '他人') + ' 編輯中、5 分鐘無動作後可接手');
      return;
    }
    if (!r.ok) {
      showToast('開啟編輯失敗、請重試');
      return;
    }
  } catch (e) {
    showToast('網路錯誤、請重試');
    return;
  }
  _currentEditItemId = item.id;
  _itemLockHeartbeatTimer = setInterval(function() {
    fetch('/notes/api/items/' + item.id + '/lock/heartbeat', { method: 'POST' }).catch(function() {});
  }, 60000);
  // ↓ 既有 dialog 開啟邏輯（不動）
  // document.getElementById('item-modal-title').textContent = ...;
  // ...
}
```

- [ ] **Step 3: `closeItemDialog()` 加 release**

Grep 找到 `function closeItemDialog`，在 function 開頭加：

```javascript
function closeItemDialog() {
  // F2: 釋放 item lock
  if (_itemLockHeartbeatTimer) {
    clearInterval(_itemLockHeartbeatTimer);
    _itemLockHeartbeatTimer = null;
  }
  if (_currentEditItemId) {
    fetch('/notes/api/items/' + _currentEditItemId + '/lock/release', { method: 'POST' }).catch(function() {});
    _currentEditItemId = null;
  }
  // ↓ 既有 close 邏輯（不動）
}
```

- [ ] **Step 4: window beforeunload sendBeacon release**

在 IIFE 末尾或 init 處加（若已有既有 beforeunload handler 就 merge 進去）：

```javascript
window.addEventListener('beforeunload', function() {
  if (_currentEditItemId) {
    navigator.sendBeacon && navigator.sendBeacon(
      '/notes/api/items/' + _currentEditItemId + '/lock/release',
      new Blob(['{}'], { type: 'application/json' })
    );
  }
});
```

- [ ] **Step 5: Commit**

```bash
git add templates/notes/editor.html
git -c commit.gpgsign=false commit -m "feat(notes/editor): item lock acquire + heartbeat + release (F2 task 7)

openEditItemDialog 先 fetch heartbeat acquire、60s interval refresh、
closeItemDialog + beforeunload sendBeacon release。
409 → showToast「XXX 編輯中」+ 不開 dialog。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Frontend WebSocket 'il' event listener

**Files:**
- Modify: `templates/notes/editor.html:377` (既有 `sock.on('r', ...)` handler)

- [ ] **Step 1: 在既有 sock.on('r') handler 內加 'il' case**

找到 `sock.on('r', function(msg) {` (line ~377)、在最後 `}` 之前加：

```javascript
    } else if (msg.op === 'il') {
      // F2: item lock 狀態變更（別人 acquire / release / save 完 / delete）
      var itemDiv = document.querySelector('#checklist-items > div[data-item-id="' + msg.item_id + '"]');
      if (!itemDiv) return;
      if (msg.deleted) {
        itemDiv.remove();
        return;
      }
      if (msg.locked_by_username) {
        itemDiv.classList.add('locked-by-other');
        itemDiv.dataset.lockedBy = msg.locked_by_username;
      } else {
        itemDiv.classList.remove('locked-by-other');
        delete itemDiv.dataset.lockedBy;
      }
    }
```

- [ ] **Step 2: Commit**

```bash
git add templates/notes/editor.html
git -c commit.gpgsign=false commit -m "feat(notes/editor): WebSocket 'il' listener for item lock sync (F2 task 8)

接 server broadcast、更新 item DOM lock 視覺 / 移除 deleted item、
即時度 <1s。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: editor.html checklist mode 不發 note-level heartbeat + 隱藏 lock-banner

**Files:**
- Modify: `templates/notes/editor.html:88-93` (lock-banner) + `templates/notes/editor.html:434-456` (heartbeat block)

- [ ] **Step 1: lock-banner 加 checklist 條件**

找到 `<div id="lock-banner" ...>` (line 88)、在外面加 Jinja conditional：

```html
{% if editor_type != 'checklist' %}
<div id="lock-banner" ...>
  ...既有內容...
</div>
{% endif %}
```

注意：要包含到 `</div>` 結尾（line 93）。

- [ ] **Step 2: heartbeat block 加 checklist 條件**

Grep 找到既有 note-level heartbeat（`grep -n "/lock/heartbeat" templates/notes/editor.html`）、應該在 line ~434 附近。把整段 heartbeat init code 包在 conditional 內：

```javascript
if (EDITOR_TYPE !== 'checklist') {
  // F2: note-level lock 只在 general note mode 啟用
  // 既有 heartbeat init code...
  _hbTimer = setInterval(function() {
    fetch('/notes/api/' + NOTE_ID + '/lock/heartbeat', { method: 'POST' })
      .then(...)
      ...
  }, 60000);
  // 既有 beforeunload sendBeacon release...
  window.addEventListener('beforeunload', function() {
    navigator.sendBeacon && navigator.sendBeacon('/notes/api/' + NOTE_ID + '/lock/release', ...);
  });
}
```

實際 edit 要先 read editor.html 那段、確定包到對的範圍（不要把 item 級的 unload 也包進去）。

- [ ] **Step 3: 跑既有 notes_lock test 確認 general note flow 沒打破**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/test_notes_lock.py -q 2>&1 | tail -3
```

Expected: 13 passed。

- [ ] **Step 4: Commit**

```bash
git add templates/notes/editor.html
git -c commit.gpgsign=false commit -m "feat(notes/editor): checklist mode skip note-level lock UI (F2 task 9)

lock-banner 用 Jinja {% if editor_type != 'checklist' %} 包起、heartbeat
init JS 也加 EDITOR_TYPE !== 'checklist' 條件。general note 行為完全不動。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Pytest backend test

**Files:**
- Create: `tests/test_checklist_item_lock.py`

- [ ] **Step 1: 寫 8 條 test**

```python
"""Item-level lock tests for F2 spec: docs/superpowers/specs/2026-05-25-checklist-item-lock.md"""
import uuid
import json
import pytest
from datetime import datetime, timedelta

from extensions import db
from models import User, Note, ChecklistItem


def _mk_users(db_session):
    suffix = uuid.uuid4().hex[:6]
    alice = User(username=f"alice_{suffix}", store="A", role="admin")
    alice.set_password("x")
    bob = User(username=f"bob_{suffix}", store="A", role="admin")
    bob.set_password("x")
    db_session.add_all([alice, bob])
    db_session.commit()
    return alice, bob


def _mk_checklist_note(db_session, store="A"):
    n = Note(title="t", content="", store=store, category="misc", note_type="checklist")
    db_session.add(n)
    db_session.commit()
    return n


def _mk_item(db_session, note, text="x"):
    item = ChecklistItem(note_id=note.id, text=text, order_index=0)
    db_session.add(item)
    db_session.commit()
    return item


def test_ensure_item_lock_acquires_when_unlocked(app, db_session):
    from notes.routes import _ensure_item_lock_for_mutation
    alice, _bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    ok, locker = _ensure_item_lock_for_mutation(item, alice.id)
    assert ok is True
    assert item.locked_by == alice.id
    assert item.locked_at is not None


def test_ensure_item_lock_passes_when_self_locked(app, db_session):
    from notes.routes import _ensure_item_lock_for_mutation
    alice, _bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    ok, _locker = _ensure_item_lock_for_mutation(item, alice.id)
    assert ok is True


def test_ensure_item_lock_rejects_when_other_locked_fresh(app, db_session):
    from notes.routes import _ensure_item_lock_for_mutation
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    ok, locker = _ensure_item_lock_for_mutation(item, bob.id)
    assert ok is False
    assert locker.username == alice.username


def test_ensure_item_lock_takes_over_expired_lock(app, db_session):
    from notes.routes import _ensure_item_lock_for_mutation, LOCK_TTL_SEC
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow() - timedelta(seconds=LOCK_TTL_SEC + 60)
    db_session.commit()
    ok, _locker = _ensure_item_lock_for_mutation(item, bob.id)
    assert ok is True
    assert item.locked_by == bob.id


def test_item_lock_heartbeat_endpoint_acquires(app, db_session, logged_in_admin_client, admin_user):
    n = _mk_checklist_note(db_session, store=admin_user.store or "A")
    admin_user.store = "A"
    n.store = "A"
    db_session.commit()
    item = _mk_item(db_session, n)
    res = logged_in_admin_client.post(f'/notes/api/items/{item.id}/lock/heartbeat')
    assert res.status_code == 200
    db_session.refresh(item)
    assert item.locked_by == admin_user.id


def test_item_lock_release_endpoint(app, db_session, logged_in_admin_client, admin_user):
    admin_user.store = "A"
    db_session.commit()
    n = _mk_checklist_note(db_session, store="A")
    item = _mk_item(db_session, n)
    item.locked_by = admin_user.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    res = logged_in_admin_client.post(f'/notes/api/items/{item.id}/lock/release')
    assert res.status_code == 200
    db_session.refresh(item)
    assert item.locked_by is None
    assert item.locked_at is None


def test_patch_item_text_locked_by_other_returns_409(app, db_session, logged_in_admin_client, admin_user):
    admin_user.store = "A"
    db_session.commit()
    alice = User(username=f"alice_{uuid.uuid4().hex[:6]}", store="A", role="admin")
    alice.set_password("x")
    db_session.add(alice)
    db_session.commit()
    n = _mk_checklist_note(db_session, store="A")
    item = _mk_item(db_session, n, text="original")
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    res = logged_in_admin_client.patch(f'/notes/api/items/{item.id}',
                                        json={"text": "changed"})
    assert res.status_code == 409


def test_patch_item_is_checked_does_not_require_lock(app, db_session, logged_in_admin_client, admin_user):
    """is_checked toggle 不檢 lock — 即使別人在編 text、仍可勾選。"""
    admin_user.store = "A"
    db_session.commit()
    alice = User(username=f"alice_{uuid.uuid4().hex[:6]}", store="A", role="admin")
    alice.set_password("x")
    db_session.add(alice)
    db_session.commit()
    n = _mk_checklist_note(db_session, store="A")
    item = _mk_item(db_session, n)
    item.locked_by = alice.id  # 別人 lock
    item.locked_at = datetime.utcnow()
    db_session.commit()
    res = logged_in_admin_client.patch(f'/notes/api/items/{item.id}',
                                        json={"is_checked": True})
    assert res.status_code == 200
    db_session.refresh(item)
    assert item.is_checked is True


def test_create_item_does_not_require_note_lock(app, db_session, logged_in_admin_client, admin_user):
    """新增 item 不檢 note-lock — 即使別人 hold note lock 仍可新增。"""
    admin_user.store = "A"
    db_session.commit()
    alice = User(username=f"alice_{uuid.uuid4().hex[:6]}", store="A", role="admin")
    alice.set_password("x")
    db_session.add(alice)
    db_session.commit()
    n = _mk_checklist_note(db_session, store="A")
    n.locked_by = alice.id  # 別人 lock 整 note
    n.locked_at = datetime.utcnow()
    db_session.commit()
    res = logged_in_admin_client.post(f'/notes/api/{n.id}/items',
                                       json={"text": "new"})
    assert res.status_code == 200


def test_checklist_item_json_includes_locked_by_username(app, db_session):
    from notes.routes import _checklist_item_json
    from flask_login import login_user
    alice, bob = _mk_users(db_session)
    n = _mk_checklist_note(db_session)
    item = _mk_item(db_session, n)
    item.locked_by = alice.id
    item.locked_at = datetime.utcnow()
    db_session.commit()
    # 模擬 bob 視角
    with app.test_request_context():
        login_user(bob)
        data = _checklist_item_json(item)
        assert data["locked_by_username"] == alice.username
    # 模擬 alice 視角（自己持鎖、視為未鎖）
    with app.test_request_context():
        login_user(alice)
        data = _checklist_item_json(item)
        assert data["locked_by_username"] is None
```

- [ ] **Step 2: 跑新 test**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/test_checklist_item_lock.py -v 2>&1 | tail -20
```

Expected: 10 passed。如果有 fail：
- `404 / route not found` → 確認 Task 3 endpoint 已 commit
- `KeyError 'locked_by_username'` → 確認 Task 5 已 commit
- fixture 問題 → 對照 `tests/test_notes_lock.py` 的 fixture 用法

- [ ] **Step 3: 跑全 suite 確認沒打破其他 test**

```bash
PYTHONPATH=. ../venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: 0 failed（既有 408 + 新 10 = 418 passed）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_checklist_item_lock.py
git -c commit.gpgsign=false commit -m "test(notes): item-level lock backend tests (F2 task 10)

10 條 test：4 helper unit + 2 endpoint + 4 mutation/json behavior。
驗 acquire/refresh/reject/expired-takeover/release/heartbeat、
PATCH text 走 item-lock 但 is_checked 不檢、POST item 不需 note-lock、
_checklist_item_json 含 locked_by_username。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Local manual verification（user 跑）

**Files:** 無新檔、user 手動驗證

按 [[feedback_local_test_before_push]] 流程。Server 已在跑（前面 background）。

- [ ] **Step 1: 開兩個 incognito tab、用兩個 seed user 登入**

- Tab A: `http://localhost:5000/dev` 點 user alice 登入
- Tab B: `http://localhost:5000/dev` 點 user bob 登入（用 incognito 避免 session 衝突）

- [ ] **Step 2: 找/建一個 checklist note 兩人都進編輯頁**

如沒既有 checklist note：alice 在 `/notes/new?type=checklist` 建一個、加 5 個 item。Bob reload list 看到。

Tab A: 進 alice 建的 checklist note edit page
Tab B: 進同 note edit page

- [ ] **Step 3: 驗單 item lock**

Tab A → 點 item 1 → dialog 開啟
Tab B（< 1s 內）→ 看 item 1 變灰底 + 🔒 alice chip
Tab B → 點 item 1 → toast「alice 編輯中、5 分鐘無動作後可接手」
Tab B → 點 item 3 → 正常開 dialog

- [ ] **Step 4: 驗 unlock 即時同步**

Tab A → 在 item 1 dialog 內按 save（或 cancel）
Tab B（< 1s 內）→ 看 item 1 灰底/chip 消失、可點開

- [ ] **Step 5: 驗 new + delete + is_checked 不互卡**

Tab A → 開 item 2 dialog 編輯（hold lock）
Tab B → 新增一個 item → 應成功
Tab B → 勾選 item 5 完成 → 應成功
Tab B → 刪除 item 4 → 應成功
Tab B → 試 delete item 2 → 應失敗（toast 提示）
Tab B → reload list → item 2 仍存在 + 灰底 + chip

- [ ] **Step 6: 驗 lock-banner 在 checklist mode 不顯示**

Tab B → 進 checklist note edit page → 確認頂部**沒** lock-banner（即使理論上 alice 有 hold item lock、整 note 不該被 readonly）

對照：開一個 general note (`/notes/new?type=note`)、確認 lock-banner 仍存在（行為不變）

- [ ] **Step 7: 驗 expired lock takeover**

Tab A → 開 item dialog → 不動 5 分鐘（或修改 DB 手動把 locked_at 改 6 分鐘前）→ Tab B 點 item → 應成功 acquire

DevTools Console 快速改 DB：
```sql
-- 從本機 sqlite (dev.db)
UPDATE checklist_items SET locked_at = datetime('now', '-6 minutes') WHERE id = ?;
```
或重啟 SQLite 直接看 row。

- [ ] **Step 8: User 確認 OK 才算 done**

---

## Self-Review 對照 spec

| Spec 段落 | 對應 Task |
|---|---|
| §1 Schema (locked_by + locked_at) | Task 1 |
| §2 Helpers (4 個) | Task 2 |
| §3 API endpoints (heartbeat + release) | Task 3 |
| §4 重整 endpoint table (5 條保留 + 3 條 modify) | Task 4 + Task 5 + (attachment 既有不動) |
| §5 `_checklist_item_json` lock 資訊 | Task 5 |
| §6 Note-level lock 在 checklist mode 行為改動 | Task 9 |
| §7 List render lock 視覺 + CSS | Task 6 |
| §8 Modal flow heartbeat / release | Task 7 |
| §9 showToast helper | Task 6 |
| §10 List click handler | Task 6 |
| §11 WebSocket broadcast 'il' | Task 2 (server helper) + Task 3-5 (emit) + Task 8 (client listener) |
| §12 Lock-banner checklist mode 隱藏 | Task 9 |
| 邊界情境表 | Task 10 覆蓋 + Task 11 manual |
| Capacity check | spec 內、純加 2 columns、無 prod 壓力 |

**Type consistency check**:
- `_item_lock_status` / `_acquire_item_lock` / `_ensure_item_lock_for_mutation` / `_broadcast_item_lock` 在 Task 2-5 + Task 10 用法一致
- `locked_by_username` field 名稱在 Task 5 / Task 6 / Task 8 / Task 10 全部對齊
- `op: 'il'` event 名稱在 Task 2 / Task 3 / Task 4 / Task 5 / Task 8 一致

**Placeholder scan**: 無 TODO / TBD、所有 step 含完整 code、commit message 含 task 編號。

---

## 不在這個 plan 內

- 不 ff-merge to main（按 chain 策略累積到 F1 branch 後才一起 push）
- 不 push origin
- 不還原 .env.local OWM key（gitignored、無 push 影響）
- 不更新 project memory（push 後再寫紀錄）
