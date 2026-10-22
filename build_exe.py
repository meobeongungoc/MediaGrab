"""Build on Windows from the project's virtual environment."""
import os
import shutil
import argparse
import tomllib
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description='Build a new release without overwriting user app data.')
parser.add_argument('--release-dir', type=Path)
options = parser.parse_args()
version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8-sig'))['project']['version']
release = (options.release_dir or root / 'releases' / version).resolve()
if not release.is_relative_to(root.resolve()):
    raise SystemExit('Release output must remain inside this workspace.')
if (release / 'MediaGrab').exists():
    raise SystemExit('Release already exists. Choose a new --release-dir; existing app files and user data will not be overwritten.')
env = dict(os.environ)
tcl = Path(sys.base_prefix) / 'tcl'
env['TCL_LIBRARY'] = str(tcl / 'tcl8.6')
env['TK_LIBRARY'] = str(tcl / 'tk8.6')
args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--console',
        '--distpath', str(release), '--hide-console', 'hide-early', '--name', 'MediaGrab', '--paths', 'src', '--additional-hooks-dir', 'hooks',
        '--hidden-import', '_tkinter', '--collect-all', 'tkinter',
        '--collect-all', 'customtkinter', '--collect-all', 'yt_dlp',
        '--collect-all', 'yt_dlp_ejs', '--collect-all', 'pip',
        '--copy-metadata', 'yt-dlp', '--copy-metadata', 'yt-dlp-ejs']
for name in ('ffmpeg.exe', 'ffprobe.exe', 'deno.exe'):
    args += ['--add-binary', f'{name};.']
args += ['--add-data', f'{root / "assets"};assets', '--icon', str(root / 'assets' / 'app-logo-original.png')]
for source, target in (('tcl8.6', '_tcl_data'), ('tk8.6', '_tk_data')):
    args += ['--add-data', f'{tcl / source};{target}']
args += ['launch_gui.py']
code = subprocess.call(args, cwd=root, env=env)
if code == 0:
    shutil.copytree(root / 'edge-extension', release / 'MediaGrab' / 'edge-extension')
raise SystemExit(code)
