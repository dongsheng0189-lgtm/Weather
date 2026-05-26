"""Tag color palette + auto fg picker."""

PALETTE = [
    "#E74C3C",  # 紅
    "#C0392B",  # 深紅 / 酒紅
    "#E67E22",  # 橘
    "#D35400",  # 深橘
    "#F39C12",  # 琥珀
    "#F1C40F",  # 黃
    "#2ECC71",  # 淡綠
    "#27AE60",  # 綠
    "#16A085",  # 青
    "#1ABC9C",  # 薄荷
    "#3498DB",  # 藍
    "#2980B9",  # 海軍藍
    "#2C3E50",  # 深藍
    "#9B59B6",  # 紫
    "#E91E63",  # 粉
    "#7F8C8D",  # 灰
]


def is_valid_color(hex_str: str) -> bool:
    return isinstance(hex_str, str) and hex_str in PALETTE


def pick_fg(bg_hex: str) -> str:
    """Returns '#000' for light bg, '#fff' for dark bg (YIQ luminance approx)."""
    if not isinstance(bg_hex, str) or len(bg_hex) != 7 or not bg_hex.startswith('#'):
        return '#fff'
    try:
        r = int(bg_hex[1:3], 16)
        g = int(bg_hex[3:5], 16)
        b = int(bg_hex[5:7], 16)
    except ValueError:
        return '#fff'
    yiq = (r * 299 + g * 587 + b * 114) / 1000
    return '#000' if yiq >= 160 else '#fff'
