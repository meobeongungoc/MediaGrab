"""Explicit local backend update; packages are installed only in app storage."""
from pathlib import Path
import uuid


def update_args(target: Path) -> list[str]:
    return ['install', '--disable-pip-version-check', '--no-input', '--upgrade', '--only-binary=:all:',
            '--target', str(target), '--index-url', 'https://pypi.org/simple', 'yt-dlp[default]']


def update_main(target: Path) -> int:
    from pip._internal.cli.main import main
    target.mkdir(parents=True, exist_ok=True)
    version = 'v-' + uuid.uuid4().hex
    staging = target / version
    code = main(update_args(staging))
    if code == 0 and (staging / 'yt_dlp' / '__init__.py').is_file():
        marker = target / 'active.tmp'
        marker.write_text(version, encoding='utf-8')
        marker.replace(target / 'active.txt')
        return 0
    return code or 1
