"""Map user choices to yt-dlp library options without network access."""
from pathlib import Path
from typing import Any, Literal

Mode = Literal["mp4", "audio"]
Quality = Literal["best", "compat"]


def build_options(output: Path, mode: Mode, quality: Quality, tools_dir: Path,
                  source: str = "youtube") -> dict[str, Any]:
    if mode not in ("mp4", "audio") or quality not in ("best", "compat"):
        raise ValueError("Invalid format selection. Use video or Best audio.")
    if source == "soundcloud" and mode == "mp4":
        raise ValueError("SoundCloud is audio-only. Select Best audio or use --audio.")
    options: dict[str, Any] = {
        "outtmpl": str(output / "%(title).65s.%(ext)s"),
        "windowsfilenames": True, "trim_file_name": 150,
        "noplaylist": True, "extract_flat": "in_playlist", "lazy_playlist": True,
        "quiet": True, "noprogress": True, "no_warnings": False,
        "cachedir": False, "retries": 3, "fragment_retries": 3,
        "extractor_retries": 3, "socket_timeout": 20,
        "concurrent_fragment_downloads": 2,
        "ffmpeg_location": str(tools_dir),
        "js_runtimes": {"deno": {"path": str(tools_dir / "deno.exe")}},
        "overwrites": False,
    }
    processors: list[dict[str, Any]] = []
    if mode == "mp4":
        if quality == "compat":
            options["format"] = "bv[vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1][acodec^=mp4a]"
            if source == "web":
                options["format"] = "bv[vcodec~='^(avc1|h264)']+ba[acodec~='^(mp4a|aac)']/b[ext=mp4][vcodec~=?'^(avc1|h264)'][acodec~=?'^(mp4a|aac)']"
            container = "mp4"
        else:
            options["format"] = "bv+ba/b"
            container = "mkv"
        options["merge_output_format"] = container
        processors.append({"key": "FFmpegVideoRemuxer", "preferedformat": container})
    else:
        options["format"] = "download/ba/b" if source == "soundcloud" else "ba/b"
        # Copy the best available audio codec without lossy re-encoding.
        processors.append({"key": "FFmpegExtractAudio", "preferredcodec": "best"})
    processors.append({"key": "FFmpegMetadata"})
    options["postprocessors"] = processors
    return options
