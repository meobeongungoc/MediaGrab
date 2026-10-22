from pathlib import Path
from unittest.mock import Mock

import pytest
from yt_dlp.utils import DownloadError

from mediagrab.core import members
from mediagrab.core.auth import AuthSession
from mediagrab.core.errors import MediaGrabError, MembershipRequired, membership_required
from yt_dlp.cookies import YoutubeDLCookieJar

CHANNEL = 'UC' + 'a' * 22
URL = 'https://www.youtube.com/channel/' + CHANNEL
PLAYLIST = 'UUMO' + CHANNEL[2:]


def row(number, **fields):
    return dict(id=f'{number:011d}', title=f'Episode {number}', channel_id=CHANNEL,
                **fields)


def install_extractor(monkeypatch, entries, **fields):
    calls = []
    class Fake:
        def __init__(self, opts):
            self.opts = opts
            calls.append(self)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            self.url = url
            return dict(_type='playlist', id=PLAYLIST, channel='Test Channel', entries=entries, **fields)
    monkeypatch.setattr(members, 'YoutubeDL', Fake)
    return calls


def session():
    return AuthSession(YoutubeDLCookieJar(), 'memory-test')


def test_all_continuation_entries_consumed_without_cap(monkeypatch):
    visited = []
    def entries():
        for i in range(1, 151):
            visited.append(i)
            yield row(i, availability='subscriber_only')
    calls = install_extractor(monkeypatch, entries())
    result = members.discover_members(URL, session())
    assert len(result.entries) == len(visited) == 150
    assert result.entries[-1].index == 150
    assert result.owner == 'Test Channel'
    assert 'list=' + PLAYLIST in calls[0].url
    assert 'playlistend' not in calls[0].opts
    assert calls[0].opts['ignoreerrors'] is False


def test_public_items_and_duplicates_not_selected(monkeypatch):
    install_extractor(monkeypatch, [row(1), row(1), row(2, availability='public'),
                                    row(3, availability='unlisted'), row(4)])
    result = members.discover_members(URL, session())
    assert [e.id for e in result.entries] == ['00000000001', '00000000004']


def test_public_tab_redirect_fails_closed(monkeypatch):
    calls = install_extractor(monkeypatch, [row(1)])
    original = members.YoutubeDL.extract_info
    def redirect(self, *a, **k):
        info = original(self, *a, **k)
        info['id'] = 'UU' + CHANNEL[2:]
        return info
    monkeypatch.setattr(members.YoutubeDL, 'extract_info', redirect)
    with pytest.raises(MediaGrabError, match='Public uploads were not substituted'):
        members.discover_members(URL, session())


def test_pagination_error_never_returns_partial_success(monkeypatch):
    def entries():
        yield row(1)
        raise DownloadError('Connection timed out')
    install_extractor(monkeypatch, entries())
    with pytest.raises(MediaGrabError, match='Connection failed'):
        members.discover_members(URL, session())


@pytest.mark.parametrize('bad', [None, {'id': 'bad-id'}, row(1, channel_id_override='unused')])
def test_missing_or_wrong_channel_items_fail(monkeypatch, bad):
    if bad and 'channel_id_override' in bad:
        bad['channel_id'] = 'UC' + 'b' * 22
    install_extractor(monkeypatch, [bad])
    with pytest.raises(MediaGrabError):
        members.discover_members(URL, session())


def test_login_required_before_network(monkeypatch):
    monkeypatch.setattr(members, 'YoutubeDL', lambda *a: pytest.fail('No session: no network'))
    with pytest.raises(MediaGrabError, match='login session'):
        members.discover_members(URL)


@pytest.mark.parametrize('url', ['https://evil.test/@MCILL', 'https://youtube.com/watch?v=123',
    'https://youtube.com/channel/UCshort', 'https://youtube.com.evil.test/@MCILL',
    'https://name:pass@youtube.com/@MCILL'])
def test_reject_nonchannel_urls(url):
    with pytest.raises(MediaGrabError):
        members.channel_url(url)


def test_normalize_membership_tab():
    assert members.channel_url(URL + '/membership?x=1') == URL
    assert members.channel_url('https://youtube.com/@MCILLDISSNEELAND/videos') == 'https://www.youtube.com/@MCILLDISSNEELAND'


def test_batch_permission_denial_is_genuine_and_stops_before_download(monkeypatch, tmp_path):
    install_extractor(monkeypatch, [row(1)])
    playlist = members.discover_members(URL, session())
    def denied(self, *a, **k):
        raise DownloadError('Join this channel to get access to members-only content like this video')
    monkeypatch.setattr(members.YoutubeDL, 'extract_info', denied)
    monkeypatch.setattr(members, 'download_playlist', lambda *a, **k: pytest.fail('Must not download after permission denial'))
    with pytest.raises(MembershipRequired, match='required tier'):
        members.download_members(playlist, playlist.entries, tmp_path, 'mp4', 'compat', tmp_path, session=session())
    assert not list(tmp_path.iterdir())


def test_all_selected_items_reach_batch_after_permission_check(monkeypatch, tmp_path):
    install_extractor(monkeypatch, (row(i) for i in range(1, 151)))
    auth = session()
    playlist = members.discover_members(URL, auth)
    check = Mock()
    download = Mock(return_value=(tmp_path, [{'status': 'downloaded'}] * 150))
    monkeypatch.setattr(members, 'check_membership', check)
    monkeypatch.setattr(members, 'download_playlist', download)
    _, rows = members.download_members(playlist, playlist.entries, tmp_path, 'mp4', 'best', tmp_path, session=auth)
    assert len(rows) == len(download.call_args.args[1]) == 150
    check.assert_called_once_with(playlist.entries[0], auth, tmp_path)


@pytest.mark.parametrize('error', ['Sign in to confirm', 'HTTP Error 403', 'HTTP Error 429',
    'This channel does not have a membership tab', 'The playlist does not exist'])
def test_unrelated_failures_are_not_membership_success(error):
    assert not membership_required(error)
