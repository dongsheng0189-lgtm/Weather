"""共用 helper：從 editor.html 現讀 `?from=` 白名單裡「建檔追蹤表」那條
regex，轉成 Python `re.Pattern`。

被 test_editor_from_wiring.py（A3：正／反面表列測試）與
test_tracking_ui_wiring.py（A1：店名帶空格的迴歸測試）共用——兩邊都需要拿
「目前檔案裡實際生效的那條 regex」去驗證，而不是在測試裡另外抄一份字面
pattern。抄一份的話，改了 regex 忘記同步兩邊測試，各自過時但仍然「相等」，
就完全測不出真正的行為（跟這次 A1/A2/A4 要修的那幾個「兩邊一起錯還互相對
得上」是同一種失效模式）。
"""
import re
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'

_JS_LITERAL_RE = re.compile(
    r"var isTracking = !isSummary && /(?P<pattern>.+)/\.test\(from\);"
)


def tracking_from_pattern():
    """回傳 editor.html 裡 isTracking 白名單 regex 目前的字面樣式，已轉成
    Python `re.Pattern`。

    JS 的 `/pattern/.test()` 在字串結尾有換行字元時，`$`（沒有 `m` flag）
    只認絕對結尾、不會在換行前面也算匹配；Python 的 `$`（沒有
    `re.MULTILINE`）則會在「結尾換行之前」也算匹配一次，兩者語意不同。這裡
    把抽出來的 pattern 結尾 `$` 換成 `\\Z`，讓 Python 版本的比對結果跟 JS
    版本一致——不然拿這個 pattern 去測「結尾帶換行的值該被拒絕」，會回報一
    個 JS 端其實不存在的失敗（regex 本身沒問題，錯的是測試移植的方式）。
    """
    src = EDITOR.read_text(encoding='utf-8')
    m = _JS_LITERAL_RE.search(src)
    assert m, (
        '找不到 isTracking 的白名單 regex 字面值——'
        "`var isTracking = !isSummary && /.../.test(from);` 這行的寫法可能被改掉了"
    )
    js_pattern = m.group('pattern')
    assert js_pattern.endswith('$'), f'預期 regex 以 $ 結尾，實際是 {js_pattern!r}'
    return re.compile(js_pattern[:-1] + r'\Z')
