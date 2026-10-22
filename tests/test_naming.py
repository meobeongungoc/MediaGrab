"""Title-only names, author metadata, collisions and folder migration."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from mediagrab.core.naming import recognize_music, collection_folder, reserve_name
from mediagrab.core.playlist import Playlist, Entry
from mediagrab.core import webpage
from test_webpage import local_forum


@pytest.mark.parametrize('title', ['Nghệ sĩ - Bài hát', 'Bài hát - Nghệ sĩ', 'Nghệ sĩ – Bài hát', 'Bài hát | Nghệ sĩ', 'Nghệ sĩ — Bài hát (Official Audio)'])
def test_artist_order_with_metadata(title):
    result = recognize_music({'title': title, 'artist': 'Nghệ sĩ'})
    assert result['title'] == 'Bài hát'
    assert result['artist'] == 'Nghệ sĩ'


def test_source_metadata_overrides_advertising_title():
    result = recognize_music({'title': 'Channel - unrelated display title', 'track': 'Song (Live)', 'artists': ['One', 'Two']})
    assert result == {'title': 'Song (Live)', 'artist': 'One, Two', 'method': 'source_metadata'}


def test_ambiguous_dash_and_uploader_are_not_artist_evidence():
    result = recognize_music({'title': 'A - B', 'uploader': 'A'})
    assert result['title'] == 'A - B' and result['artist'] == '' and result['needs_review']
    assert recognize_music({'title': 'A Song (Remix)', 'track': 'A Song (Remix)'})['title'] == 'A Song (Remix)'


def test_folder_owner_fallback_and_legacy(tmp_path):
    folder, old = collection_folder(tmp_path, 'Playlist', 'Channel', 'abc', 'audio-compat')
    assert folder.name == 'Channel' and not old
    fallback, _ = collection_folder(tmp_path, 'Playlist', '', 'abc', 'audio-compat')
    assert fallback.name == 'Playlist'
    legacy = tmp_path / 'Playlist [abc] - audio-compat'
    legacy.mkdir()
    assert collection_folder(tmp_path, 'Playlist', 'Channel', 'abc', 'audio-compat') == (legacy, True)


def test_reservations_and_existing_files_never_collide(tmp_path):
    (tmp_path / 'Song.mp4').write_bytes(b'keep')
    name, lock = reserve_name(tmp_path, 'Song')
    assert name == 'Song (2)'
    second, other = reserve_name(tmp_path, 'Song')
    assert second == 'Song (3)'
    lock.unlink(); other.unlink()
    assert (tmp_path / 'Song.mp4').read_bytes() == b'keep'


@pytest.mark.parametrize('artists,expected', [(['Artist', 'Artist'], 'Artist'), (['A', 'B'], 'Channel'), (['', ''], 'Channel')])
def test_discovery_common_artist_or_channel(monkeypatch, artists, expected):
    from mediagrab.core import playlist
    class FakeYDL:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, *a, **k):
            return {'_type': 'playlist', 'title': 'List', 'channel': 'Channel', 'entries': [
                {'id': str(i), 'title': 'Song', 'artist': artist, 'url': f'https://youtu.be/item{i}'} for i, artist in enumerate(artists)]}
    monkeypatch.setattr(playlist, 'YoutubeDL', FakeYDL)
    assert playlist.discover('https://youtube.com/playlist?list=PLtest').owner == expected


def test_zip_options_removed():
    from mediagrab.cli import parser
    assert '--zip' not in parser().format_help()
    with pytest.raises(SystemExit):
        parser().parse_args(['https://youtu.be/test', '--zip'])


def test_old_zip_job_saves_only_folder(monkeypatch, tmp_path):
    from mediagrab import service
    monkeypatch.setattr(service, 'load_session', lambda *a: None)
    monkeypatch.setattr(service, 'discover', lambda *a: Playlist('https://example.com', 'List', (Entry(1, '1', 'Song', 'https://youtu.be/test'),)))
    monkeypatch.setattr(service, 'download_playlist', lambda *a, **k: (tmp_path, [{'status': 'downloaded', 'file': 'Song.mp4'}]))
    assert service.execute_job({'kind': 'playlist', 'url': 'https://youtube.com/playlist?list=test', 'output': 'D:/Downloads', 'zip': True}) == 0
    assert not list(tmp_path.glob('*.zip'))


def test_real_title_only_and_artist_metadata(local_forum, tmp_path, monkeypatch):
    from mediagrab.core.downloader import download_one
    base, tools = local_forum
    monkeypatch.setattr(webpage, 'validate_web_url', lambda url: None)
    monkeypatch.setattr(webpage, 'check_public_host', lambda url: None)
    page = webpage.discover_page(base + '/forum2.html', tools)
    raw = deepcopy(page.entries[0].media)
    raw.update(title='Artist - Tên bài hát (Official Audio)', track='Tên bài hát', artist='Artist')
    a = download_one(page.entries[0].url, tmp_path, 'mp4', 'compat', tools, prepared_info=raw)
    assert a.path.name == 'Tên bài hát.mp4'
    tags = a.metadata['format']['tags']
    assert tags['title'] == 'Tên bài hát' and tags['artist'] == 'Artist'
    original_hash = hashlib.sha256(a.path.read_bytes()).hexdigest()
    b = download_one(page.entries[0].url, tmp_path, 'mp4', 'compat', tools, prepared_info=raw)
    assert b.path.name == 'Tên bài hát (2).mp4'
    assert hashlib.sha256(a.path.read_bytes()).hexdigest() == original_hash
    assert not list(tmp_path.glob('*.reserve'))


def test_podcast_tags_preserve_audio_packets(tmp_path):
    from mediagrab.core.podcast import tag_audio
    from mediagrab.core.downloader import inspect_media
    tools = Path(__file__).resolve().parents[1]
    source, target = tmp_path / 'source.m4a', tmp_path / 'Tên tập.m4a'
    subprocess.run([str(tools / 'ffmpeg.exe'), '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '0.5', '-c:a', 'aac', str(source)], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    tag_audio(source, target, 'Tên tập', 'Tác giả', tools)
    tags = inspect_media(target, tools)['format']['tags']
    assert tags['title'] == 'Tên tập' and tags['artist'] == 'Tác giả'
    def packets(path):
        r = subprocess.run([str(tools / 'ffprobe.exe'), '-v', 'error', '-select_streams', 'a', '-show_packets', '-show_data_hash', 'sha256', '-of', 'json', str(path)], check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return [p['data_hash'] for p in json.loads(r.stdout)['packets']]
    assert packets(source) == packets(target)
