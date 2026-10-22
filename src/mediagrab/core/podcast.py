"""Bounded public HTTP fetches and podcast feeds; no external conversion service."""
import hashlib
import ipaddress
import json
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urljoin

import requests
from defusedxml import ElementTree
from .errors import MediaGrabError
from .playlist import safe_name, digest, write_json
from .downloader import inspect_media
from .naming import collection_folder, reserve_name


def open_http(url: str) -> requests.Response:
    # Validate every redirect, including links read from untrusted RSS.
    for _ in range(6):
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
            raise MediaGrabError('RSS/media URLs must use public HTTP/HTTPS and must not contain a password.')
        try:
            addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise MediaGrabError('RSS sources cannot access internal network addresses.')
            r = requests.get(url, timeout=(15, 45), stream=True, allow_redirects=False, headers={'User-Agent': 'MediaGrab/0.2'})
            if r.is_redirect:
                target = urljoin(url, r.headers.get('Location', ''))
                r.close()
                url = target
                continue
            r.raise_for_status()
            return r
        except MediaGrabError:
            raise
        except Exception:
            raise MediaGrabError('Could not read the source. Check the network and access rights, or retry later.') from None
    raise MediaGrabError('Too many source redirects.')


def fetch_bytes(url: str, limit: int = 8_000_000) -> bytes:
    with open_http(url) as response:
        result = bytearray()
        for chunk in response.iter_content(65536):
            result.extend(chunk)
            if len(result) > limit:
                raise MediaGrabError('Source data exceeds the allowed size limit.')
        return bytes(result)


@dataclass(frozen=True, repr=False)
class Episode:
    index: int
    id: str
    title: str
    url: str = field(repr=False)
    mime: str = ''


@dataclass(frozen=True, repr=False)
class Feed:
    title: str
    author: str
    url: str = field(repr=False)
    episodes: tuple[Episode, ...] = ()


def parse_feed(data: bytes, url: str) -> Feed:
    try:
        root = ElementTree.fromstring(data)
        channel = root.find('channel')
        if channel is None:
            raise ValueError
        title = channel.findtext('title') or 'Podcast'
        author = channel.findtext('{http://www.itunes.com/dtds/podcast-1.0.dtd}author') or ''
        episodes = []
        for index, item in enumerate(channel.findall('item'), 1):
            enclosure = item.find('enclosure')
            media = enclosure.get('url', '') if enclosure is not None else ''
            guid = item.findtext('guid') or media or str(index)
            episodes.append(Episode(index, hashlib.sha256(guid.encode()).hexdigest()[:16],
                                    item.findtext('title') or f'Episode {index}', media,
                                    enclosure.get('type', '') if enclosure is not None else ''))
        if not episodes:
            raise ValueError
        return Feed(title, author, url, tuple(episodes))
    except Exception:
        raise MediaGrabError('Invalid or empty podcast RSS. A standard RSS feed with audio enclosures is required.') from None


def read_feed(url: str) -> Feed:
    return parse_feed(fetch_bytes(url), url)


# This mapping was independently matched against Spotify and Apple on 16/10/2026.
VERIFIED_SHOWS = {'6xKiTgnRBN18AZrx2TD4cf': 1672776215}


def find_feeds(url: str) -> list[dict[str, str]]:
    p = urlsplit(url)
    if p.hostname not in ('open.spotify.com', 'www.open.spotify.com'):
        feed = read_feed(url)
        return [{'title': feed.title, 'author': feed.author, 'url': url}]
    parts = p.path.strip('/').split('/')
    if len(parts) != 2 or parts[0] != 'show':
        raise MediaGrabError('For Spotify, use a show URL (/show/) or a direct RSS feed. Episode URL matching is not supported yet.')
    from urllib.parse import urlencode
    if parts[1] in VERIFIED_SHOWS:
        endpoint = 'https://itunes.apple.com/lookup?' + urlencode({'id': VERIFIED_SHOWS[parts[1]], 'entity': 'podcast'})
    else:
        metadata = json.loads(fetch_bytes('https://open.spotify.com/oembed?' + urlencode({'url': f'https://open.spotify.com/show/{parts[1]}'})))
        title = metadata.get('title')
        if not title:
            raise MediaGrabError('Could not identify the show. Use an RSS feed provided by its creator, if available.')
        endpoint = 'https://itunes.apple.com/search?' + urlencode({'term': title, 'media': 'podcast', 'entity': 'podcast', 'limit': 10})
    candidates = json.loads(fetch_bytes(endpoint)).get('results', [])
    feeds = [{'title': c.get('collectionName', ''), 'author': c.get('artistName', ''), 'url': c['feedUrl']}
             for c in candidates if c.get('feedUrl')]
    if not feeds:
        raise MediaGrabError('No RSS feed found. The show may be Spotify-only; creating an RSS file does not provide access to its audio.')
    return feeds


