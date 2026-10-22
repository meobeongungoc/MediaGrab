"""Playlist discovery, selection, safe naming and resumable batch downloads."""
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit, urlencode

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from .downloader import SafeLogger, download_one, validate_url
from .errors import MediaGrabError, explain_error
from .options import Mode, Quality
from .auth import AuthSession, apply_auth_limits, fresh_edge_session, session_rejected
from .errors import SessionRejected
from .naming import safe_name, collection_owner, collection_folder, recognize_music


@dataclass(frozen=True)
class Entry:
    index: int
    id: str
    title: str
    url: str | None
    media: dict[str, Any] | None = field(default=None, repr=False, compare=False)
    music: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class Playlist:
    url: str
    title: str
    entries: tuple[Entry, ...]
    owner: str = ""


def playlist_url(url: str) -> str | None:
    try:
        p = urlsplit(url)
        if p.scheme not in ('https', 'http') or p.username or p.password or p.port not in (None, 80, 443):
            return None
        host = (p.hostname or '').lower()
        if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'):
            ids = parse_qs(p.query).get('list')
            if ids and re.fullmatch(r'[A-Za-z0-9_-]+', ids[0]):
                return 'https://www.youtube.com/playlist?' + urlencode({'list': ids[0]})
        if host in ('soundcloud.com', 'www.soundcloud.com', 'm.soundcloud.com'):
            parts = p.path.strip('/').split('/')
            if len(parts) == 3 and parts[1] == 'sets':
                return 'https://soundcloud.com/' + '/'.join(parts)
    except ValueError:
        pass
    return None


@fresh_edge_session
def discover(url: str, session: AuthSession | None = None) -> Playlist:
    canonical = playlist_url(url)
    if canonical is None:
        raise MediaGrabError('Use a public YouTube playlist or SoundCloud set/album URL.')
    options = dict(extract_flat='in_playlist', skip_download=True, quiet=True,
                   ignoreerrors=True, cachedir=False, logger=SafeLogger(),
                   socket_timeout=20, extractor_retries=3)
    if session and "youtube.com" not in canonical:
        raise MediaGrabError("This login session can only be used for YouTube.")
    apply_auth_limits(options, session)
    if session and session.source == 'edge-extension':
        options['ignoreerrors'] = False
    try:
        with YoutubeDL(options) as ydl:
            if session:
                session.attach(ydl)
            info = ydl.extract_info(canonical, download=False)
        if not info or info.get('_type') != 'playlist':
            raise MediaGrabError('Could not read the playlist. Check that it is public and the URL is complete.')
        entries = []
        for position, raw in enumerate(info.get('entries') or [], 1):
            raw = raw or {}
            number = raw.get('playlist_index') or position
            item_url = raw.get('webpage_url') or raw.get('url')
            if item_url and not item_url.startswith(('http://', 'https://')):
                item_url = 'https://www.youtube.com/watch?v=' + str(raw.get('id', '')) if 'youtube.com' in canonical else None
            if item_url:
                try:
                    validate_url(item_url)
                except MediaGrabError:
                    item_url = None
            entries.append(Entry(int(number), str(raw.get('id') or f'missing-{number}'),
                                 str(raw.get('title') or 'Unavailable item'), item_url, music=recognize_music(raw)))
        if not entries:
            raise MediaGrabError('The playlist is empty or has no accessible items.')
        artists = {e.music.get('artist') for e in entries}
        owner = next(iter(artists)) if len(artists) == 1 and all(artists) else collection_owner(info)
        return Playlist(canonical, str(info.get('title') or 'Playlist'), tuple(entries), owner)
    except (DownloadError, ExtractorError) as error:
        if session and session.source == 'edge-extension' and session_rejected(error):
            raise SessionRejected('YouTube rejected the current session.') from None
        raise MediaGrabError(explain_error(error)) from None


