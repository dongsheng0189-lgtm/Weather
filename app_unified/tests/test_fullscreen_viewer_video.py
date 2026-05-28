"""Video slide: poster + center play button + pause-on-leave."""
from pathlib import Path

VIEWER_JS = Path(__file__).parent.parent / 'static' / 'js' / 'fullscreen_viewer.js'


def test_video_slide_markup():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'fs-video-wrap' in src
    assert 'fs-video-play' in src
    assert 'playsinline' in src
    assert 'preload="metadata"' in src


def test_play_button_handler():
    src = VIEWER_JS.read_text(encoding='utf-8')
    # play button binding inside _fsInit or per-slide
    assert 'fs-video-play' in src
    assert '.play()' in src
    assert 'video.controls = true' in src
    assert "classList.add('playing')" in src


def test_pause_and_reset_video():
    src = VIEWER_JS.read_text(encoding='utf-8')
    assert 'function _pauseAndResetVideo(' in src
    assert '.pause()' in src
    assert 'currentTime = 0' in src
    assert 'video.controls = false' in src
    assert "classList.remove('playing')" in src


def test_close_pauses_all_videos():
    """closeFullscreen should pause all video slides before clearing."""
    src = VIEWER_JS.read_text(encoding='utf-8')
    # closeFullscreen body iterates _items and calls _pauseAndResetVideo
    close_section = src.split('function closeFullscreen')[1].split('function ')[0]
    assert '_pauseAndResetVideo' in close_section
