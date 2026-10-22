"""Desktop entry point and isolated worker modes for the packaged app."""
import json
import os
import sys
from pathlib import Path

APP_ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
UPDATE_ROOT = APP_ROOT / '.updates'
try:
    active = (UPDATE_ROOT / 'active.txt').read_text('utf-8').strip()
    if active.startswith('v-') and Path(active).name == active and (UPDATE_ROOT / active).is_dir():
        sys.path.insert(0, str(UPDATE_ROOT / active))
except OSError:
    pass
if not getattr(sys, 'frozen', False):
    for variable, subdir in (('TCL_LIBRARY', 'tcl8.6'), ('TK_LIBRARY', 'tk8.6')):
        location = Path(sys.base_prefix) / 'tcl' / subdir
        if location.is_dir():
            os.environ[variable] = str(location)

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(APP_ROOT / 'src'))

if __name__ == '__main__':
    if any(arg.startswith('chrome-extension://') for arg in sys.argv[1:]):
        from mediagrab.core.edge_bridge import native_main
        raise SystemExit(native_main())
    elif '--install-edge-host' in sys.argv:
        from mediagrab.core.edge_bridge import install_host
        if not getattr(sys, 'frozen', False):
            raise SystemExit('Use the packaged MediaGrab EXE to register the Edge host.')
        install_host(Path(sys.executable))
        print('Edge host registered for the current Windows user.')
    elif '--worker' in sys.argv:
        from mediagrab.service import execute_job
        from mediagrab.core.errors import MediaGrabError
        try:
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
            job = json.loads(sys.stdin.read())
            code = execute_job(job)
        except MediaGrabError as error:
            print('Error: ' + str(error), flush=True)
            code = 1
        except Exception:
            print('Unexpected error. Completion is not confirmed; check the source and app tools.', flush=True)
            code = 1
        raise SystemExit(code)
    elif '--update-backend' in sys.argv:
        from mediagrab.core.updater import update_main
        raise SystemExit(update_main(UPDATE_ROOT))
    elif '--self-test' in sys.argv:
        from mediagrab.config import TOOLS
        from mediagrab.core.options import build_options
        import customtkinter, defusedxml, yt_dlp, yt_dlp_ejs
        import tkinter
        from mediagrab.gui import App
        window = App()
        window.withdraw()
        window.update()
        window.destroy()
        assert all((TOOLS / name).is_file() for name in ('ffmpeg.exe', 'ffprobe.exe', 'deno.exe'))
        print('PASS: runtime, GUI, downloader, RSS and bundled tools.')
    else:
        from mediagrab.gui import main
        main()
