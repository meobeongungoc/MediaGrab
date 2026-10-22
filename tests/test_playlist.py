from pathlib import Path
from unittest.mock import Mock
import json
import zipfile

import pytest

from mediagrab.core.errors import MediaGrabError
from mediagrab.core.downloader import DownloadResult
from mediagrab.core.playlist import Entry, Playlist, safe_name, playlist_url, select_entries, download_playlist
from mediagrab.core.packager import create_zip


@pytest.mark.parametrize('name', ['CON', 'NUL.txt', 'COM1', 'LPT¹.log', 'A/B:C*D?E"F<G>H|', '...', 'a' * 300])
def test_windows_names(name):
    result = safe_name(name)
    assert len(result) <= 81
    assert not any(x in result for x in '<>:"/\\|?*')
    assert result and not result.endswith((' ', '.'))
    assert result.split('.')[0].upper() not in ('CON', 'NUL', 'COM1', 'LPT¹')


def test_accents_preserved():
    assert safe_name('Mưa hè') == 'Mưa hè'


def test_watch_list_normalized():
    assert playlist_url('https://www.youtube.com/watch?v=abc&list=PL123') == 'https://www.youtube.com/playlist?list=PL123'
    assert playlist_url('https://youtube.com.evil/playlist?list=PL123') is None
    assert playlist_url('https://soundcloud.com/a/sets/b?si=tracking') == 'https://soundcloud.com/a/sets/b'


ENTRIES = tuple(Entry(i, f'id{i}', f'Bài {i}', f'https://youtu.be/test{i}') for i in range(1, 5))
PLAYLIST = Playlist('https://www.youtube.com/playlist?list=PL123', 'Danh sách tiếng Việt', ENTRIES)


def test_selection_order_duplicates_and_original_index():
    assert [e.index for e in select_entries(ENTRIES, '4,1,3-4')] == [1, 3, 4]
    assert select_entries(ENTRIES, None) == ENTRIES


@pytest.mark.parametrize('selection', ['0', '5', '2-1', '1-999999999', 'a', '1,,2', '-1'])
def test_invalid_selection(selection):
    with pytest.raises(MediaGrabError):
        select_entries(ENTRIES, selection)


def fake_download(url, output, mode, quality, tools_dir, **kwargs):
    if url.endswith('test2'):
        raise MediaGrabError('Video đã xóa.')
    path = output / (kwargs['filename_stem'] + '.mp3')
    path.write_bytes(b'media:' + url.encode())
    return DownloadResult(path, {})


def test_failure_continues_skip_missing_and_corrupt_file(tmp_path, monkeypatch):
    from mediagrab.core import playlist as module
    downloader = Mock(side_effect=fake_download)
    monkeypatch.setattr(module, 'download_one', downloader)
    folder, rows = download_playlist(PLAYLIST, ENTRIES, tmp_path, 'audio', 'compat', tmp_path)
    assert [r['status'] for r in rows] == ['downloaded', 'error', 'downloaded', 'downloaded']
    assert len(json.loads(next(folder.glob('.mediagrab*.json')).read_text('utf-8'))) == 3
    downloader.reset_mock()
    _, rows = download_playlist(PLAYLIST, ENTRIES, tmp_path, 'audio', 'compat', tmp_path)
    assert [r['status'] for r in rows] == ['skipped', 'error', 'skipped', 'skipped']
    assert downloader.call_count == 1
    (folder / rows[0]['file']).unlink()
    (folder / rows[2]['file']).write_bytes(b'corrupt')
    downloader.reset_mock()
    _, rows = download_playlist(PLAYLIST, ENTRIES, tmp_path, 'audio', 'compat', tmp_path)
    assert [r['status'] for r in rows] == ['downloaded', 'error', 'downloaded', 'skipped']
    assert downloader.call_count == 3


def test_modes_get_separate_records(tmp_path, monkeypatch):
    from mediagrab.core import playlist as module
    monkeypatch.setattr(module, 'download_one', fake_download)
    a, _ = download_playlist(PLAYLIST, ENTRIES[:1], tmp_path, 'mp4', 'compat', tmp_path)
    b, _ = download_playlist(PLAYLIST, ENTRIES[:1], tmp_path, 'audio', 'compat', tmp_path)
    assert a == b
    records = {}
    for record in a.glob('.mediagrab*.json'):
        records.update(json.loads(record.read_text('utf-8')))
    assert any(':mp4:compat:' in key for key in records)
    assert any(':audio:compat:' in key for key in records)


def test_zip_stored_exact_bytes_and_safe_delete(tmp_path):
    folder = tmp_path / 'Playlist'
    folder.mkdir()
    files = [folder / '01 - Mưa hè.mp3', folder / '02 - Tập.mp3']
    for i, file in enumerate(files):
        file.write_bytes(bytes([i]) * 100)
    archive = create_zip(folder, files, delete_source=True)
    assert not folder.exists()
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert all(f.compress_type == zipfile.ZIP_STORED for f in z.infolist())
        assert z.read(files[1].name) == bytes([1]) * 100


def test_zip_failure_keeps_sources(tmp_path, monkeypatch):
    folder = tmp_path / 'Playlist'
    folder.mkdir()
    file = folder / 'song.mp3'
    file.write_bytes(b'audio')
    monkeypatch.setattr(zipfile.ZipFile, 'testzip', lambda self: 'song.mp3')
    with pytest.raises(MediaGrabError):
        create_zip(folder, [file], True)
    assert file.read_bytes() == b'audio'


def test_unknown_files_block_delete(tmp_path):
    folder = tmp_path / 'Playlist'
    folder.mkdir()
    file = folder / 'song.mp3'
    file.write_bytes(b'audio')
    other = folder / 'personal.txt'
    other.write_text('keep me')
    with pytest.raises(MediaGrabError):
        create_zip(folder, [file], True)
    assert file.exists() and other.exists()


def test_zip_rejects_outside_file_and_does_not_overwrite(tmp_path):
    folder = tmp_path / 'Playlist'
    folder.mkdir()
    file = folder / 'song.mp3'
    file.write_bytes(b'audio')
    outside = tmp_path / 'outside.txt'
    outside.write_text('keep')
    with pytest.raises(MediaGrabError):
        create_zip(folder, [outside])
    first = create_zip(folder, [file])
    second = create_zip(folder, [file])
    assert first != second and first.exists() and second.exists()
