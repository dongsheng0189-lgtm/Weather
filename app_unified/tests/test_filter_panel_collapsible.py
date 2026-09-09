"""Filter panel collapsible wrapper markup."""
import json as _json
import uuid
import pytest


@pytest.fixture
def db(db_session):
    from extensions import db as _db
    return _db


def _mk_user(db):
    from models import User
    u = User(username=f'u_fp_{uuid.uuid4().hex[:6]}', role='user', store='B店',
             features=_json.dumps(['notes']))
    u.set_password('0000')
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user.id)
        sess['_fresh'] = True


def test_filter_panel_wrapper_present(client, db):
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/').get_data(as_text=True)
    assert 'data-section="filters"' in html
    assert '進階篩選' in html


def test_filter_panel_contains_existing_filter_rows(client, db):
    u = _mk_user(db)
    _login(client, u)
    html = client.get('/notes/').get_data(as_text=True)
    assert 'range-tabs' in html or '時間' in html
    assert '種類' in html
    assert '班別' in html


# ─────────────────────────────────────────────────────────────
# 展開判斷的實際行為（2026-09-09）
#
# 這裡壞掉過的是「判斷順序」——舊版把 activeCount 排在 stored 之前，
# 於是使用者收起面板的動作每次載入都被蓋掉（現場回報：關起來滑掉重開還是開的）。
# 觸發鏈：localStorage 存過 notes_sort='created' → index.html 頁首的 redirect
# script 每次都把 ?sort=created 補回 URL → active_filter_count 永遠 >= 1。
#
# 這種順序錯誤，上面那種原始碼字串比對驗不到，所以改用 node 實際執行該函式
# （手法沿用 test_quick_tag_first_grapheme.py 建立的先例）。
# ─────────────────────────────────────────────────────────────
import json
import shutil
import subprocess
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / 'static' / 'js' / 'collapsible_sections.js'

_needs_node = pytest.mark.skipif(
    shutil.which('node') is None, reason='需要 node 才能實際執行這段前端函式'
)


def _extract_fn(name):
    """把某個 function 的原始碼整段挖出來（大括號配對）。"""
    src = JS.read_text(encoding='utf-8')
    head = 'function %s(' % name
    start = src.index(head)
    i = src.index('{', start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError('function %s 的大括號沒有配對' % name)


def _decide(cases):
    """cases: [[activeCount, stored], ...] → [bool, ...]"""
    script = (_extract_fn('decideFilterExpanded')
              + '\nconsole.log(JSON.stringify(%s.map(function(c){'
                'return decideFilterExpanded(c[0], c[1]);})));' % json.dumps(cases))
    out = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


@_needs_node
def test_explicit_collapse_survives_active_filter():
    """本次修的 bug 本體：使用者明確收起來後，即使 URL 帶著 active filter 也要保持收起。

    還原成舊的判斷順序（activeCount 排第一）這條就會紅。
    """
    assert _decide([[1, '0']]) == [False]
    assert _decide([[3, '0']]) == [False]


@_needs_node
def test_full_truth_table():
    """六種組合一次驗完，避免只修好一格、弄壞另一格。"""
    cases = [
        [0, '0'],    # 收起來過、沒篩選 → 收起
        [1, '0'],    # 收起來過、有篩選 → 收起（修正重點）
        [0, '1'],    # 展開過、沒篩選 → 展開
        [1, '1'],    # 展開過、有篩選 → 展開
        [0, None],   # 沒表達過、沒篩選 → 收起（預設）
        [1, None],   # 沒表達過、有篩選 → 展開（幫使用者看見作用中的篩選）
    ]
    assert _decide(cases) == [False, False, True, True, False, True]
