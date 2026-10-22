# MediaGrab

MediaGrab is a portable Windows app for saving supported videos and audio, managing download queues, and finding podcast episodes from accessible RSS feeds. It offers compatible MP4 video or the best available source audio, with no audio conversion to MP3. An optional Edge bridge can use a browser session you choose to share with the app.

## Download and run

1. Download [MediaGrab-Windows-x64.zip](https://github.com/meobeongungoc/MediaGrab/releases/latest).
2. Extract the **entire** `MediaGrab` folder.
3. Open `MediaGrab.exe` at the top of that folder. Keep the `releases` folder beside it.

The Windows package includes Python, FFmpeg, FFprobe, and Deno. It does not include your settings, downloaded media, logs, browser sessions, or credentials. The Edge extension is optional.

## What to expect

Supported sources and available formats can change when websites change. MediaGrab does not decrypt DRM. Access to account-only content depends on your own authorized session and has not been verified for every site or account. The portable package passed an extracted-package self-test on the build PC; a separate clean-PC test is still pending.

The repository contains source code. Use the Release ZIP above for a ready-to-run Windows copy.
