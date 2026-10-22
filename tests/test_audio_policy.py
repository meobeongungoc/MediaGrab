import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from mediagrab.core.options import build_options
from mediagrab.core.podcast import download_feed
from mediagrab.core.errors import MediaGrabError


def test_mp3_removed_from_options():
    with pytest.raises(ValueError):
        build_options(Path('out'), 'mp3', 'compat', Path('tools'))


def test_cli_no_mp3_flag(capsys):
    from mediagrab.cli import parser
    help_text = parser().format_help()
    assert '--mp3' not in help_text and '--audio' in help_text
    with pytest.raises(SystemExit):
        parser().parse_args(['https://youtu.be/example', '--mp3'])


def test_old_worker_job_rejected_before_any_download(monkeypatch, tmp_path):
    from mediagrab import service
    monkeypatch.setattr(service, 'download_one', Mock(side_effect=AssertionError('Must not download')))
    with pytest.raises(MediaGrabError, match='conversion has been removed'):
        service.execute_job({'kind':'single','url':'https://youtu.be/example','mode':'mp3','output':str(Path.cwd() / 'downloads')})


@pytest.mark.parametrize('old', ['MP3', 'Âm thanh gốc', 'mp3', 'audio'])
def test_saved_audio_settings_migrate_to_best_audio(old, monkeypatch, tmp_path):
    from mediagrab import config
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'mode':old,'quality':'MP4 tương thích','browser':'Không đăng nhập','output':'D:/MyMedia'}), encoding='utf-8')
    monkeypatch.setattr(config, 'CONFIG', settings)
    loaded = config.load_settings()
    assert loaded == {'mode':'Best audio','quality':'Compatible MP4','browser':'No login','output':'D:/MyMedia'}


def test_original_mp3_source_is_preserved_not_transcoded(tmp_path):
    from yt_dlp import YoutubeDL
    from mediagrab.core.downloader import SafeLogger
    options = build_options(tmp_path, 'audio', 'compat', tmp_path, 'soundcloud')
    options.update(logger=SafeLogger(), skip_download=True)
    with YoutubeDL(options) as ydl:
        info = ydl.process_ie_result({'id':'test','title':'Native MP3','extractor':'test','formats':[{'format_id':'download','url':'https://example.com/original.mp3','ext':'mp3','vcodec':'none','acodec':'mp3','abr':320}]}, download=False)
    assert info['ext'] == 'mp3'
    assert options['postprocessors'][0]['preferredcodec'] == 'best'
    assert all('preferredquality' not in pp for pp in options['postprocessors'])


def test_rss_conversion_rejected(tmp_path):
    with pytest.raises(MediaGrabError, match='Best audio'):
        download_feed(None, set(), tmp_path, 'mp3', tmp_path)
