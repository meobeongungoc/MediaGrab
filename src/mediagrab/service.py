"""Run one GUI job in a child process, keeping secrets off the command line."""
from pathlib import Path
from .config import ROOT, TOOLS
from .core.auth import load_session
from .core.downloader import download_one
from .core.errors import MediaGrabError
from .core.playlist import discover, select_entries, download_playlist
from .core.podcast import read_feed, download_feed
from .core.webpage import discover_page
from .core.members import discover_members, download_members


def execute_job(job: dict) -> int:
    output = Path(job['output']).expanduser().resolve()
    if len(str(output)) > 80:
        raise MediaGrabError('Output path is too long. Choose a folder path shorter than 80 characters.')
    mode, quality = job.get('mode', 'mp4'), job.get('quality', 'compat')
    if mode not in ('mp4', 'audio'):
        raise MediaGrabError('Audio conversion has been removed. Select Best audio to preserve source quality.')
    if job.get('kind') not in ('single', 'playlist', 'webpage', 'podcast', 'members'):
        raise MediaGrabError('Invalid download type. Reload the link and try again.')
    if quality not in ('best', 'compat'):
        raise MediaGrabError('Invalid quality. Select Compatible MP4 or Best quality (MKV).')
    if job['kind'] == 'podcast':
        if not job.get('ids'):
            raise MediaGrabError('Select at least one podcast episode before downloading.')
        if mode != 'audio' or job.get('browser') or job.get('cookies'):
            raise MediaGrabError('Podcast RSS requires Best audio and No login.')
    if job['kind'] == 'webpage' and (job.get('browser') or job.get('cookies')):
        raise MediaGrabError('Web pages require No login. YouTube sessions cannot be used here.')
    session = load_session(ROOT, job.get('browser') or None, job.get('profile') or None,
                           Path(job['cookies']) if job.get('cookies') else None)
    total, completed = 0, 0
    def notify(text: str) -> None:
        nonlocal completed
        print(text, flush=True)
        if total and text.startswith(('Downloaded.', 'Downloaded and checked.', 'Already present', 'Valid file already present', 'Error:')):
            completed += 1
            print(f'PROGRESS:{min(100, int(completed * 100 / total))}', flush=True)
    if job['kind'] == 'podcast':
        feed = read_feed(job['url'])
        wanted = set(job.get('ids', []))
        indices = {e.index for e in feed.episodes if e.id in wanted}
        if len(indices) != len(wanted):
            raise MediaGrabError('The RSS feed changed or selected episodes are missing. Reload the episode list.')
        total = len(indices)
        folder, rows = download_feed(feed, indices, output, mode, TOOLS, notify)
    elif job['kind'] in ('playlist', 'webpage', 'members'):
        playlist = discover_members(job['url'], session) if job['kind'] == 'members' else discover_page(job['url'], TOOLS) if job['kind'] == 'webpage' else discover(job['url'], session)
        selected = select_entries(playlist.entries, job.get('items'))
        expected = job.get('ids')
        if expected and [e.id for e in selected] != expected:
            raise MediaGrabError('Playlist order or contents changed. Reload the list.')
        total = len(selected)
        download_batch = download_members if job['kind'] == 'members' else download_playlist
        folder, rows = download_batch(playlist, selected, output, mode, quality, TOOLS, notify, session=session)
    else:
        def progress(event: dict) -> None:
            total = event.get('total_bytes') or event.get('total_bytes_estimate')
            if total and event.get('status') == 'downloading':
                print(f"PROGRESS:{min(100, int(100 * (event.get('downloaded_bytes') or 0) / total))}", flush=True)
        result = download_one(job['url'], output, mode, quality, TOOLS, progress, session=session)
        print('Saved and checked: ' + result.path.name, flush=True)
        if result.metadata.get('inspection_warning'):
            notify('The file has an inspection warning. Check playback.')
        return 0
    errors = sum(r['status'] == 'error' for r in rows)
    good = [folder / r['file'] for r in rows if r['status'] != 'error']
    notify(f"Completed: {len(good)} items with files / {errors} failed. Details: results*.json in the output folder.")
    notify('Saved folder: ' + str(folder))
    return 1 if errors else 0