def tag_audio(source: Path, destination: Path, title: str, artist: str, tools: Path) -> None:
    command = [str(tools / 'ffmpeg.exe'), '-v', 'error', '-nostdin', '-y', '-i', str(source),
               '-map', '0:a:0', '-c:a', 'copy', '-metadata', 'title=' + title]
    if artist:
        command += ['-metadata', 'artist=' + artist]
    command.append(str(destination))
    try:
        result = subprocess.run(command, capture_output=True, timeout=120,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise MediaGrabError('Could not save podcast title/author metadata without converting the audio.')
    except (OSError, subprocess.TimeoutExpired):
        raise MediaGrabError('Could not write podcast metadata. Check FFmpeg and retry.') from None


def download_feed(feed: Feed, indices: set[int], output: Path, mode: str, tools: Path,
                  notify: Callable[[str], None] | None = None) -> tuple[Path, list[dict]]:
    if mode != 'audio':
        raise MediaGrabError('Podcast RSS uses Best audio; conversion is not available.')
    key = hashlib.sha256(feed.url.encode()).hexdigest()[:10]
    folder, legacy = collection_folder(output, feed.title, feed.author, key, mode)
    if folder.is_symlink() or (folder.exists() and folder.is_junction()):
        raise MediaGrabError('The RSS folder must not be a filesystem link.')
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / ('.mediagrab.json' if legacy else f'.mediagrab-{key}-{mode}.json')
    try:
        manifest = json.loads(archive.read_text('utf-8')) if archive.exists() else {}
        if not isinstance(manifest, dict):
            raise ValueError
    except Exception:
        raise MediaGrabError('Could not read the RSS download record. Choose a new output folder.') from None
    rows = []
    for episode in feed.episodes:
        if episode.index not in indices:
            continue
        record_key = episode.id if legacy else f'{key}:{episode.id}'
        row = {'index': episode.index, 'title': episode.title, 'artist': feed.author}
        if notify:
            notify(f'Episode {episode.index:02d}: {episode.title}')
        temp = folder / f'.{key}-{episode.id}.part'
        tagged = None
        reservation = None
        try:
            old = manifest.get(record_key, {})
            previous = folder / old.get('file', '')
            safe_previous = previous.parent.resolve() == folder.resolve() and not previous.is_symlink()
            if safe_previous and previous.is_file() and not previous.is_symlink() and previous.stat().st_size == old.get('size') and digest(previous) == old.get('sha256'):
                row.update(status='skipped', file=previous.name)
            else:
                if not episode.url:
                    raise MediaGrabError('This episode has no audio URL in the RSS feed.')
                with open_http(episode.url) as response:
                    mime = response.headers.get('Content-Type', '').split(';')[0]
                    if mime in ('text/html', 'application/xml', 'text/xml'):
                        raise MediaGrabError('The source returned a web page instead of an audio file.')
                    suffix = Path(urlsplit(response.url).path).suffix.lower()
                    if suffix not in ('.mp3', '.m4a', '.ogg', '.opus', '.flac', '.wav', '.mp4', '.aac'):
                        suffix = {'audio/mpeg': '.mp3', 'audio/mp4': '.m4a', 'audio/x-m4a': '.m4a', 'audio/ogg': '.ogg', 'audio/aac': '.aac'}.get(mime or episode.mime, '.m4a')
                    with temp.open('wb') as stream:
                        for chunk in response.iter_content(262144):
                            stream.write(chunk)
                inspect_media(temp, tools)
                if old.get('file') and safe_previous:
                    target = previous
                else:
                    filename, reservation = reserve_name(folder, episode.title)
                    target = folder / (filename + suffix)
                tagged = folder / f'.{key}-{episode.id}.tagged{target.suffix}'
                tag_audio(temp, tagged, episode.title, feed.author, tools)
                inspect_media(tagged, tools)
                tagged.replace(target)
                temp.unlink(missing_ok=True)
                manifest[record_key] = {'file': target.name, 'size': target.stat().st_size, 'sha256': digest(target)}
                write_json(archive, manifest)
                row.update(status='downloaded', file=target.name)
        except Exception as error:
            row.update(status='error', reason=str(error) if isinstance(error, MediaGrabError) else 'Could not download this episode. Retry or check the source.')
        finally:
            if reservation is not None:
                reservation.unlink(missing_ok=True)
            if tagged is not None:
                tagged.unlink(missing_ok=True)
        rows.append(row)
        if notify:
            notify({'downloaded': 'Downloaded.', 'skipped': 'Already present — skipped.', 'error': 'Error: ' + row.get('reason', '')}[row['status']])
    write_json(folder / ('results.json' if legacy else f'results-{key}-{mode}.json'), rows)
    return folder, rows
