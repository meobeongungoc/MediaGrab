import pytest
from mediagrab.core.podcast import parse_feed, find_feeds, download_feed, open_http
from mediagrab.core.errors import MediaGrabError
from mediagrab.core.updater import update_args
from pathlib import Path

RSS = '''<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>Mưa hè</title><itunes:author>Vanesa</itunes:author><item><title>Tập 1</title><guid>stable1</guid><enclosure url="https://example.com/audio.mp3?token=PRIVATE" type="audio/mpeg"/></item><item><title>Tập thiếu</title></item></channel></rss>'''


def test_feed_unicode_missing_enclosure_and_secret_repr():
    feed = parse_feed(RSS.encode(), 'https://example.com/feed?key=SECRET')
    assert feed.title == 'Mưa hè' and feed.author == 'Vanesa'
    assert len(feed.episodes) == 2 and not feed.episodes[1].url
    assert 'PRIVATE' not in repr(feed.episodes[0]) and 'SECRET' not in repr(feed)


def test_guid_stable_if_signed_media_url_changes():
    a = parse_feed(RSS.encode(), 'https://example.com/feed')
    b = parse_feed(RSS.replace('token=PRIVATE', 'token=CHANGED').encode(), 'https://example.com/feed')
    assert a.episodes[0].id == b.episodes[0].id


@pytest.mark.parametrize('xml', ['<html>bad</html>', '<rss><channel/></rss>', '<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///secret">]><rss><channel><title>&x;</title></channel></rss>'])
def test_bad_or_entity_xml_rejected(xml):
    with pytest.raises(MediaGrabError):
        parse_feed(xml.encode(), 'https://example.com/feed')


@pytest.mark.parametrize('url', ['http://127.0.0.1/feed', 'http://[::1]/feed', 'file:///cookies.txt', 'https://user:password@example.com/feed'])
def test_feed_blocks_local_or_credential_urls(url):
    with pytest.raises(MediaGrabError):
        open_http(url)


def test_updater_targets_local_and_only_official_index(tmp_path):
    args = update_args(tmp_path)
    assert args[args.index('--target') + 1] == str(tmp_path)
    assert args[args.index('--index-url') + 1] == 'https://pypi.org/simple'


def test_missing_episode_continues_with_safe_report(tmp_path):
    feed = parse_feed(RSS.encode(), 'https://example.com/feed?key=SECRET')
    folder, rows = download_feed(feed, {2}, tmp_path, 'audio', tmp_path)
    assert rows[0]['status'] == 'error'
    assert 'SECRET' not in next(folder.glob('results*.json')).read_text('utf-8')


def test_feed_redownloads_only_changed_or_missing(tmp_path, monkeypatch):
    import json
    from mediagrab.core import podcast
    feed = parse_feed(RSS.encode(), 'https://example.com/feed')
    calls = []
    class Response:
        headers = {'Content-Type': 'audio/mpeg'}
        url = 'https://example.com/audio.mp3'
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, size):
            calls.append('download')
            yield b'fake-audio-content'
    monkeypatch.setattr(podcast, 'open_http', lambda url: Response())
    monkeypatch.setattr(podcast, 'tag_audio', lambda source, dest, *args: dest.write_bytes(source.read_bytes()))
    monkeypatch.setattr(podcast, 'inspect_media', lambda *a: {'streams': [{'codec_type':'audio'}]})
    folder, rows = download_feed(feed, {1,2}, tmp_path, 'audio', tmp_path)
    assert [r['status'] for r in rows] == ['downloaded', 'error']
    _, rows = download_feed(feed, {1}, tmp_path, 'audio', tmp_path)
    assert rows[0]['status'] == 'skipped' and len(calls) == 1
    (folder / rows[0]['file']).write_bytes(b'changed')
    _, rows = download_feed(feed, {1}, tmp_path, 'audio', tmp_path)
    assert rows[0]['status'] == 'downloaded' and len(calls) == 2
    assert 'PRIVATE' not in next(folder.glob('.mediagrab*.json')).read_text('utf-8')
