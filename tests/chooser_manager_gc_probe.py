from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import threading
import time
import tkinter
import types
import weakref


def _manager_button(root: tkinter.Tk) -> tkinter.Widget | None:
    pending = list(root.winfo_children())
    while pending:
        widget = pending.pop()
        try:
            if str(widget.cget("text")) == "Library Manager":
                return widget
        except tkinter.TclError:
            pass
        pending.extend(widget.winfo_children())
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--disable-main-thread-gc", action="store_true")
    args = parser.parse_args()

    original_tk = tkinter.Tk
    selector_root: weakref.ReferenceType[tkinter.Tk] | None = None
    root_count = 0

    def scheduled_tk(*tk_args: object, **tk_kwargs: object) -> tkinter.Tk:
        nonlocal selector_root, root_count
        root = original_tk(*tk_args, **tk_kwargs)
        root_count += 1
        if root_count == 1:
            selector_root = weakref.ref(root)

            def choose_manager() -> None:
                button = _manager_button(root)
                if button is None:
                    raise RuntimeError("Library Manager button was not created")
                button.invoke()

            root.after(100, choose_manager)
        return root

    tkinter.Tk = scheduled_tk  # type: ignore[assignment]
    try:
        from skill_magnet import cli

        if args.disable_main_thread_gc:
            cli.gc = types.SimpleNamespace(collect=lambda: None)
            cli.show_context_error = lambda *messages: print(messages[-1])

        worker_done = threading.Event()
        worker_error: list[str] = []

        def manager_worker(*_args: object, **_kwargs: object) -> None:
            if selector_root is None or selector_root() is not None:
                raise RuntimeError("destroyed selector is still retained before Manager worker")

            def collect_destroyed_selector() -> None:
                try:
                    time.sleep(0.05)
                    gc.collect()
                except BaseException as exc:
                    worker_error.append(repr(exc))
                finally:
                    worker_done.set()

            worker = threading.Thread(target=collect_destroyed_selector, daemon=True)
            worker.start()
            worker.join(timeout=5)
            if worker.is_alive():
                raise RuntimeError("Manager worker did not finish collecting")

        cli._show_library_manager_ui = manager_worker  # type: ignore[assignment]
        code = cli.main(
            [
                "--config",
                str(args.config),
                "--state-dir",
                str(args.state_dir),
                "context",
                "--platform",
                "windows",
                "--project",
                str(args.project),
                "--launcher",
            ]
        )
        if code != 0 or not worker_done.is_set() or worker_error:
            raise RuntimeError(
                f"context result={code}; worker_done={worker_done.is_set()}; "
                f"worker_error={worker_error}"
            )
    finally:
        tkinter.Tk = original_tk  # type: ignore[assignment]

    print(json.dumps({"status": "ok", "worker_gc_completed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
