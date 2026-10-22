"""Windows GUI: all Tk calls stay on the UI thread; work runs in subprocesses."""
import json
import os
import queue
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .config import ROOT, TOOLS, load_settings, save_settings, edge_extension_dir
from .core.auth import load_session
from .core.errors import MediaGrabError
from .core.playlist import playlist_url, discover
from .core.podcast import find_feeds, read_feed
from .core.webpage import discover_page
from .core.members import discover_members
from .core.logging_utils import redact, local_logger
from .themes import THEMES, apply_theme, installed_fonts, line_icon, SurfaceCard


def command(mode: str = '') -> list[str]:
    base = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(ROOT / 'launch_gui.py')]
    return base + ([mode] if mode else [])


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title('MediaGrab 0.8.2 — Video and Podcast Downloader')
        scale = self._get_window_scaling()
        self.geometry(f'{int(min(1060, self.winfo_screenwidth()/scale - 60))}x{int(min(800, self.winfo_screenheight()/scale - 80))}')
        self.minsize(680, 580)
        self.events = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.auth_lock = threading.Lock()
        self.jobs = []
        try:
            self.safe_log = local_logger(ROOT)
        except OSError:
            self.safe_log = None
        self.busy_prepare = False
        self.updating = False
        self.cookie_file = ''
        self.settings = load_settings()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(7, weight=1)
        self.font_families = installed_fonts(self)
        self.font_family = self.settings.get('font', 'Segoe UI')
        if self.font_family not in self.font_families:
            self.font_family = 'Segoe UI' if 'Segoe UI' in self.font_families else self.font_families[0]
        logo = TOOLS / 'assets' / 'app.ico'
        if logo.exists():
            self.iconbitmap(str(logo))
        self.bind_all('<Map>', self._style_auxiliary, add='+')
        header = ctk.CTkFrame(self, fg_color='transparent')
        header.grid(row=0, column=0, sticky='ew', padx=20, pady=(14, 4))
        self.theme = ctk.CTkOptionMenu(header, values=list(THEMES), command=self.change_theme, width=225)
        self.theme.set(self.settings.get('theme') if self.settings.get('theme') in THEMES else 'Modern Neutral (Blue)')
        self.theme.pack(side='left')
        ctk.CTkButton(header, text='Appearance', image=line_icon('settings'), width=120, command=self.appearance).pack(side='right')
        hint = ctk.CTkLabel(self, text='Paste a link, choose your quality, then download.', anchor='w')
        hint._skin_role = 'muted'
        hint.grid(row=1, column=0, sticky='ew', padx=20)
        self.url = ctk.CTkEntry(self, placeholder_text='Video, channel, forum post, Spotify show or RSS URL', height=36)
        self.url.grid(row=2, column=0, sticky='ew', padx=20, pady=(5, 10))
        self.url.bind('<Return>', lambda event: self.prepare())
        self.options = SurfaceCard(self)
        self.options.grid(row=3, column=0, sticky='ew', padx=20)
        self.source = ctk.CTkOptionMenu(self.options, values=['Video / SoundCloud', 'Channel members-only', 'Web page / Forum', 'Podcast Spotify / RSS'], width=190)
        self.mode = ctk.CTkOptionMenu(self.options, values=['MP4', 'Best audio'], width=120)
        self.mode.set(self.settings.get('mode', 'MP4'))
        self.quality = ctk.CTkOptionMenu(self.options, values=['Compatible MP4', 'Best quality (MKV)'], width=170)
        self.quality.set(self.settings.get('quality', 'Compatible MP4'))
        self.browser = ctk.CTkOptionMenu(self.options, values=['No login', 'Edge extension', 'Edge', 'Chrome', 'Firefox', 'Cookie file'], width=170)
        self.browser.set(self.settings.get('browser', 'No login'))
        self.profile = ctk.CTkEntry(self.options, placeholder_text='Default / Profile 1', width=140)
        session_tools = ctk.CTkFrame(self.options, fg_color='transparent')
        ctk.CTkButton(session_tools, text='Cookie file', width=90, command=self.choose_cookie).pack(side='left', padx=(0,5))
        ctk.CTkButton(session_tools, text='Edge setup', width=95, command=self.setup_edge).pack(side='left')
        self.option_fields=[]
        for label, control in [('Source',self.source),('Save as',self.mode),('Quality',self.quality),('Login',self.browser),('Browser profile (optional)',self.profile),('Session',session_tools)]:
            caption=ctk.CTkLabel(self.options,text=label,anchor='w',height=18)
            caption._skin_role='muted'
            self.option_fields.append((caption,control))
        self._option_columns=0
        self.options.bind('<Configure>', self._reflow_options, add='+')
        paths = ctk.CTkFrame(self, fg_color='transparent')
        paths.grid(row=4, column=0, sticky='ew', padx=20, pady=10)
        paths.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(paths,text='Save to').grid(row=0,column=0,padx=(0,10))
        self.output = ctk.CTkEntry(paths)
        self.output.insert(0, self.settings.get('output', str(ROOT / 'downloads')))
        self.output.grid(row=0, column=1, sticky='ew')
        ctk.CTkButton(paths, text='Browse', image=line_icon('folder'), width=100, command=self.choose_output).grid(row=0, column=2, padx=(10, 0))
        actions = ctk.CTkFrame(self, fg_color='transparent')
        actions.grid(row=5, column=0, sticky='ew', padx=20)
        self.prepare_button = ctk.CTkButton(actions, text='Download', image=line_icon('download'), command=self.prepare, height=34, width=145)
        self.prepare_button._skin_role='primary'
        self.prepare_button._skin_icon='download'
        self.prepare_button.pack(side='left')
        ctk.CTkButton(actions, text='Open folder', image=line_icon('folder'), width=125, command=self.open_output).pack(side='left', padx=8)
        ctk.CTkButton(actions, text='Update downloader', image=line_icon('refresh'), width=160, command=self.update_backend).pack(side='right')
        self.status = ctk.CTkLabel(self, text='Playlists save to folders. Original audio quality; no MP3 conversion.', anchor='w', justify='left')
        self.status._skin_role='muted'
        self.status.grid(row=6, column=0, sticky='ew', padx=20, pady=8)
        self.panes = tk.PanedWindow(self, orient='vertical', sashwidth=6, borderwidth=0)
        self.panes.grid(row=7,column=0,sticky='nsew',padx=20,pady=(0,14))
        queue_pane=ctk.CTkFrame(self.panes,fg_color='transparent')
        queue_pane.grid_columnconfigure(0,weight=1);queue_pane.grid_rowconfigure(0,weight=1)
        self.rows = ctk.CTkScrollableFrame(queue_pane, label_text='Downloads — 2 public jobs; signed-in jobs run one at a time')
        self.rows.grid(row=0,column=0,sticky='nsew')
        log_pane=ctk.CTkFrame(self.panes,fg_color='transparent')
        log_pane.grid_columnconfigure(0,weight=1);log_pane.grid_rowconfigure(1,weight=1)
        caption=ctk.CTkLabel(log_pane,text='Activity',anchor='w');caption._skin_role='muted'
        caption.grid(row=0,column=0,sticky='ew')
        self.log = ctk.CTkTextbox(log_pane,height=100)
        self.log.grid(row=1,column=0,sticky='nsew')
        self.log.configure(state='disabled')
        self.panes.add(queue_pane,minsize=90,stretch='always')
        self.panes.add(log_pane,minsize=90,height=120,stretch='never')
        self.bind('<Configure>',self._resize_status,add='+')
        self.protocol('WM_DELETE_WINDOW', self.close)
        apply_theme(self, self.theme.get(),self.font_family)
        self.after(100, self.poll)

    def _resize_status(self,event):
        if event.widget is self:
            self.status.configure(wraplength=max(320,int(event.width/self._get_window_scaling())-45))

    def _reflow_options(self,event=None):
        width=self.options.winfo_width()/self.options._get_widget_scaling()
        columns=3 if width>=850 else 2
        if columns==self._option_columns:
            return
        self._option_columns=columns
        for column in range(3): self.options.grid_columnconfigure(column,weight=1 if column<columns else 0)
        for index,(label,control) in enumerate(self.option_fields):
            row,column=divmod(index,columns)
            label.grid(row=row*2,column=column,sticky='ew',padx=12,pady=(4,0))
            control.grid(row=row*2+1,column=column,sticky='ew',padx=12,pady=(0,5))

    def _style_auxiliary(self,event):
        if isinstance(event.widget,ctk.CTkToplevel):
            apply_theme(self,self.theme.get(),self.font_family)
            logo=TOOLS/'assets'/'app.ico'
            if logo.exists(): event.widget.iconbitmap(str(logo))

    def appearance(self):
        dialog=ctk.CTkToplevel(self)
        dialog.title('Appearance')
        dialog.geometry('470x235');dialog.transient(self)
        dialog.grid_columnconfigure(0,weight=1)
        ctk.CTkLabel(dialog,text='Font — choose or type an installed family',anchor='w').grid(row=0,column=0,sticky='ew',padx=20,pady=(18,5))
        font=ctk.CTkComboBox(dialog,values=list(self.font_families))
        font.set(self.font_family);font.grid(row=1,column=0,sticky='ew',padx=20)
        ctk.CTkLabel(dialog,text='The selected font and theme are saved for the next launch.',wraplength=420).grid(row=2,column=0,padx=20,pady=10)
        def accept():
            choices={name.casefold():name for name in self.font_families}
            selected=choices.get(font.get().strip().casefold())
            if not selected:
                messagebox.showinfo('Font unavailable','Choose a font installed on this computer.',parent=dialog)
                return
            self.font_family=selected
            apply_theme(self,self.theme.get(),selected)
            self.save_appearance()
            dialog.destroy()
        ctk.CTkButton(dialog,text='Apply',command=accept).grid(row=3,column=0,padx=20,pady=8,sticky='e')
        apply_theme(self,self.theme.get(),self.font_family)

    def save_appearance(self):
        try:
            save_settings({**load_settings(),'theme':self.theme.get(),'font':self.font_family})
        except OSError:
            self.status.configure(text='Appearance applied; could not save the preference.')

    def change_theme(self, name: str) -> None:
        apply_theme(self, name,self.font_family)
        self.settings.update(theme=name)
        self.save_appearance()

    def setup_edge(self) -> None:
        if not getattr(sys, 'frozen', False):
            messagebox.showinfo('Edge setup', 'Open the packaged MediaGrab EXE to set up the Edge extension.')
            return
        from .core.edge_bridge import install_host
        try:
            install_host(Path(sys.executable))
            extension = edge_extension_dir(ROOT)
            os.startfile(extension)
            self.browser.set('Edge extension')
            messagebox.showinfo('Edge setup', 'Host registered. In Edge, open edge://extensions, enable Developer mode, click Load unpacked and select the edge-extension folder just opened. Then open the extension and click Connect this profile. Keep Edge open. Use Edge extension as the login source in MediaGrab.')
        except Exception:
            messagebox.showerror('Edge setup', 'Could not complete Edge setup. Keep the full MediaGrab folder together and try again.')

    def choose_cookie(self) -> None:
        path = filedialog.askopenfilename(title='Choose a Netscape cookie file outside the app/project folder', filetypes=[('Cookie', '*.txt')])
        if path:
            if Path(path).resolve().is_relative_to(ROOT.resolve()):
                messagebox.showerror('Cookie location', 'Keep the cookie file outside the app/project folder.')
                return
            self.cookie_file = path
            self.browser.set('Cookie file')

    def choose_output(self) -> None:
        value = filedialog.askdirectory(title='Choose output folder')
        if value:
            self.output.delete(0, 'end')
            self.output.insert(0, value)

    def open_output(self) -> None:
        path = Path(self.output.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def snapshot(self) -> dict:
        browser = self.browser.get()
        mode = {'MP4': 'mp4', 'Best audio': 'audio'}[self.mode.get()]
        return {'url': self.url.get().strip(), 'kind': 'single', 'mode': mode,
                'quality': 'best' if self.quality.get() == 'Best quality (MKV)' else 'compat',
                'output': self.output.get(), 'browser': 'edge-extension' if browser == 'Edge extension' else browser.lower() if browser in ('Edge', 'Chrome', 'Firefox') else '',
                'profile': self.profile.get().strip() if browser in ('Edge', 'Chrome', 'Firefox') else '', 'cookies': self.cookie_file if browser == 'Cookie file' else '',
                'webpage': self.source.get() == 'Web page / Forum',
                'members': self.source.get() == 'Channel members-only',
                'podcast': self.source.get().startswith('Podcast') or 'open.spotify.com/show/' in self.url.get()}

    def prepare(self) -> None:
        if self.busy_prepare or self.updating:
            return
        job = self.snapshot()
        if job['podcast'] and job['mode'] == 'mp4':
            job['mode'] = 'audio'
            self.mode.set('Best audio')
        if not job['url']:
            self.status.configure(text='Paste a URL first.')
            return
        try:
            save_settings({'output': self.output.get(), 'mode': self.mode.get(), 'quality': self.quality.get(), 'browser': self.browser.get() if self.browser.get() != 'Cookie file' else 'No login', 'theme': self.theme.get(), 'font':self.font_family})
        except OSError:
            self.status.configure(text='Could not save settings. Check folder write permission.')
            return
        self.busy_prepare = True
        self.prepare_button.configure(state='disabled')
        self.status.configure(text='Loading information... You can continue using this window.')
        def work() -> None:
            try:
                if job.get('webpage'):
                    if job['browser'] or job['cookies']:
                        raise MediaGrabError('Web page / Forum currently supports public pages. Select No login; YouTube cookies are not used here.')
                    pl = discover_page(job['url'], TOOLS)
                    job['kind'] = 'webpage'
                    self.events.put(('selection', job, pl.title, [(e.index, e.id, e.title) for e in pl.entries]))
                elif job['podcast']:
                    if job['mode'] == 'mp4':
                        raise MediaGrabError('Podcast RSS uses Best audio; no audio conversion is performed.')
                    if job['browser'] or job['cookies']:
                        raise MediaGrabError('Podcast RSS does not use YouTube cookies. Select No login.')
                    candidates = find_feeds(job['url'])
                    self.events.put(('candidates', job, candidates))
                else:
                    session = load_session(ROOT, job['browser'] or None, job['profile'] or None, Path(job['cookies']) if job['cookies'] else None)
                    if job.get('members'):
                        pl = discover_members(job['url'], session)
                        job['kind'] = 'members'
                        self.events.put(('selection', job, pl.title, [(e.index, e.id, e.title) for e in pl.entries]))
                    elif playlist_url(job['url']):
                        pl = discover(job['url'], session)
                        job['kind'] = 'playlist'
                        self.events.put(('selection', job, pl.title, [(e.index, e.id, e.title) for e in pl.entries]))
                    else:
                        # Read selected formats to estimate size before queueing large videos.
                        from yt_dlp import YoutubeDL
                        from .core.options import build_options
                        from .core.downloader import SafeLogger, validate_url
                        opts = build_options(Path(job['output']), job['mode'], job['quality'], TOOLS, validate_url(job['url']))
                        opts['logger'] = SafeLogger()
                        with YoutubeDL(opts) as ydl:
                            if session:
                                session.attach(ydl)
                            info = ydl.extract_info(job['url'], download=False)
                        formats = info.get('requested_formats') or [info]
                        size = sum(f.get('filesize') or f.get('filesize_approx') or 0 for f in formats)
                        job['label'] = info.get('title', 'One video')
                        self.events.put(('confirm', job, size))
            except Exception as error:
                self.events.put(('error', str(error) if isinstance(error, MediaGrabError) else 'Could not load the URL. Check the source, connection and selected login session.'))
        threading.Thread(target=work, daemon=True).start()

    def candidates(self, job: dict, candidates: list[dict]) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title('Confirm RSS source')
        dialog.geometry('700x420')
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text='Confirm the show and author before loading episodes.', wraplength=650).pack(pady=16)
        scroll = ctk.CTkScrollableFrame(dialog)
        scroll.pack(fill='both', expand=True, padx=15, pady=10)
        for candidate in candidates:
            def choose(c=candidate) -> None:
                dialog.destroy()
                self.busy_prepare = True
                self.prepare_button.configure(state='disabled')
                def work() -> None:
                    try:
                        feed = read_feed(c['url'])
                        job.update(kind='podcast', url=c['url'])
                        self.events.put(('selection', job, feed.title, [(e.index, e.id, e.title) for e in feed.episodes]))
                    except Exception:
                        self.events.put(('error', 'Could not read the selected RSS feed. It may have expired or restricted access.'))
                threading.Thread(target=work, daemon=True).start()
            ctk.CTkButton(scroll, text=f"{candidate['title']} — {candidate['author']}", command=choose, height=45).pack(fill='x', pady=5)

    def selection(self, job: dict, title: str, entries: list[tuple]) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title('Select items')
        dialog.geometry('790x620')
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text=f'{title} — {len(entries)} items found' + ('\nCurrent page only; hidden videos and other comment pages may be missing.' if job['kind'] == 'webpage' else ''), wraplength=740).pack(pady=15)
        tools = ctk.CTkFrame(dialog)
        tools.pack(fill='x', padx=15)
        choices = []
        def set_all(value: bool) -> None:
            for _, _, flag in choices:
                flag.set(value)
        ctk.CTkButton(tools, text='Select all', command=lambda: set_all(True)).pack(side='left', padx=8, pady=8)
        ctk.CTkButton(tools, text='Deselect all', command=lambda: set_all(False)).pack(side='left', padx=8)
        scroll = ctk.CTkScrollableFrame(dialog)
        scroll.pack(fill='both', expand=True, padx=15, pady=12)
        for index, ident, name in entries:
            flag = ctk.BooleanVar(value=True)
            choices.append((index, ident, flag))
            ctk.CTkCheckBox(scroll, text=f'{index:03d}. {name[:90]}', variable=flag).pack(anchor='w', pady=6)
        def accept() -> None:
            selected = [(index, ident) for index, ident, flag in choices if flag.get()]
            if not selected:
                messagebox.showinfo('Select items', 'Select at least one item.', parent=dialog)
                return
            job.update(ids=[ident for _, ident in selected], items=','.join(str(i) for i, _ in selected), label=f'{title} ({len(selected)} items)')
            dialog.destroy()
            self.enqueue(job)
        ctk.CTkButton(dialog, text='Download selected', command=accept, height=38).pack(pady=(0, 15))

    def enqueue(self, job: dict) -> None:
        signature = (job['kind'], job['url'], str(Path(job['output']).resolve()), job['mode'], job['quality'])
        for active in self.jobs:
            other = active['job']
            previous = (other['kind'], other['url'], str(Path(other['output']).resolve()), other['mode'], other['quality'])
            if active['state'] in ('waiting', 'running') and signature == previous:
                self.status.configure(text='This download is already queued. Wait for it to finish before adding it again.')
                return
        row = ctk.CTkFrame(self.rows)
        row.pack(fill='x', pady=5)
        row.grid_columnconfigure(0,weight=1)
        label = ctk.CTkLabel(row, text=job.get('label', 'Download'), anchor='w')
        label.grid(row=0,column=0,columnspan=3,sticky='ew',padx=10,pady=(5,0))
        progress = ctk.CTkProgressBar(row, width=120)
        progress.set(0)
        progress.grid(row=1,column=0,sticky='ew',padx=10,pady=(0,8))
        item = {'job': job, 'row': row, 'label': label, 'progress': progress, 'cancel': threading.Event(), 'process': None, 'state': 'waiting'}
        ctk.CTkButton(row, text='Cancel', width=60, command=lambda: self.cancel(item)).grid(row=1,column=1,padx=5,pady=(0,8))
        ctk.CTkButton(row, text='Retry', width=70, command=lambda: self.retry(item)).grid(row=1,column=2,padx=5,pady=(0,8))
        self.jobs.append(item)
        apply_theme(self,self.theme.get(),self.font_family)
        self.executor.submit(self.run_job, item)

    def retry(self, item: dict) -> None:
        if item['state'] in ('done', 'failed', 'cancelled') and not self.updating:
            self.enqueue(dict(item['job']))

    def cancel(self, item: dict) -> None:
        item['cancel'].set()
        process = item.get('process')
        if process and process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    def run_job(self, item: dict) -> None:
        lock = self.auth_lock if item['job'].get('browser') or item['job'].get('cookies') else threading.Lock()
        with lock:
            if item['cancel'].is_set():
                self.events.put(('done', item, 'cancelled'))
                return
            item['state'] = 'running'
            self.events.put(('line', item, 'Downloading...'))
            try:
                p = subprocess.Popen(command('--worker'), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
                item['process'] = p
                p.stdin.write(json.dumps(item['job'], ensure_ascii=False))
                p.stdin.close()
                if item['cancel'].is_set():
                    self.cancel(item)
                for line in p.stdout:
                    self.events.put(('line', item, line.strip()))
                code = p.wait()
                self.events.put(('done', item, 'cancelled' if item['cancel'].is_set() else 'done' if code == 0 else 'failed'))
            except Exception:
                self.events.put(('line', item, 'Could not start the download. Check folder permissions and app files.'))
                self.events.put(('done', item, 'failed'))

    def update_backend(self) -> None:
        if self.updating or self.busy_prepare or any(j['state'] in ('waiting', 'running') for j in self.jobs):
            messagebox.showinfo('Update', 'Wait for downloads to finish, or cancel them before updating.')
            return
        self.updating = True
        self.prepare_button.configure(state='disabled')
        self.status.configure(text='Updating the downloader inside the app folder...')
        def work() -> None:
            try:
                r = subprocess.run(command('--update-backend'), capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                self.events.put(('updated', r.returncode))
            except Exception:
                self.events.put(('updated', 1))
        threading.Thread(target=work, daemon=True).start()

    def poll(self) -> None:
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind in ('error', 'selection', 'candidates', 'confirm'):
                self.busy_prepare = False
                self.prepare_button.configure(state='normal')
                self.status.configure(text='Ready.')
            if kind == 'error':
                self.status.configure(text=event[1])
            elif kind == 'candidates':
                self.candidates(event[1], event[2])
            elif kind == 'selection':
                self.selection(event[1], event[2], event[3])
            elif kind == 'confirm':
                _, job, size = event
                estimate = f'Approximately {size / 1048576:.1f} MB before processing' if size else 'Size is not reported by the source. Long or 4K videos can be large.'
                if messagebox.askyesno('Download', f"{job['label']}\n{estimate}\n\nStart download?", parent=self):
                    self.enqueue(job)
            elif kind == 'line':
                _, item, text = event
                text = redact(text)
                if text.startswith('PROGRESS:'):
                    try:
                        item['progress'].set(float(text.split(':')[1]) / 100)
                    except ValueError:
                        pass
                else:
                    if self.safe_log:
                        self.safe_log.info(text)
                    self.log.configure(state='normal')
                    self.log.insert('end', text + '\n')
                    self.log.see('end')
                    self.log.configure(state='disabled')
            elif kind == 'done':
                _, item, state = event
                item['state'] = state
                label = {'done': 'Done', 'failed': 'Failed — see details below', 'cancelled': 'Cancelled'}[state]
                item['label'].configure(text=f"{item['job'].get('label', 'Download')[:45]} — {label}")
                item['progress'].set(1 if state == 'done' else 0)
            elif kind == 'updated':
                self.updating = False
                self.prepare_button.configure(state='normal')
                self.status.configure(text='Update completed. Restart MediaGrab to use the new downloader.' if event[1] == 0 else 'Update failed. Check the network and folder permissions. The previous version is retained.')
        self.after(100, self.poll)

    def close(self) -> None:
        active = [j for j in self.jobs if j['state'] in ('waiting', 'running')]
        if active and not messagebox.askyesno('Close application', 'Cancel active downloads and close the window?', parent=self):
            return
        for item in active:
            self.cancel(item)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main() -> None:
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('blue')
    App().mainloop()


if __name__ == '__main__':
    main()
