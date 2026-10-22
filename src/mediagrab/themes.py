"""CustomTkinter adaptation of the user-approved subtitledesk-themes skill."""
import tkinter as tk
from tkinter import font as tkfont
import customtkinter as ctk
from PIL import Image, ImageDraw

THEMES = {
    'Modern Neutral (Blue)': dict(bg='#0c151e', surface='#121d28', field='#203041', selection='#2b5278', accent='#3478d4'),
    'Warm Neutral (Beige/Brown)': dict(bg='#12161b', surface='#1b2026', field='#2b2e34', selection='#655b4e', accent='#c7a681'),
    'Green Accent': dict(bg='#0b191d', surface='#122328', field='#20363b', selection='#2c6056', accent='#589d7c'),
    'Purple Accent': dict(bg='#131320', surface='#1c1b2c', field='#2c293e', selection='#574376', accent='#875bb6'),
}
TEXT, MUTED = '#e8edf3', '#adbac9'


def mix(a, b, fraction):
    return '#' + ''.join(f'{round(int(a[i:i+2],16)+(int(b[i:i+2],16)-int(a[i:i+2],16))*fraction):02x}' for i in (1,3,5))


class SurfaceCard(ctk.CTkFrame):
    """Eight-pixel rounded surface with the skill's restrained top gradient."""
    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        palette = getattr(self, '_skin_palette', None)
        if not palette:
            return
        self._canvas.delete('skin_gradient')
        scale = self._apply_widget_scaling
        width = scale(self._current_width)
        for y in range(2, 11):
            color = mix(palette['surface'], '#ffffff', .025 * (11-y)/9)
            self._canvas.create_line(scale(8), scale(y), max(scale(8), width-scale(8)), scale(y),
                fill=color, width=max(1, scale(1)), tags='skin_gradient')


def line_icon(name, color=TEXT):
    """Code-drawn compact icons adapted from the skill's surfaces.py."""
    shapes = {
        'folder': [(2,5,7,5),(7,5,9,7),(9,7,14,7),(14,7,14,13),(14,13,2,13),(2,13,2,5),(2,9,14,9)],
        'download': [(8,2,8,10),(4,7,8,11),(8,11,12,7),(3,11,3,14),(3,14,13,14),(13,14,13,11)],
        'refresh': [(12,3,13,7),(13,7,9,7),(4,13,3,9),(3,9,7,9)],
        'settings': [(8,1,8,3),(8,13,8,15),(1,8,3,8),(13,8,15,8),(3,3,4,4),(12,12,13,13),(3,13,4,12),(12,4,13,3)],
    }
    image = Image.new('RGBA',(64,64))
    draw = ImageDraw.Draw(image)
    for points in shapes.get(name, []):
        draw.line(tuple(round(v*4) for v in points),fill=color,width=4)
    if name in ('refresh','settings'):
        r = 5 if name == 'refresh' else 4
        draw.ellipse(((8-r)*4,(8-r)*4,(8+r)*4,(8+r)*4),outline=color,width=4)
    return ctk.CTkImage(image, size=(16,16))


def installed_fonts(root):
    return tuple(sorted({f for f in tkfont.families(root) if not f.startswith('@')}, key=str.casefold))


def apply_theme(root, name, family=None):
    palette = THEMES.get(name, THEMES['Modern Neutral (Blue)'])
    accent_text = '#141c22' if name in ('Warm Neutral (Beige/Brown)', 'Green Accent') else '#ffffff'
    root.configure(fg_color=palette['bg'])
    root._skin_palette = palette
    if family:
        root._skin_font = family
    family = getattr(root, '_skin_font', 'Segoe UI')
    for value in ctk.ThemeManager.theme.get('CTkFont', {}).values():
        if isinstance(value, dict):
            value['family'] = family
    defaults = {
        'CTk': dict(fg_color=palette['bg']),
        'CTkToplevel': dict(fg_color=palette['bg']),
        'CTkFrame': dict(fg_color=palette['surface'], top_fg_color=palette['surface'], border_color=mix(palette['field'],'#ffffff',.04), corner_radius=8),
        'CTkButton': dict(fg_color=palette['field'], hover_color=palette['selection'], text_color=TEXT, corner_radius=5, border_width=1, border_color=mix(palette['field'],'#ffffff',.12)),
        'CTkOptionMenu': dict(fg_color=palette['field'], button_color=palette['field'], button_hover_color=palette['selection'], text_color=TEXT, corner_radius=5),
        'CTkComboBox': dict(fg_color=palette['surface'], border_color=palette['field'], button_color=palette['field'], button_hover_color=palette['selection'], text_color=TEXT, corner_radius=5),
        'DropdownMenu': dict(fg_color=palette['field'], hover_color=palette['selection'], text_color=TEXT),
        'CTkEntry': dict(fg_color=palette['surface'], border_color=palette['field'], text_color=TEXT, placeholder_text_color=MUTED, corner_radius=5),
        'CTkTextbox': dict(fg_color=palette['surface'], border_color=palette['field'], text_color=TEXT, corner_radius=8),
        'CTkCheckBox': dict(fg_color=palette['accent'], hover_color=palette['selection'], text_color=TEXT, border_color=palette['field'], checkmark_color=accent_text),
        'CTkProgressBar': dict(fg_color=palette['field'], progress_color=palette['accent']),
        'CTkScrollbar': dict(fg_color=palette['surface'], button_color=palette['field'], button_hover_color=palette['selection']),
        'CTkLabel': dict(text_color=TEXT),
    }
    for kind, values in defaults.items():
        ctk.ThemeManager.theme[kind].update(values)
    seen_fonts = set()
    def visit(widget):
        kind = type(widget).__name__
        if isinstance(widget, SurfaceCard):
            kind = 'CTkFrame'
            widget._skin_palette = palette
        values = dict(defaults.get(kind, {}))
        if kind == 'CTkFrame':
            values.pop('top_fg_color',None)
            if widget.cget('fg_color') == 'transparent': values.pop('fg_color',None)
        if isinstance(widget, (ctk.CTkOptionMenu,ctk.CTkComboBox)):
            values.update(dropdown_fg_color=palette['field'],dropdown_hover_color=palette['selection'],dropdown_text_color=TEXT)
        if getattr(widget,'_skin_role',None) == 'primary':
            values.update(fg_color=palette['accent'],hover_color=mix(palette['accent'],'#ffffff',.10),text_color=accent_text)
            if getattr(widget,'_skin_icon',None): values['image']=line_icon(widget._skin_icon,accent_text)
        elif getattr(widget,'_skin_role',None) == 'muted':
            values['text_color']=MUTED
        if values:
            widget.configure(**values)
        if hasattr(widget,'cget') and isinstance(widget,ctk.CTkBaseClass):
            for key in ('font','dropdown_font','label_font'):
                try:
                    font=widget.cget(key)
                except ValueError:
                    continue
                if isinstance(font,ctk.CTkFont):
                    if id(font) not in seen_fonts:
                        font.configure(family=family);seen_fonts.add(id(font))
                elif isinstance(font,tuple):
                    widget.configure(**{key:(family,*font[1:])})
        if isinstance(widget,tk.PanedWindow):
            widget.configure(background=palette['bg'],sashwidth=6,bd=0)
        if isinstance(widget,tk.Menu):
            # CustomTkinter's DropdownMenu handles its own native platform details.
            if kind != 'DropdownMenu':
                widget.configure(background=palette['field'],foreground=TEXT,activebackground=palette['selection'],activeforeground=TEXT,font=(family,10))
        for child in widget.winfo_children(): visit(child)
    visit(root)
    return palette
