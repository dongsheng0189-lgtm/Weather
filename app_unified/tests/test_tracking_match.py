"""建檔追蹤的比對純函式 —— 不碰 DB、不需要 app fixture。"""
from notes.tracking_match import normalize, text_matches, find_hits


def test_normalize_strips_and_lowers():
    assert normalize('  Wang XiaoMing  ') == 'wang xiaoming'
    assert normalize(None) == ''
    assert normalize('') == ''


def test_text_matches_is_case_insensitive_substring():
    n = normalize('王小明')
    assert text_matches(n, '今天王小明遲到 10 分鐘') is True
    assert text_matches(n, '今天大家都準時') is False


def test_text_matches_ignores_case_for_ascii():
    n = normalize('Wang')
    assert text_matches(n, 'WANG 打卡異常') is True
    assert text_matches(n, 'wang 打卡異常') is True


def test_text_matches_handles_none_text():
    assert text_matches(normalize('王'), None) is False


def test_text_matches_empty_needle_never_matches():
    # 空關鍵字若當成子字串會命中全部資料，必須明確擋掉
    assert text_matches('', '任何文字') is False


def test_find_hits_returns_matching_records_in_order():
    records = [
        ('note', 1, ['王小明的交接', '內容無關']),
        ('item', 7, ['今天準時']),
        ('item', 9, [None, '王小明遲到']),
    ]
    assert find_hits('王小明', records) == [('note', 1), ('item', 9)]


def test_find_hits_matches_each_record_only_once():
    # 同一筆資料的多個欄位都命中，也只能回傳一次
    records = [('note', 1, ['王小明', '王小明又一次'])]
    assert find_hits('王小明', records) == [('note', 1)]


def test_find_hits_with_blank_keyword_returns_nothing():
    records = [('note', 1, ['任何文字'])]
    assert find_hits('   ', records) == []
