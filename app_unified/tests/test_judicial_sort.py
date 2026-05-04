"""Test judicial results 按關鍵字命中排序"""

from vetting.routes import _judicial_sort_key


def test_keyword_hits_first():
    cases = [
        {"id": "A", "keyword_hits": [], "id_match": True},
        {"id": "B", "keyword_hits": ["詐欺"], "id_match": False},
        {"id": "C", "keyword_hits": [], "id_match": None},
        {"id": "D", "keyword_hits": ["毒品", "搶劫"], "id_match": True},
    ]
    cases.sort(key=_judicial_sort_key)
    assert [c["id"] for c in cases] == ["D", "B", "A", "C"]


def test_stable_within_same_bucket():
    cases = [
        {"id": "A", "keyword_hits": ["詐欺"], "id_match": True},
        {"id": "B", "keyword_hits": ["洗錢"], "id_match": True},
    ]
    cases.sort(key=_judicial_sort_key)
    assert [c["id"] for c in cases] == ["A", "B"]


def test_empty_keyword_hits_means_no_hit():
    cases = [
        {"id": "A", "keyword_hits": [], "id_match": True},
        {"id": "B", "keyword_hits": ["詐欺"], "id_match": False},
    ]
    cases.sort(key=_judicial_sort_key)
    assert [c["id"] for c in cases] == ["B", "A"]
