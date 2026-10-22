"""Single-item downloads and media inspection; no UI dependencies."""
import json
from copy import deepcopy
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from .errors import MediaGrabError, explain_error
from .options import Mode, Quality, build_options
from .auth import AuthSession, apply_auth_limits, fresh_edge_session, session_rejected
from .errors import SessionRejected, MembershipRequired, MEMBERSHIP_MESSAGE, membership_required
from .naming import recognize_music, reserve_name

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    metadata: dict[str, Any]


def validate_url(url: str, *, allow_web: bool = False) -> str:
    if allow_web:
        from .webpage import validate_web_url
        validate_web_url(url)
        return "web"
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise ValueError
        if parsed.port not in (None, 80, 443):
            raise ValueError
    except ValueError:
        raise MediaGrabError("Invalid URL. Paste a public YouTube or SoundCloud URL.") from None
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"):
        parts = parsed.path.strip("/").split("/")
        valid = (host == "youtu.be" and len(parts) == 1 and bool(parts[0])) or (
            parsed.path == "/watch" and bool(parse_qs(parsed.query).get("v"))) or (
            len(parts) == 2 and parts[0] in ("shorts", "embed", "live") and bool(parts[1]))
        if not valid:
            raise MediaGrabError("A single-video URL is required here. Use the playlist workflow for a playlist.")
        return "youtube"
    if host in ("soundcloud.com", "www.soundcloud.com", "m.soundcloud.com", "on.soundcloud.com"):
        parts = parsed.path.strip("/").split("/")
        if "sets" in parts or not parsed.path.strip("/") or (host != "on.soundcloud.com" and len(parts) < 2):
            raise MediaGrabError("A single SoundCloud track URL is required here. Use the playlist workflow for sets.")
        return "soundcloud"
    raise MediaGrabError("For Spotify/RSS, use the podcast source selector in the app. Direct video downloads support YouTube and SoundCloud.")


class SafeLogger:
    def debug(self, message: str) -> None:
        pass  # Never persist raw extractor output (it can contain signed URLs).

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        if any(note in message.lower() for note in ('falling back on generic information extractor',
                                                    'forcing generic information extractor',
                                                    'url could be a direct video link, returning it as such')):
            return
        LOG.warning("Source warning: %s", explain_error(message))

    def error(self, message: str) -> None:
        pass  # The final DownloadError is translated by download_one.


def source_has_audio(info: dict[str, Any]) -> bool:
    selected = info.get('requested_formats') or [info]
    return any(f.get('acodec') not in (None, 'none') for f in selected)


def inspect_media(path: Path, tools_dir: Path, *, require_audio: bool = True) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [str(tools_dir / "ffprobe.exe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.returncode:
            raise MediaGrabError("The file was saved but FFprobe cannot read it. Download is not confirmed.")
        data = json.loads(process.stdout)
        data["inspection_warning"] = bool(process.stderr.strip())
        if require_audio and not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
            raise MediaGrabError("The saved file has no audio stream. Download is not confirmed.")
        return data
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise MediaGrabError("The file was saved but could not be checked with FFprobe.") from None


@fresh_edge_session
def download_one(url: str, output: Path, mode: Mode, quality: Quality, tools_dir: Path,
                 progress: Callable[[dict[str, Any]], None] | None = None, *,
                 filename_stem: str | None = None, overwrite: bool = False,
                 session: AuthSession | None = None,
                 prepared_info: dict[str, Any] | None = None,
                 reuse_filename: bool = False) -> DownloadResult:
    source = validate_url(url, allow_web=prepared_info is not None)
    options = build_options(output, mode, quality, tools_dir, source)
    if session and source != "youtube":
        raise MediaGrabError("This login session can only be used for YouTube.")
    apply_auth_limits(options, session)
    if filename_stem is not None:
        if Path(filename_stem).name != filename_stem or any(c in filename_stem for c in ("/", "\\")):
            raise MediaGrabError("Invalid filename.")
        options["outtmpl"] = str(output / (filename_stem.replace("%", "%%") + ".%(ext)s"))
        options["overwrites"] = overwrite
    for executable in ("ffmpeg.exe", "ffprobe.exe", "deno.exe"):
        if not (tools_dir / executable).is_file():
            raise MediaGrabError(f"Missing {executable} in tools folder: {tools_dir}")
    final_paths: list[Path] = []
    reservation: Path | None = None
    naming: dict[str, Any] = {}
    options["logger"] = SafeLogger()
    options["post_hooks"] = [lambda name: final_paths.append(Path(name))]
    if progress:
        def safe_progress(event: dict[str, Any]) -> None:
            progress({key: event.get(key) for key in ("status", "downloaded_bytes", "total_bytes", "total_bytes_estimate")})
        options["progress_hooks"] = [safe_progress]
    try:
        output.mkdir(parents=True, exist_ok=True)
        with YoutubeDL(options) as downloader:
            if session:
                session.attach(downloader)
            info = deepcopy(prepared_info) if prepared_info is not None else downloader.extract_info(url, download=False)
            if prepared_info is not None:
                for key in ('requested_downloads', 'requested_formats', 'format_id', 'format', 'filepath', '_filename'):
                    info.pop(key, None)
            if not info or info.get("_type") in ("playlist", "multi_video") or "entries" in info:
                raise MediaGrabError("A single item is required here. Use the playlist workflow for lists.")
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
                raise MediaGrabError("Live, upcoming or still-processing videos are not supported.")
            if info.get("has_drm"):
                raise MediaGrabError("DRM-protected content is not supported.")
            naming = recognize_music(info)
            info['title'] = naming['title']
            if naming.get('artist'):
                info['artist'] = naming['artist']
            if naming['method'] != 'source_title':
                info['track'] = naming['title']
            if not reuse_filename:
                stem, reservation = reserve_name(output, naming['title'])
                downloader.params['outtmpl']['default'] = str(output / (stem.replace('%', '%%') + '.%(ext)s'))
            for key in ('filepath', '_filename', 'filename', '__finaldir'):
                info.pop(key, None)
            downloader.process_ie_result(info, download=True)
    except (DownloadError, ExtractorError, OSError) as error:
        if membership_required(error):
            raise MembershipRequired(MEMBERSHIP_MESSAGE) from None
        if session and session.source == 'edge-extension' and session_rejected(error):
            raise SessionRejected('YouTube rejected the current session.') from None
        raise MediaGrabError(explain_error(error)) from None
    finally:
        if reservation is not None:
            reservation.unlink(missing_ok=True)
    if len(final_paths) != 1 or not final_paths[0].is_file():
        raise MediaGrabError("Output file could not be identified. Download is not confirmed.")
    path = final_paths[0]
    data = inspect_media(path, tools_dir, require_audio=mode == "audio" or source_has_audio(info))
    if mode == "mp4" and not any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in data["streams"]):
        raise MediaGrabError("This file has no video stream. Choose Best audio for this source.")
    if source == "web" and mode == "mp4" and quality == "compat":
        for stream in data['streams']:
            if stream.get('disposition', {}).get('attached_pic'):
                continue
            if ((stream.get('codec_type') == 'video' and stream.get('codec_name') != 'h264') or
                    (stream.get('codec_type') == 'audio' and stream.get('codec_name') != 'aac')):
                raise MediaGrabError('The source does not provide H.264/AAC compatible MP4. The saved file was not re-encoded. Choose Best quality (MKV).')
    data["naming"] = naming
    LOG.info("Media file downloaded and checked.")
    return DownloadResult(path, data)
