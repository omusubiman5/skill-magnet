"""Isolated close-path diagnosis; does not register, publish or install anything."""
import ctypes
import faulthandler
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from skill_magnet.activation import ActivationEngine
from skill_magnet.core import Config
from skill_magnet.library_ui import show_library_manager
from skill_magnet.ui import show_context_selection, tk_top_level_window_handle

started = time.monotonic()
def note(event, **values):
    print(json.dumps(dict(event=event, elapsed=round(time.monotonic()-started, 3), **values)), flush=True)

original_protocol = tk.Wm.protocol
def protocol(self, name=None, func=None):
    if name == "WM_DELETE_WINDOW" and callable(func):
        def traced():
            note("close_callback_enter", title=self.title(), callback=func.__name__)
            try:
                return func()
            finally:
                note("close_callback_return", callback=func.__name__)
        return original_protocol(self, name, traced)
    return original_protocol(self, name, func)
tk.Wm.protocol = protocol
original_loop = tk.Tk.mainloop
def loop(self, n=0):
    title = self.title()
    note("mainloop_enter", title=title)
    def operate():
        if title == "Library Manager":
            hwnd = tk_top_level_window_handle(self)
            note("post_close", hwnd=hwnd, title=self.title())
            ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), 0x10, 0, 0)
        else:
            buttons = [w for w in self.winfo_children() if w.winfo_class() == "TButton" and str(w.cget("text")) == "Library Manager"]
            assert len(buttons) == 1, buttons
            note("manager_button_invoke")
            buttons[0].invoke()
    self.after(1000, operate)
    result = original_loop(self, n)
    note("mainloop_return", title=title)
    return result
tk.Tk.mainloop = loop
faulthandler.dump_traceback_later(6, repeat=False)
config = Path(__file__).resolve().parents[1] / "skill-magnet.json"
with tempfile.TemporaryDirectory(prefix="skill-magnet-close-probe-") as scratch:
    base = Path(scratch)
    project = base / "project"
    project.mkdir()
    if sys.argv[1] == "transition":
        action = show_context_selection(ActivationEngine(Config.load(config), base / "state"), platform="windows", project=project, allow_dynamic_selection=True, library_manager=lambda _: None)
        note("chooser_return", action=str(action))
        assert tk._default_root is None, "chooser left a live default root"
    result = show_library_manager(config_path=config, state_dir=base / "state")
    note("manager_return", status=result.get("status"))
    assert not (base / "state" / "library-manager.owner.json").exists()
faulthandler.cancel_dump_traceback_later()
