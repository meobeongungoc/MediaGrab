"""Vietnamese command-line interface."""
import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from .core.downloader import download_one
from .core.errors import MediaGrabError
from .core.auth import load_session
from .core.playlist import playlist_url, discover, select_entries, download_playlist
from .core.webpage import discover_page
from .core.members import discover_members, download_members

ROOT = Path(__file__).resolve().parents[2]


class VietnameseParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_help(sys.stderr)
        self.exit(2, "\nInvalid command. Check the URL and options shown above.\n")


def parser() -> argparse.ArgumentParser:
    result = VietnameseParser(description="MediaGrab — download a YouTube/SoundCloud item or playlist.", add_help=False)
    result._positionals.title = "Input"
    result._optionals.title = "Options"
    result.usage = "%(prog)s LINK [--mp4 | --audio] [--quality compat|best] [--output OUTPUT_FOLDER] [--list | --items ITEMS --yes]"
    result.add_argument("url", nargs="?", help="Video or audio URL")
    result.add_argument("-h", "--help", action="help", help="Show help")
    modes = result.add_mutually_exclusive_group()
    modes.add_argument("--mp4", action="store_true", help="Video: compatible MP4 or best-quality MKV")
    modes.add_argument("--audio", action="store_true", help="Best available audio; keep source encoding")
    result.add_argument("--quality", choices=("best", "compat"), default="compat", help="compat: H.264/AAC MP4; best: highest-quality MKV")
    result.add_argument("--output", type=Path, default=ROOT / "downloads", help="Output folder")
    result.add_argument("--tools-dir", type=Path, default=ROOT, help="Folder containing FFmpeg, FFprobe and Deno")
    result.add_argument("--list", action="store_true", help="List items without downloading")
    result.add_argument("--items", help="Select items: 1,3-4 or all")
    result.add_argument("--yes", action="store_true", help="Download selected items without prompting")
    result.add_argument("--webpage", action="store_true", help="Find and select embedded media on a public page or forum post")
    result.add_argument("--single", action="store_true", help="Download only the video from a playlist URL")
    result.add_argument("--members-only", action="store_true", help="List or download all members-only uploads of a YouTube channel; requires account access")
    auth = result.add_mutually_exclusive_group()
    auth.add_argument("--browser", choices=("edge-extension", "edge", "chrome", "firefox"), help="Read a YouTube login session from your browser; edge-extension avoids cookie file decryption")
    auth.add_argument("--cookies", type=Path, help="Netscape cookie file outside the project folder")
    result.add_argument("--profile", help="Profile name, e.g. Default or Profile 1")
    return result


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    cli = parser()
    args = cli.parse_args(argv)
    if not args.url:
        cli.error("missing URL")
    mode = "audio" if args.audio else "mp4"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if mode != "mp4" and args.quality != "compat":
        cli.error("quality is only applicable to video")
    if mode == "mp4" and args.quality == "best":
        print("Best quality: MKV output, without video re-encoding.")
    if args.members_only and (args.webpage or args.single):
        cli.error('members-only cannot be combined with webpage or single')
    is_playlist = args.members_only or args.webpage or (playlist_url(args.url) is not None and not args.single)
    if not is_playlist and (args.list or args.items or args.yes):
        cli.error("playlist options require playlist URL")
    last_bucket = -1

    def progress(event: dict[str, Any]) -> None:
        nonlocal last_bucket
        total = event.get("total_bytes") or event.get("total_bytes_estimate")
        if event.get("status") == "downloading" and total:
            percent = min(100, int(100 * (event.get("downloaded_bytes") or 0) / total))
            bucket = percent // 10
            if bucket > last_bucket:
                print(f"Downloading: {percent}%", flush=True)
                last_bucket = bucket
        elif event.get("status") == "finished":
            print("Stream downloaded; processing the file...", flush=True)
            last_bucket = -1

    try:
        output = args.output.expanduser().resolve()
        if len(str(output)) > 80:
            raise MediaGrabError("Output path is too long. Choose a shorter folder, such as D:/Downloads.")
        if args.webpage and (args.browser or args.cookies or args.single):
            raise MediaGrabError("Use --webpage with No login and without --single.")
        session = load_session(ROOT, args.browser, args.profile, args.cookies)
        if session:
            print("Browser session loaded. Membership access still depends on the account and video.")
        if is_playlist:
            playlist = discover_members(args.url, session) if args.members_only else discover_page(args.url, args.tools_dir.resolve()) if args.webpage else discover(args.url, session)
            print(f"Playlist: {playlist.title} — {len(playlist.entries)} items")
            for entry in playlist.entries:
                print(f"{entry.index:02d}. {entry.title}")
            if args.list:
                return 0
            selection = args.items
            if not args.yes:
                if not sys.stdin.isatty():
                    raise MediaGrabError("Use --list first, then add --items 1-4 --yes to download.")
                selection = input(f"Select item numbers (Enter = {selection or 'all'}, q = cancel): ").strip() or selection
                if selection == 'q':
                    return 0
            entries = select_entries(playlist.entries, selection)
            download_batch = download_members if args.members_only else download_playlist
            folder, rows = download_batch(playlist, entries, output, mode, args.quality, args.tools_dir.resolve(), lambda message: print(message, flush=True), session=session)
            downloaded = sum(r['status'] == 'downloaded' for r in rows)
            skipped = sum(r['status'] == 'skipped' for r in rows)
            errors = sum(r['status'] == 'error' for r in rows)
            print(f"Downloaded: {downloaded} / Already present: {skipped} / Failed: {errors}")
            print(f"Folder: {folder}")
            print(f"Reports: results*.json in {folder}")
            return 1 if errors else 0
        result = download_one(args.url, output, mode, args.quality, args.tools_dir.resolve(), progress, session=session)
    except (MediaGrabError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled. Run the same command to retry the unfinished download.", file=sys.stderr)
        return 130
    except Exception:
        print("Unexpected error. Download is not confirmed; check the downloader version.", file=sys.stderr)
        return 1
    print(f"Saved: {result.path}")
    if result.metadata.get("inspection_warning"):
        print("Warning: FFprobe reported a problem. Check playback; FFmpeg/FFprobe may need an update.")
    for stream in result.metadata.get("streams", []):
        if stream.get("disposition", {}).get("attached_pic"):
            print("Cover art: included")
            continue
        if stream.get("codec_type") == "video":
            print(f"Video: {stream.get('width')}×{stream.get('height')} — {stream.get('codec_name')}")
        if stream.get("codec_type") == "audio":
            bitrate = stream.get("bit_rate")
            rate = f"{int(bitrate) / 1000:.0f} kbps" if bitrate else "source does not report a separate bitrate"
            print(f"Audio: {stream.get('codec_name')} — {rate}")
    duration = float(result.metadata.get("format", {}).get("duration") or 0)
    print(f"Duration: {duration:.1f} seconds; size: {result.path.stat().st_size / 1048576:.2f} MB")
    return 0
