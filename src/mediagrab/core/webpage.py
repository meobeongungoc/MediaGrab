"""Discover a bounded selection of public page media; keep extraction data in memory."""
import hashlib
import ipaddress
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from .downloader import SafeLogger
from .errors import MediaGrabError, explain_error
from .playlist import Entry, Playlist
from .naming import collection_owner

MAX_ITEMS = 100


def validate_web_url(url: str) -> None:
    try:
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
            raise ValueError
        host = p.hostname.lower().rstrip('.')
        if host == 'localhost' or host.endswith(('.localhost', '.local')) or '.' not in host:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
    except ValueError:
        raise MediaGrabError('Use a public HTTP/HTTPS page or media URL without embedded login details.') from None


def check_public_host(url: str) -> None:
    validate_web_url(url)
    p = urlsplit(url)
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError
    except (OSError, ValueError):
        raise MediaGrabError('This source does not resolve to a public internet address.') from None


def media_identity(raw: dict[str, Any]) -> str:
    # Keep content identifiers in queries; ignore only known expiring signatures.
    # Only the resulting hash is stored in download records.
    urls = [f.get('url') for f in raw.get('formats', []) if f.get('url')]
    if not urls and raw.get('url'):
        urls = [raw['url']]
    volatile = {'token', 'signature', 'sig', 'expires', 'expiry', 'exp', 'policy', 'key-pair-id', 'hdnts', 'hdnea'}
    def stable_url(value: str) -> str:
        parsed = urlsplit(value)
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                 if key.lower() not in volatile and not key.lower().startswith('x-amz-')]
        return urlunsplit((*parsed[:3], urlencode(sorted(query)), ''))
    paths = sorted({stable_url(u) for u in urls})
    value = '|'.join(paths) or str(raw.get('extractor', '')) + ':' + str(raw.get('id', ''))
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def page_entries(info: dict[str, Any]) -> tuple[Entry, ...]:
    result: list[Entry] = []
    seen: set[str] = set()
    visited = 0

    def walk(raw: dict[str, Any] | None, depth: int = 0) -> None:
        nonlocal visited
        visited += 1
        if visited > MAX_ITEMS * 4 or depth > 5:
            raise MediaGrabError('This page contains too many nested items. Use a more specific post URL.')
        if raw and ('entries' in raw or raw.get('_type') in ('playlist', 'multi_video')):
            for child in raw.get('entries') or []:
                walk(child, depth + 1)
            return
        number = len(result) + 1
        if number > MAX_ITEMS:
            raise MediaGrabError('This page contains more than 100 media items. Use a more specific post URL.')
        if not raw:
            result.append(Entry(number, f'unavailable-{number}', 'Unavailable embedded item', None))
            return
        identity = media_identity(raw)
        if identity in seen:
            return
        seen.add(identity)
        title = str(raw.get('title') or f'Video {number}')
        playable = bool(raw.get('formats') or raw.get('url')) and raw.get('_type') not in ('url', 'url_transparent')
        # Keep failed items visible instead of claiming all page videos are available.
        urls = [f.get('url') for f in raw.get('formats', []) if f.get('url')]
        if raw.get('url'):
            urls.append(raw['url'])
        for media_url in urls:
            validate_web_url(media_url)
        page = raw.get('webpage_url') or (urls[0] if urls else None)
        if page:
            validate_web_url(page)
        result.append(Entry(number, identity, title, page if playable else None, raw if playable else None))

    walk(info)
    if not result:
        raise MediaGrabError('No downloadable video was found on this page. Try the original player URL.')
    return tuple(result)


def discover_page(url: str, tools_dir: Path) -> Playlist:
    check_public_host(url)
    options = dict(quiet=True, no_warnings=True, skip_download=True, cachedir=False,
                   logger=SafeLogger(), noplaylist=False, extract_flat=False,
                   ignoreerrors=True, socket_timeout=20, retries=2, extractor_retries=2,
                   playlistend=MAX_ITEMS + 1, lazy_playlist=True,
                   js_runtimes={'deno': {'path': str(tools_dir / 'deno.exe')}},
                   ffmpeg_location=str(tools_dir))
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise MediaGrabError('No accessible video found. The page may require login, JavaScript interaction or an unsupported player.')
            entries = page_entries(info)
        return Playlist(url, str(info.get('title') or 'Web page videos'), entries, collection_owner(info) if info.get('extractor_key') != 'Generic' else '')
    except (DownloadError, ExtractorError) as error:
        raise MediaGrabError(explain_error(error)) from None
