"""tag_palette helper tests."""
from notes.tag_palette import PALETTE, is_valid_color, pick_fg


def test_palette_has_10_colors():
    assert len(PALETTE) == 10
    assert all(c.startswith('#') and len(c) == 7 for c in PALETTE)


def test_is_valid_color_accepts_palette_colors():
    for c in PALETTE:
        assert is_valid_color(c) is True


def test_is_valid_color_rejects_others():
    assert is_valid_color('#000000') is False
    assert is_valid_color('red') is False
    assert is_valid_color(None) is False
    assert is_valid_color('') is False


def test_pick_fg_light_bg_returns_black():
    # 黃 / 琥珀 = light → 黑字
    assert pick_fg('#F1C40F') == '#000'
    assert pick_fg('#F39C12') == '#000'


def test_pick_fg_dark_bg_returns_white():
    # 紅 / 深藍 = dark → 白字
    assert pick_fg('#E74C3C') == '#fff'
    assert pick_fg('#2C3E50') == '#fff'


def test_pick_fg_invalid_returns_white():
    assert pick_fg('not-a-hex') == '#fff'
    assert pick_fg(None) == '#fff'
    assert pick_fg('#FFF') == '#fff'
