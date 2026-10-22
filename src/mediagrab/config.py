"""Per-user preferences contain no cookies, private feeds or download history."""
import json
import sys
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
TOOLS = Path(getattr(sys, '_MEIPASS', ROOT))
CONFIG = ROOT / 'mediagrab-settings.json'


def load_settings() -> dict:
    try:
        data = json.loads(CONFIG.read_text('utf-8'))
        settings = {k: v for k, v in data.items() if k in ('output', 'mode', 'quality', 'browser', 'theme', 'font') and isinstance(v, str)}
        if settings.get('mode') in ('MP3', 'Âm thanh gốc', 'mp3', 'audio'):
            settings['mode'] = 'Best audio'
        if settings.get('mode') not in ('MP4', 'Best audio'):
            settings['mode'] = 'Best audio'
        settings['quality'] = {'MP4 tương thích': 'Compatible MP4', 'Cao nhất (MKV)': 'Best quality (MKV)'}.get(settings.get('quality'), settings.get('quality', 'Compatible MP4'))
        settings['browser'] = {'Không đăng nhập': 'No login', 'File cookie': 'No login'}.get(settings.get('browser'), settings.get('browser', 'No login'))
        return settings
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    allowed = {k: v for k, v in data.items() if k in ('output', 'mode', 'quality', 'browser', 'theme', 'font')}
    CONFIG.write_text(json.dumps(allowed, ensure_ascii=False, indent=2), encoding='utf-8')


def edge_extension_dir(app_root: Path = ROOT) -> Path:
    """Find extension files in a portable release or the development layout."""
    for folder in (app_root / 'edge-extension', app_root.parent.parent.parent / 'edge-extension'):
        if (folder / 'manifest.json').is_file():
            return folder
    raise FileNotFoundError('Keep the complete MediaGrab folder, including edge-extension, together.')
