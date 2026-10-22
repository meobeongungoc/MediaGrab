"""Public-page discovery and real local HTML5 download regression checks."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from mediagrab.core import webpage
from mediagrab.core.errors import MediaGrabError
from mediagrab.core.playlist import download_playlist


@pytest.mark.parametrize('url', ['file:///a.mp4', 'https://user:secret@example.com/a', 'http://127.0.0.1/a', 'http://192.168.1.1/a', 'http://localhost/a', 'http://host.local/a', 'https://example.com:8000/a'])
def test_invalid_web_sources(url):
    with pytest.raises(MediaGrabError):
        webpage.validate_web_url(url)


def test_public_web_urls():
    webpage.validate_web_url('https://forum.example.com/thread?id=2')


def video(url, title='Video'):
    return {'id': 'v', 'title': title, 'webpage_url': 'https://forum.example.com/thread',
            'formats': [{'url': url, 'ext': 'mp4'}]}


def test_duplicate_embeds_and_signed_identity():
    a = video('https://cdn.example.com/one.mp4?token=secret')
    b = video('https://cdn.example.com/one.mp4?token=renewed')
    c = video('https://cdn.example.com/two.mp4')
    items = webpage.page_entries({'entries': [a, b, c, None]})
    assert len(items) == 3
    assert items[-1].url is None
    assert items[0].id == webpage.media_identity(b)
    assert 'secret' not in repr(items)


def test_nested_and_excessive_entries():
    info = {'entries': [{'entries': [video('https://cdn.example.com/one.mp4')]}]}
    assert len(webpage.page_entries(info)) == 1
    with pytest.raises(MediaGrabError, match='100'):
        webpage.page_entries({'entries': [video(f'https://cdn.example.com/{i}.mp4') for i in range(101)]})


def test_web_worker_rejects_youtube_session_before_loading(monkeypatch):
    from mediagrab import service
    monkeypatch.setattr(service, 'load_session', lambda *a: pytest.fail('Must not read browser session'))
    with pytest.raises(MediaGrabError, match='No login'):
        service.execute_job({'kind': 'webpage', 'browser': 'edge', 'output': 'D:/Downloads'})


def test_web_worker_rechecks_selected_identity(monkeypatch):
    from mediagrab import service
    from mediagrab.core.playlist import Playlist, Entry
    monkeypatch.setattr(service, 'load_session', lambda *a: None)
    monkeypatch.setattr(service, 'discover_page', lambda *a: Playlist('https://example.com', 'Page', (Entry(1, 'new', 'New video', None),)))
    monkeypatch.setattr(service, 'download_playlist', lambda *a, **k: pytest.fail('Must not download changed selection'))
    with pytest.raises(MediaGrabError, match='changed'):
        service.execute_job({'kind': 'webpage', 'url': 'https://example.com', 'output': 'D:/Downloads', 'items': '1', 'ids': ['old']})


@pytest.fixture(scope='module')
def local_forum(tmp_path_factory):
    root = tmp_path_factory.mktemp('forum')
    tools = Path(__file__).resolve().parents[1]
    # Artificial colour/tone fixture; no user media and no external downloads.
    subprocess.run([str(tools / 'ffmpeg.exe'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '0.5',
                    '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', str(root / 'sample.mp4')],
                   capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    for i in range(10):
        shutil.copyfile(root / 'sample.mp4', root / f'clip{i}.mp4')
    for count in (2, 10):
        body = ''.join(f'<article><p>Comment {i}</p><video controls><source src="/clip{i}.mp4" type="video/mp4"></video></article>' for i in range(count))
        (root / f'forum{count}.html').write_text(f'<html><head><title>Forum {count}</title></head><body>{body}</body></html>')
    class QuietHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith('.mp4') and '/forum' not in self.headers.get('Referer', ''):
                self.send_error(403)
                return
            super().do_GET()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', tools
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize('count', [2, 10])
def test_html5_forum_download_all_and_resume(local_forum, tmp_path, monkeypatch, count):
    base, tools = local_forum
    # Loopback is permitted only inside this isolated test fixture.
    monkeypatch.setattr(webpage, 'validate_web_url', lambda url: None)
    monkeypatch.setattr(webpage, 'check_public_host', lambda url: None)
    page = webpage.discover_page(f'{base}/forum{count}.html', tools)
    assert len(page.entries) == count
    from mediagrab.core.downloader import download_one
    first = page.entries[0]
    download_one(first.url, tmp_path / 'single', 'mp4', 'compat', tools, prepared_info=first.media)
    folder, rows = download_playlist(page, page.entries, tmp_path, 'mp4', 'compat', tools)
    assert all(r['status'] == 'downloaded' for r in rows), rows
    assert len(list(folder.glob('*.mp4'))) == count
    _, repeat = download_playlist(page, page.entries, tmp_path, 'mp4', 'compat', tools)
    assert all(r['status'] == 'skipped' for r in repeat)


def test_best_audio_has_playable_audio_only(local_forum, tmp_path, monkeypatch):
    import json
    from urllib.request import Request, urlopen
    from mediagrab.core.downloader import download_one
    base, tools = local_forum
    monkeypatch.setattr(webpage, 'validate_web_url', lambda url: None)
    monkeypatch.setattr(webpage, 'check_public_host', lambda url: None)
    page = webpage.discover_page(base + '/forum2.html', tools)
    entry = page.entries[0]
    result = download_one(entry.url, tmp_path / 'out', 'audio', 'compat', tools, prepared_info=entry.media)
    assert any(s['codec_type'] == 'audio' for s in result.metadata['streams'])
    assert not any(s['codec_type'] == 'video' for s in result.metadata['streams'])
    original = tmp_path / 'source.mp4'
    with urlopen(Request(base + '/clip0.mp4', headers={'Referer': base + '/forum2.html'})) as response:
        original.write_bytes(response.read())
    def packets(path):
        r = subprocess.run([str(tools / 'ffprobe.exe'), '-v', 'error', '-select_streams', 'a', '-show_packets', '-show_data_hash', 'sha256', '-of', 'json', str(path)], check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return [p['data_hash'] for p in json.loads(r.stdout)['packets']]
    assert packets(original) == packets(result.path)
    subprocess.run([str(tools / 'ffmpeg.exe'), '-v', 'error', '-i', str(result.path), '-f', 'null', '-'], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
