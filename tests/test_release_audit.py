"""Regression cases found during the release-completion audit."""
import logging
from pathlib import Path
import pytest
from mediagrab.core.errors import MediaGrabError
from mediagrab.core.webpage import page_entries


def test_query_identifies_two_different_embedded_videos():
    def raw(number):
        return {'id': 'media', 'title': f'Video {number}', 'webpage_url': 'https://forum.example.com/thread',
                'formats': [{'url': f'https://cdn.example.com/video.mp4?id={number}&token=SECRET', 'ext': 'mp4'}]}
    assert len(page_entries({'entries': [raw(1), raw(2)]})) == 2


def test_generic_fallback_is_not_logged_as_download_failure(caplog):
    from mediagrab.core.downloader import SafeLogger
    with caplog.at_level(logging.WARNING):
        SafeLogger().warning('[generic] Falling back on generic information extractor')
    assert 'Download failed' not in caplog.text


def test_empty_podcast_selection_rejected_before_network(monkeypatch):
    from mediagrab import service
    monkeypatch.setattr(service, 'load_session', lambda *a: pytest.fail('No browser or network work for invalid jobs'))
    with pytest.raises(MediaGrabError, match='Select'):
        service.execute_job({'kind': 'podcast', 'url': 'https://example.com/feed', 'output': 'D:/Downloads', 'ids': [], 'mode': 'audio'})


@pytest.mark.parametrize('kind,quality', [('typo', 'compat'), ('single', 'invalid')])
def test_invalid_job_options_rejected_before_network(monkeypatch, kind, quality):
    from mediagrab import service
    monkeypatch.setattr(service, 'load_session', lambda *a: pytest.fail('No network for invalid jobs'))
    with pytest.raises(MediaGrabError):
        service.execute_job({'kind': kind, 'quality': quality, 'url': 'https://youtu.be/test', 'output': 'D:/Downloads'})


def test_expiring_query_signatures_do_not_change_media_identity():
    from mediagrab.core.webpage import media_identity
    def raw(signature):
        return {'formats': [{'url': f'https://cdn.example.com/play?id=12&X-Amz-Signature={signature}&expires=12'}]}
    assert media_identity(raw('old')) == media_identity(raw('new'))


def test_selected_audio_must_not_silently_disappear(tmp_path, monkeypatch):
    import shutil
    import subprocess
    from mediagrab.core import downloader
    tools = Path(__file__).resolve().parents[1]
    silent = tmp_path / 'silent.mp4'
    subprocess.run([str(tools / 'ffmpeg.exe'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10', '-t', '0.5', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(silent)], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    class FakeYDL:
        def __init__(self, options):
            self.params = dict(options)
            self.params['outtmpl'] = {'default': options['outtmpl']}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, *args, **kwargs):
            return {'id': 'test', 'title': 'Audio expected', 'acodec': 'aac', 'vcodec': 'h264'}
        def process_ie_result(self, info, download):
            target = Path(self.params['outtmpl']['default'].replace('%(ext)s', 'mp4'))
            shutil.copyfile(silent, target)
            self.params['post_hooks'][0](str(target))
    monkeypatch.setattr(downloader, 'YoutubeDL', FakeYDL)
    with pytest.raises(MediaGrabError, match='no audio stream'):
        downloader.download_one('https://youtu.be/test', tmp_path / 'out', 'mp4', 'compat', tools)
    assert not list((tmp_path / 'out').glob('*.reserve'))


def test_audio_presence_in_separate_formats():
    from mediagrab.core.downloader import source_has_audio
    assert source_has_audio({'requested_formats': [{'vcodec': 'avc1', 'acodec': 'none'}, {'vcodec': 'none', 'acodec': 'mp4a'}]})
    assert not source_has_audio({'vcodec': 'avc1', 'acodec': 'none'})
