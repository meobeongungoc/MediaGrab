"""Discover the channel's members-only uploads, without public-tab fallback."""
import re
from pathlib import Path
from urllib.parse import urlsplit, unquote

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from .auth import AuthSession, apply_auth_limits, fresh_edge_session, session_rejected
from .downloader import SafeLogger
from .errors import MediaGrabError, MembershipRequired, MEMBERSHIP_MESSAGE, membership_required, explain_error, SessionRejected
from .playlist import Entry, Playlist, download_playlist


def channel_url(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise ValueError
        if parsed.hostname not in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            raise ValueError
        parts = unquote(parsed.path).strip('/').split('/')
        if len(parts) > 1 and parts[-1] in ('videos', 'streams', 'shorts', 'membership', 'featured'):
            parts.pop()
        if len(parts) == 2 and parts[0] == 'channel' and re.fullmatch(r'UC[\w-]{22}', parts[1], re.ASCII):
            return 'https://www.youtube.com/channel/' + parts[1]
        if len(parts) == 1 and re.fullmatch(r'@[\w.\-·]{3,60}', parts[0]):
            return 'https://www.youtube.com/' + parts[0]
    except (ValueError, AttributeError):
        pass
    raise MediaGrabError('Paste a YouTube channel URL, such as https://www.youtube.com/@ChannelName. This mode selects members-only videos, not public uploads.')


def options(session: AuthSession, tools: Path | None = None) -> dict:
    result = dict(extract_flat='in_playlist', skip_download=True, quiet=True,
        lazy_playlist=True, ignoreerrors=False, cachedir=False, logger=SafeLogger(),
        socket_timeout=20, extractor_retries=2)
    if tools:
        result['js_runtimes'] = {'deno': {'path': str(tools / 'deno.exe')}}
    apply_auth_limits(result, session)
    return result


def raise_source_error(error, session):
    if membership_required(error):
        raise MembershipRequired(MEMBERSHIP_MESSAGE) from None
    if session.source == 'edge-extension' and session_rejected(error):
        raise SessionRejected('YouTube rejected the current session.') from None
    raise MediaGrabError(explain_error(error)) from None


@fresh_edge_session
def discover_members(url: str, session: AuthSession | None = None) -> Playlist:
    canonical = channel_url(url)
    if session is None:
        raise MediaGrabError('Members-only channel downloads require a YouTube login session. Select Edge extension or a cookie file first.')
    try:
        if '/channel/' in canonical:
            channel_id = canonical.rsplit('/', 1)[1]
            owner = ''
        else:
            resolve_opts = options(session)
            resolve_opts.update(playlistend=1)
            with YoutubeDL(resolve_opts) as downloader:
                session.attach(downloader)
                resolved = downloader.extract_info(canonical + '/videos', download=False)
                channel_id = (resolved or {}).get('channel_id') or (resolved or {}).get('id')
                owner = (resolved or {}).get('channel') or ''
            if not isinstance(channel_id, str) or not re.fullmatch(r'UC[\w-]{22}', channel_id, re.ASCII):
                raise MediaGrabError('Could not resolve the YouTube channel. Paste its /channel/UC... URL instead.')
        playlist_id = 'UUMO' + channel_id[2:]
        playlist_url = 'https://www.youtube.com/playlist?list=' + playlist_id
        with YoutubeDL(options(session)) as downloader:
            session.attach(downloader)
            info = downloader.extract_info(playlist_url, download=False)
            if not info or info.get('_type') != 'playlist' or info.get('id') != playlist_id:
                raise MediaGrabError('YouTube did not return the requested members-only playlist. Public uploads were not substituted; completeness is not confirmed.')
            owner = info.get('channel') or info.get('uploader') or owner
            entries, seen = [], set()
            # Fully consume continuation pages while the authenticated downloader is open.
            # Never set playlistend here and never ignore an extraction/pagination failure.
            for raw in info.get('entries') or ():
                if not isinstance(raw, dict):
                    raise MediaGrabError('YouTube returned an incomplete members-only list. Reload before downloading; completeness is not confirmed.')
                video_id = raw.get('id')
                if not isinstance(video_id, str) or not re.fullmatch(r'[\w-]{11}', video_id, re.ASCII):
                    raise MediaGrabError('A members-only item has no valid video ID. The list is incomplete; no batch was started.')
                if raw.get('channel_id') and raw['channel_id'] != channel_id:
                    raise MediaGrabError('YouTube returned a video from a different channel. No batch was started.')
                if raw.get('availability') in ('public', 'unlisted') or video_id in seen:
                    continue
                seen.add(video_id)
                entries.append(Entry(len(entries) + 1, video_id, str(raw.get('title') or 'Unavailable members-only video'),
                    'https://www.youtube.com/watch?v=' + video_id))
        if not entries:
            raise MediaGrabError('No members-only videos were returned for this account. Membership access and a complete channel list are not confirmed. No public videos were selected.')
        return Playlist(canonical + '/membership', (owner or 'Channel') + ' - Members-only', tuple(entries), owner)
    except (DownloadError, ExtractorError) as error:
        raise_source_error(error, session)


@fresh_edge_session
def check_membership(entry: Entry, session: AuthSession, tools: Path) -> None:
    """Preflight the batch once; a nonmember must not retry every locked video."""
    opts = options(session, tools)
    opts.update(extract_flat=False, noplaylist=True)
    try:
        with YoutubeDL(opts) as downloader:
            session.attach(downloader)
            info = downloader.extract_info(entry.url, download=False)
        if not info or not info.get('formats'):
            raise MediaGrabError('Could not verify video access. No members-only batch was started.')
    except (DownloadError, ExtractorError) as error:
        raise_source_error(error, session)


def download_members(playlist: Playlist, entries: tuple[Entry, ...], output: Path,
                     mode, quality, tools: Path, notify=None, *, session: AuthSession | None = None):
    if session is None or not entries:
        raise MediaGrabError('Select at least one members-only video and a YouTube login session.')
    check_membership(entries[0], session, tools)
    return download_playlist(playlist, entries, output, mode, quality, tools, notify, session=session)
