"""併發去重 + 「缺少 note_id」診斷 log 的接線斷言 (2026-07-10).

背景：一般 notes checklist 項目 modal 上傳圖偶發回「缺少 note_id」400，flaky、
prod-only、本機無法重現。逐一讀碼三條上傳路徑都堵住了 null 窗口 → 真正觸發時序
無法從碼構造。故：
  (1) 後端 400 分支加診斷 log，下次再發即有原始 note_id 實證。
  (2) 順手補 ensureNoteExists 的 in-flight guard，修掉「新筆記雙擊完成 / 快速連新增
      項目」併發打兩次 POST /notes/api 建重複筆記的實錘 bug。

純接線斷言（同專案 wiring test 慣例）；瀏覽器併發行為另行本機/手動驗。
"""
import io
from pathlib import Path

ROOT = Path(__file__).parent.parent
EDITOR = ROOT / 'templates' / 'notes' / 'editor.html'
ROUTES = ROOT / 'notes' / 'routes.py'


def _ensure_note_body(src):
    """擷取 ensureNoteExists() 函式 body（到下一個 top-level `function ` 為止）。"""
    start = src.index('function ensureNoteExists()')
    rest = src[start + len('function ensureNoteExists()'):]
    nxt = rest.find('\nfunction ')
    return rest if nxt == -1 else rest[:nxt]


def test_ensure_note_exists_has_inflight_guard():
    """新筆記併發呼叫 ensureNoteExists 只能建一篇：共用 in-flight promise 去重。"""
    src = EDITOR.read_text(encoding='utf-8')
    # 模組層共用的 in-flight promise
    assert '_ensureNotePromise' in src
    body = _ensure_note_body(src)
    # 已有 in-flight 就直接回傳同一個 promise（不再 POST 第二次）
    assert 'if (_ensureNotePromise) return _ensureNotePromise' in body


def test_ensure_note_promise_cleared_after_settle():
    """settle 後要清掉，失敗可重試、成功後續呼叫走 NOTE_ID 短路。"""
    src = EDITOR.read_text(encoding='utf-8')
    body = _ensure_note_body(src)
    assert '_ensureNotePromise = null' in body


def test_upload_missing_note_id_logs_raw_value():
    """400『缺少 note_id』分支要記下原始 note_id 值（診斷用）。"""
    src = ROUTES.read_text(encoding='utf-8')
    assert '[note_id-missing]' in src


def test_upload_bad_note_id_returns_400_and_logs(logged_in_client, capsys):
    """實打：note_id 送非數字（重現前端 FormData 把 JS null 轉成 "null"）→
    回 400『缺少 note_id』，且診斷 log 印出原始值、不 crash（log 自身若 throw 會變 500）。"""
    resp = logged_in_client.post(
        '/notes/api/attachments/upload',
        data={'note_id': 'null', 'file': (io.BytesIO(b'x'), 'a.jpg')},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400
    assert '缺少 note_id' in resp.get_json()['message']
    out = capsys.readouterr().out
    assert '[note_id-missing]' in out
    assert "raw note_id='null'" in out
