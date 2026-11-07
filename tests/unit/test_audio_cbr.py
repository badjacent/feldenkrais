import shutil
import subprocess

import pytest

from kb.audio import ensure_cbr_mp3, is_vbr_mp3

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _tone(path, *quality):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:a", "libmp3lame", *quality, str(path)],
        check=True, capture_output=True,
    )
    return str(path)


def test_vbr_and_cbr_mp3_are_told_apart(tmp_path):
    assert is_vbr_mp3(_tone(tmp_path / "vbr.mp3", "-q:a", "0"))
    assert not is_vbr_mp3(_tone(tmp_path / "cbr.mp3", "-b:a", "192k"))


def test_vbr_mp3_is_reencoded_to_cbr(tmp_path):
    vbr = _tone(tmp_path / "lesson.mp3", "-q:a", "0")
    result = ensure_cbr_mp3(vbr)
    assert result != vbr
    assert result.endswith(".cbr.mp3")
    assert not is_vbr_mp3(result)


def test_cbr_mp3_is_left_alone(tmp_path):
    cbr = _tone(tmp_path / "lesson.mp3", "-b:a", "192k")
    assert ensure_cbr_mp3(cbr) == cbr


def test_non_mp3_audio_becomes_cbr_mp3(tmp_path):
    m4a = tmp_path / "lesson.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:a", "aac", str(m4a)],
        check=True, capture_output=True,
    )
    result = ensure_cbr_mp3(str(m4a))
    assert result.endswith(".mp3")
    assert not is_vbr_mp3(result)
