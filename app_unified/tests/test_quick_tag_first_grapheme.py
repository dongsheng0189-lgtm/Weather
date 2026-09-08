"""快速標籤 compact 按鈕的「首字」必須是一個完整的字形叢集（grapheme）。

背景：舊版用 `tag.name.charAt(0)`，取的是一個 UTF-16 code unit。
標籤名如果以 emoji 開頭（🔥、🇹🇼、👨‍👩‍👦 都在 BMP 之外，是代理對），
charAt(0) 只會拿到半個代理對（\\ud83d），瀏覽器畫不出來 → 使用者看到「?」。
2026-09-08 現場回報過這個症狀。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

EDITOR = Path(__file__).resolve().parent.parent / 'templates' / 'notes' / 'editor.html'

pytestmark = pytest.mark.skipif(
    shutil.which('node') is None, reason='需要 node 才能實際執行這段前端函式'
)


def _extract_fn(name):
    """從模板裡把某個 function 的原始碼整段挖出來（大括號配對）。"""
    src = EDITOR.read_text(encoding='utf-8')
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


def _run(names, segmenter=True):
    # segmenter=False 模擬沒有 Intl.Segmenter 的舊瀏覽器，逼函式走 fallback。
    # 兩條路徑都要驗 —— 只驗一條的話另一條壞掉不會有人知道。
    prelude = '' if segmenter else 'delete Intl.Segmenter;\n'
    script = (prelude + _extract_fn('firstGrapheme')
              + '\nconsole.log(JSON.stringify(%s.map(firstGrapheme)));' % json.dumps(names))
    out = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


@pytest.mark.parametrize('segmenter', [True, False], ids=['Intl.Segmenter', 'fallback'])
def test_both_code_paths_agree(segmenter):
    """主路徑與 fallback 對同一批輸入必須給一樣的答案。"""
    names = ['🔥急件', '🇹🇼台灣', '👨‍👩‍👦家庭', '⚠️注意', '1️⃣第一', '👍🏽讚', '急件', 'Urgent']
    assert _run(names, segmenter=segmenter) == \
        ['🔥', '🇹🇼', '👨‍👩‍👦', '⚠️', '1️⃣', '👍🏽', '急', 'U']


def test_first_grapheme_keeps_emoji_whole():
    """代理對不可以被切一半 —— 這正是使用者看到「?」的成因。"""
    got = _run(['🔥急件', '🇹🇼台灣', '👨‍👩‍👦家庭', '⚠️注意', '1️⃣第一', '👍🏽讚'])
    assert got == ['🔥', '🇹🇼', '👨‍👩‍👦', '⚠️', '1️⃣', '👍🏽'], got
    for g in got:
        assert not any(0xD800 <= ord(c) <= 0xDFFF for c in g), '拿到落單的代理對：%r' % g


def test_first_grapheme_plain_text_unchanged():
    """一般中英文字的行為必須跟舊版 charAt(0) 完全一樣，不可回歸。"""
    assert _run(['急件', 'Urgent', '★重點', '3C維修', ' 前面有空白']) == \
        ['急', 'U', '★', '3', ' ']


def test_first_grapheme_handles_empty_and_missing():
    """空字串 / null 不可爆掉（標籤名理論上不會空，但按鈕渲染不該因此整段掛掉）。"""
    script = _extract_fn('firstGrapheme') + \
        '\nconsole.log(JSON.stringify([firstGrapheme(""), firstGrapheme(null), firstGrapheme(undefined)]));'
    out = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip()) == ['', '', '']
