"""Wiring: 通用浮動返回鈕（取代 review-back-widget）。"""
from pathlib import Path

EDITOR = Path(__file__).parent.parent / 'templates' / 'notes' / 'editor.html'


def test_float_back_widget_present():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'id="float-back-widget"' in src


def test_float_back_mirrors_btn_back_and_uses_observer():
    src = EDITOR.read_text(encoding='utf-8')
    assert "getElementById('btn-back')" in src
    assert 'IntersectionObserver' in src


def test_old_review_back_widget_removed():
    src = EDITOR.read_text(encoding='utf-8')
    assert 'review-back-widget' not in src


def test_review_mode_label_preserved():
    src = EDITOR.read_text(encoding='utf-8')
    assert '← 返回摘要' in src
