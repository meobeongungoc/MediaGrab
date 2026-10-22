"""Music metadata recognition and Windows-safe names, without guessing artist order."""
import re
import unicodedata
from pathlib import Path
from typing import Any


def safe_name(value: str, limit: int = 80) -> str:
    value = unicodedata.normalize('NFC', value).replace('%', '_')
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', value).strip(' .')
    value = value[:limit].rstrip(' .') or 'Untitled'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', value, re.I):
        value = '_' + value
    return value


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ''


def recognize_music(info: dict[str, Any]) -> dict[str, Any]:
    original = text(info.get('title')) or 'Untitled'
    track = text(info.get('track'))
    artist = text(info.get('artist'))
    if not artist and isinstance(info.get('artists'), list):
        artist = ', '.join(text(a) for a in info['artists'] if text(a))
    result: dict[str, Any] = {'title': original, 'artist': artist, 'method': 'source_title'}
    if track:
        result.update(title=track, method='source_metadata')
        return result
    # Only strip known presentation labels; retain live/remix/version information.
    cleaned = re.sub(r'\s*[\[(](?:official\s+)?(?:music\s+video|video|audio|lyric\s+video|lyrics)[\])]\s*', ' ', original, flags=re.I).strip()
    parts = [p.strip() for p in re.split(r'\s+[-–—|]\s+', cleaned)]
    if artist and len(parts) == 2:
        normalized = lambda value: unicodedata.normalize('NFC', value).casefold().strip()
        for index in (0, 1):
            if normalized(parts[index]) == normalized(artist) and parts[1-index]:
                result.update(title=parts[1-index], method='title_with_known_artist')
                return result
    # A separator alone cannot establish which half is the performer.
    if len(parts) == 2 and all(parts):
        result['needs_review'] = True
    return result


def collection_owner(info: dict[str, Any]) -> str:
    return text(info.get('channel')) or text(info.get('uploader'))


def collection_folder(output: Path, title: str, owner: str, identity: str, variant: str) -> tuple[Path, bool]:
    legacy = output / f'{safe_name(title, 45)} [{identity}] - {variant}'
    # Keep existing records usable; do not move or rename earlier downloads.
    if legacy.exists():
        folder, old = legacy, True
    else:
        folder, old = output / safe_name(owner or title, 45), False
    if folder.is_symlink() or (folder.exists() and folder.is_junction()):
        from .errors import MediaGrabError
        raise MediaGrabError('The output folder must not be a filesystem link.')
    return folder, old


def reserve_name(folder: Path, title: str) -> tuple[str, Path]:
    """Reserve a unique title across concurrent workers; never overwrite other media."""
    base = safe_name(title, 65)
    for number in range(1, 10001):
        stem = base if number == 1 else f'{base} ({number})'
        if any(p.is_file() and p.stem.casefold() == stem.casefold() for p in folder.iterdir()):
            continue
        lock = folder / f'.{stem}.reserve'
        try:
            with lock.open('x', encoding='utf-8'):
                pass
            # A concurrent worker may have completed just before the reservation.
            if any(p.is_file() and p != lock and p.stem.casefold() == stem.casefold() for p in folder.iterdir()):
                lock.unlink()
                continue
            return stem, lock
        except FileExistsError:
            continue
    from .errors import MediaGrabError
    raise MediaGrabError('Too many files share this title. Choose a different output folder.')