def select_entries(entries: tuple[Entry, ...], selection: str | None) -> tuple[Entry, ...]:
    if not selection or selection.lower().strip() in ('all', 'tatca'):
        return entries
    available = {e.index for e in entries}
    wanted: set[int] = set()
    try:
        for part in selection.split(','):
            bounds = [int(x) for x in part.strip().split('-')]
            if len(bounds) == 1:
                wanted.add(bounds[0])
            elif len(bounds) == 2 and 1 <= bounds[0] <= bounds[1] <= max(available):
                wanted.update(range(bounds[0], bounds[1] + 1))
            else:
                raise ValueError
        if not wanted or not wanted <= available:
            raise ValueError
    except ValueError:
        raise MediaGrabError('Invalid item numbers. Example: 1,3-4. Use numbers present in the list.') from None
    return tuple(e for e in entries if e.index in wanted)


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path: Path, data: Any) -> None:
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def download_playlist(playlist: Playlist, entries: tuple[Entry, ...], output: Path,
                      mode: Mode, quality: Quality, tools_dir: Path,
                      notify: Callable[[str], None] | None = None, *,
                      session: AuthSession | None = None) -> tuple[Path, list[dict[str, Any]]]:
    identity = hashlib.sha256(playlist.url.encode()).hexdigest()[:10]
    folder, legacy = collection_folder(output, playlist.title, playlist.owner, identity, f'{mode}-{quality}')
    if folder.is_symlink() or (folder.exists() and folder.is_junction()):
        raise MediaGrabError('The playlist folder must not be a link to another location.')
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / ('.mediagrab.json' if legacy else f'.mediagrab-{identity}-{mode}-{quality}.json')
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if not isinstance(manifest, dict):
                raise ValueError
        except (OSError, ValueError):
            raise MediaGrabError('The download record is invalid. Choose a new output folder to avoid overwriting data.') from None
    results: list[dict[str, Any]] = []
    # Sequential items, at most two fragments in the underlying downloader.
    for entry in entries:
        if notify:
            notify(f'Item {entry.index:02d}: {entry.title}')
        key = f'{entry.index}:{entry.id}' if legacy else f'{identity}:{mode}:{quality}:{entry.index}:{entry.id}'
        row: dict[str, Any] = {'index': entry.index, 'title': entry.title}
        try:
            prior = manifest.get(key, {})
            if not isinstance(prior, dict):
                prior = {}
            previous_name = prior.get('file', '')
            previous = folder / previous_name if isinstance(previous_name, str) else folder
            safe_previous = previous.parent.resolve() == folder.resolve() and not previous.is_symlink()
            if previous_name and safe_previous and previous.is_file() and previous.stat().st_size == prior.get('size') and digest(previous) == prior.get('sha256'):
                row.update(status='skipped', file=previous.name, naming=prior.get('naming', {}))
            else:
                if not entry.url:
                    raise MediaGrabError('The item was removed, is private, or has no usable download URL.')
                stem = previous.stem if previous_name and safe_previous else safe_name(entry.title, 65)
                result = download_one(entry.url, folder, mode, quality, tools_dir, filename_stem=stem,
                                      overwrite=bool(previous_name and safe_previous), reuse_filename=bool(previous_name and safe_previous),
                                      **({"prepared_info": entry.media} if entry.media is not None else {}), **({"session": session} if session else {}))
                manifest[key] = {'file': result.path.name, 'size': result.path.stat().st_size, 'sha256': digest(result.path), 'naming': result.metadata.get('naming', {})}
                write_json(manifest_path, manifest)
                row.update(status='downloaded', file=result.path.name,
                           warning=bool(result.metadata.get('inspection_warning')), naming=result.metadata.get('naming', {}))
        except (MediaGrabError, ValueError, OSError) as error:
            row.update(status='error', reason=str(error) if isinstance(error, MediaGrabError) else explain_error(error))
        except Exception:
            row.update(status='error', reason='Unexpected error for this item. Other items will continue.')
        results.append(row)
        if notify:
            notify({'skipped': 'Valid file already present — skipped.', 'downloaded': 'Downloaded and checked.',
                    'error': 'Error: ' + row.get('reason', '')}[row['status']])
            if row.get('warning'):
                notify('FFprobe reported a warning. Check playback.')
    write_json(folder / ('results.json' if legacy else f'results-{identity}-{mode}-{quality}.json'), results)
    return folder, results
