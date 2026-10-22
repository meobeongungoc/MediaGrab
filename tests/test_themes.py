"""Behavioral checks for the shared skill's live theme, font and layout requirements."""
import json
import sys
from pathlib import Path
import customtkinter as ctk
import pytest
from mediagrab import config, gui
from mediagrab.themes import THEMES, apply_theme


@pytest.fixture(scope='module')
def app(tmp_path_factory):
    tmp_path=tmp_path_factory.mktemp('theme-ui')
    monkeypatch=pytest.MonkeyPatch()
    for key,folder in [('TCL_LIBRARY','tcl8.6'),('TK_LIBRARY','tk8.6')]:
        location=Path(sys.base_prefix)/'tcl'/folder
        if location.is_dir():monkeypatch.setenv(key,str(location))
    monkeypatch.setattr(config,'CONFIG',tmp_path/'settings.json')
    monkeypatch.setattr(gui,'load_settings',config.load_settings)
    monkeypatch.setattr(gui,'save_settings',config.save_settings)
    instance=gui.App()
    callback_errors=[]
    instance.report_callback_exception=lambda *error:callback_errors.append(error)
    instance.update()
    yield instance
    instance.executor.shutdown(wait=False,cancel_futures=True)
    instance.destroy()
    monkeypatch.undo()
    assert not callback_errors


def test_all_themes_update_dropdowns_dialogs_and_text_contrast(app):
    app.appearance()
    dialog=next(w for w in app.winfo_children() if isinstance(w,ctk.CTkToplevel))
    for name,palette in THEMES.items():
        app.theme.set(name)
        app.change_theme(name)
        app.update()
        assert app.cget('fg_color')==palette['bg']
        assert dialog.cget('fg_color')==palette['bg']
        assert app.browser.cget('dropdown_fg_color')==palette['field']
        assert app.browser.cget('dropdown_hover_color')==palette['selection']
        expected='#141c22' if name in ('Warm Neutral (Beige/Brown)','Green Accent') else '#ffffff'
        assert app.prepare_button.cget('text_color')==expected
        assert app.options._canvas.find_withtag('skin_gradient')
    dialog.destroy()


def test_saved_font_and_theme_survive_reload_and_reject_secret_keys(app):
    font='Arial' if 'Arial' in app.font_families else app.font_families[0]
    assert not any(name.startswith('@') for name in app.font_families)
    app.font_family=font
    app.theme.set('Purple Accent')
    apply_theme(app,app.theme.get(),font)
    app.save_appearance()
    saved=config.load_settings()
    assert saved['font']==font and saved['theme']=='Purple Accent'
    assert app.prepare_button.cget('font').cget('family')==font
    config.save_settings({**saved,'cookies':'SYNTHETIC_SECRET','token':'SYNTHETIC_SECRET'})
    assert 'SYNTHETIC_SECRET' not in config.CONFIG.read_text('utf-8')


def test_narrow_and_wide_options_reflow_without_hiding_controls(app):
    app.geometry('680x780');app.update()
    assert app._option_columns==2
    for _,control in app.option_fields:
        assert control.winfo_ismapped()
        assert control.winfo_x()>=0
        assert control.winfo_x()+control.winfo_width() <= app.options.winfo_width()+2
    app.geometry('1060x780');app.update()
    assert app._option_columns==3
    assert app.panes.panes()
    assert len(app.panes.panes())==2


def test_new_selection_window_uses_current_palette_and_has_all_items(app):
    app.theme.set('Green Accent');app.change_theme('Green Accent')
    app.selection({'kind':'members'},'Members-only',[(i,str(i),f'Video {i}') for i in range(1,151)])
    app.update()
    dialog=next(w for w in app.winfo_children() if isinstance(w,ctk.CTkToplevel))
    assert dialog.cget('fg_color')==THEMES['Green Accent']['bg']
    def controls(widget):
        for child in widget.winfo_children():
            yield child
            yield from controls(child)
    boxes=[w for w in controls(dialog) if isinstance(w,ctk.CTkCheckBox)]
    assert len(boxes)==150 and all(w.get() for w in boxes)
    dialog.destroy()


def test_extension_resolves_inside_standalone_portable_release(tmp_path):
    app=tmp_path/'Portable MediaGrab'
    extension=app/'edge-extension'
    extension.mkdir(parents=True)
    (extension/'manifest.json').write_text('{"manifest_version":3}',encoding='utf-8')
    assert config.edge_extension_dir(app)==extension


def test_missing_extension_is_not_silently_claimed_available(tmp_path):
    with pytest.raises(FileNotFoundError,match='complete MediaGrab folder'):
        config.edge_extension_dir(tmp_path/'missing'/'app')
