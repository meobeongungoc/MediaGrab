from pathlib import Path
import logging
from copy import deepcopy

import pytest
from yt_dlp import YoutubeDL
from yt_dlp.utils import ExtractorError

from mediagrab.core.options import build_options
from mediagrab.core.downloader import validate_url, SafeLogger
from mediagrab.core.errors import MediaGrabError, explain_error


def choose(mode, quality, formats, source="youtube"):
    options = build_options(Path("out"), mode, quality, Path("tools"), source)
    options.update(logger=SafeLogger(), skip_download=True)
    with YoutubeDL(options) as ydl:
        return ydl.process_ie_result({"id": "test", "title": "Tiếng Việt", "extractor": "test", "formats": deepcopy(formats)}, download=False)


def fmt(id, video="none", audio="none", height=None, ext="mp4", abr=None):
    return dict(format_id=id, url=f"https://example.com/{id}", vcodec=video,
                acodec=audio, height=height, ext=ext, abr=abr)


FORMATS = [fmt("h264", "avc1.640028", height=1080),
           fmt("vp9", "vp9", height=2160, ext="webm"),
           fmt("aac", audio="mp4a.40.2", ext="m4a", abr=128),
           fmt("opus", audio="opus", ext="webm", abr=160)]


def test_compatible_does_not_pick_4k_vp9():
    info = choose("mp4", "compat", FORMATS)
    assert [f["format_id"] for f in info["requested_formats"]] == ["h264", "aac"]


def test_best_preserves_4k():
    info = choose("mp4", "best", FORMATS)
    assert info["height"] == 2160


def test_compatible_refuses_incompatible_fallback():
    with pytest.raises(ExtractorError):
        choose("mp4", "compat", [FORMATS[1], FORMATS[3]])


def test_compatible_combined_fallback():
    assert choose("mp4", "compat", [fmt("combined", "avc1", "mp4a", 720)])["format_id"] == "combined"


def test_soundcloud_original_preferred():
    formats = [fmt("download", audio="flac", ext="flac"), FORMATS[3]]
    assert choose("audio", "compat", formats, "soundcloud")["format_id"] == "download"


@pytest.mark.parametrize("mode,quality", [("mp4", "best"), ("mp4", "compat"), ("audio", "compat")])
def test_no_video_reencoding_or_cookies(mode, quality):
    options = build_options(Path("out"), mode, quality, Path("tools"))
    assert "cookiefile" not in options and "cookiesfrombrowser" not in options
    assert not any(pp["key"] == "FFmpegVideoConvertor" for pp in options["postprocessors"])
    if mode == "audio":
        assert options["postprocessors"][0]["preferredcodec"] == "best"


@pytest.mark.parametrize("url", ["https://youtube.com/playlist?list=123", "https://youtube.com/@someone", "https://soundcloud.com/a/sets/b", "file:///cookies.txt", "https://youtube.com.evil.test/watch?v=x", "https://user:secret@youtube.com/watch?v=x"])
def test_rejects_non_single_or_unsafe_inputs(url):
    with pytest.raises(MediaGrabError):
        validate_url(url)


def test_public_video_with_playlist_keeps_single_video():
    assert validate_url("https://www.youtube.com/watch?v=123&list=456") == "youtube"


@pytest.mark.parametrize("message", ["HTTP Error 403 token=SECRET", "Private video cookie=SECRET", "unavailable SECRET", "requested format SECRET", "random SECRET"])
def test_errors_do_not_expose_raw_credentials(message, caplog):
    assert "SECRET" not in explain_error(message)
    with caplog.at_level(logging.WARNING):
        SafeLogger().warning(message)
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize("info", [
    {"_type": "playlist", "entries": []},
    {"id": "1", "is_live": True},
    {"id": "1", "live_status": "is_upcoming"},
    {"id": "1", "has_drm": True},
])
def test_preflight_blocks_download(info, tmp_path, monkeypatch):
    from mediagrab.core import downloader as module
    for executable in ("ffmpeg.exe", "ffprobe.exe", "deno.exe"):
        (tmp_path / executable).touch()

    class FakeYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            assert download is False
            return info

        def process_ie_result(self, *args, **kwargs):
            pytest.fail("No download allowed for rejected metadata")

    monkeypatch.setattr(module, "YoutubeDL", FakeYDL)
    with pytest.raises(MediaGrabError):
        module.download_one("https://youtu.be/example", tmp_path / "out", "mp4", "compat", tmp_path)


def test_missing_tools_fails_before_network(tmp_path, monkeypatch):
    from mediagrab.core import downloader as module
    monkeypatch.setattr(module, "YoutubeDL", lambda _: pytest.fail("Network must not start"))
    with pytest.raises(MediaGrabError, match="Missing"):
        module.download_one("https://youtu.be/example", tmp_path, "audio", "compat", tmp_path)


def test_soundcloud_rejects_video_output(tmp_path):
    with pytest.raises(ValueError, match="SoundCloud"):
        build_options(tmp_path, "mp4", "compat", tmp_path, "soundcloud")


def test_probe_warning_is_not_silently_discarded(tmp_path, monkeypatch):
    from mediagrab.core import downloader as module
    from subprocess import CompletedProcess
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: CompletedProcess([], 0, '{"streams": [{"codec_type": "audio"}]}', 'parser warning'))
    assert module.inspect_media(tmp_path / "sample.mkv", tmp_path)["inspection_warning"] is True


def test_probe_failure_not_reported_as_success(tmp_path, monkeypatch):
    from mediagrab.core import downloader as module
    from subprocess import CompletedProcess
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: CompletedProcess([], 1, '{}', 'error'))
    with pytest.raises(MediaGrabError):
        module.inspect_media(tmp_path / "sample.mkv", tmp_path)
