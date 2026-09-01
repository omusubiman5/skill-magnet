from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from .core import SkillMagnetError
from .library_manager import (
    CATALOG_FILENAME,
    DEFAULT_REPOSITORY_NAME,
    LibraryTransaction,
    add_skill,
    initialize_library,
    validate_library,
)


LIBRARY_WIZARD_STEPS = (
    "1. Repository",
    "2. Skill",
    "3. Pack & INDEX",
    "4. Validation",
    "5. Preview",
    "6. Publish",
    "7. Activate & Receipt",
)


def library_wizard_steps() -> tuple[str, ...]:
    return LIBRARY_WIZARD_STEPS


def show_library_manager(
    *,
    config_path: Path,
    state_dir: Path,
    menu_update: Callable[[Path, str], Any] | None = None,
) -> dict[str, Any]:
    """Open the seven-step library manager and return its final status."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        raise SkillMagnetError("Tk is required for the Skill Library Manager UI") from exc

    root = tk.Tk()
    root.title("Skill Magnet — Skill Library Manager")
    root.geometry("920x680")
    root.minsize(760, 560)
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=12, pady=12)
    pages = [ttk.Frame(notebook, padding=12) for _ in LIBRARY_WIZARD_STEPS]
    for title, page in zip(LIBRARY_WIZARD_STEPS, pages, strict=True):
        notebook.add(page, text=title)

    repository = tk.StringVar()
    repository_name = tk.StringVar(value=DEFAULT_REPOSITORY_NAME)
    remote = tk.StringVar()
    skill_id = tk.StringVar()
    display_name = tk.StringVar()
    purpose = tk.StringVar()
    pack_id = tk.StringVar()
    pack_display_name = tk.StringVar()
    import_source = tk.StringVar()
    transaction_id = tk.StringVar()
    platform = tk.StringVar(value="windows" if os.name == "nt" else "macos")
    publish_confirmed = tk.BooleanVar(value=False)
    activate_confirmed = tk.BooleanVar(value=False)
    result: dict[str, Any] = {"status": "closed_without_activation"}

    def row(page: Any, number: int, label: str, variable: Any, browse: Callable[[], None] | None = None) -> None:
        ttk.Label(page, text=label).grid(row=number, column=0, sticky="w", padx=4, pady=5)
        ttk.Entry(page, textvariable=variable, width=74).grid(
            row=number, column=1, sticky="ew", padx=4, pady=5
        )
        if browse is not None:
            ttk.Button(page, text="Browse", command=browse).grid(row=number, column=2, padx=4)
        page.columnconfigure(1, weight=1)

    def select_repository() -> None:
        value = filedialog.askdirectory(title="Select skill library draft")
        if value:
            repository.set(value)

    def select_import() -> None:
        value = filedialog.askdirectory(title="Select skill directory")
        if value:
            import_source.set(value)

    def show_error(exc: Exception) -> None:
        messagebox.showerror("Skill Library Manager", str(exc), parent=root)

    def require_repository() -> Path:
        if not repository.get().strip():
            raise SkillMagnetError("Repository draft directory is required")
        return Path(repository.get()).resolve()

    page = pages[0]
    ttk.Label(
        page,
        text="汎用skill repositoryを新規作成するか、既存draftを接続します。既存名は変更しません。",
        wraplength=820,
    ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
    row(page, 1, "Draft directory", repository, select_repository)
    row(page, 2, "Repository name", repository_name)
    row(page, 3, "Git remote", remote)

    def initialize() -> None:
        try:
            initialized = initialize_library(require_repository(), repository_name.get())
            messagebox.showinfo("Repository", json.dumps(initialized, ensure_ascii=False, indent=2), parent=root)
        except Exception as exc:
            show_error(exc)

    def connect() -> None:
        try:
            catalog = json.loads(
                (require_repository() / CATALOG_FILENAME).read_text(encoding="utf-8")
            )
            repository_name.set(str(catalog["repository"]["name"]))
            messagebox.showinfo("Repository", "Repositoryを接続しました。", parent=root)
            notebook.select(1)
        except Exception as exc:
            show_error(exc)

    ttk.Button(page, text="Create", command=initialize).grid(row=4, column=1, sticky="w", pady=12)
    ttk.Button(page, text="Connect / Next", command=connect).grid(row=4, column=1, sticky="e", pady=12)

    page = pages[1]
    ttk.Label(page, text="新規skillを作成するか、SKILL.mdとacceptance.jsonをimportします。").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
    )
    row(page, 1, "Skill ID", skill_id)
    row(page, 2, "Display name", display_name)
    row(page, 3, "Purpose", purpose)
    row(page, 4, "Pack ID", pack_id)
    row(page, 5, "Pack display name", pack_display_name)
    row(page, 6, "Import directory (optional)", import_source, select_import)

    def add() -> None:
        try:
            added = add_skill(
                require_repository(),
                skill_id=skill_id.get().strip(),
                display_name=display_name.get().strip(),
                purpose=purpose.get().strip(),
                pack_id=pack_id.get().strip(),
                pack_display_name=pack_display_name.get().strip() or None,
                skill_source=Path(import_source.get()) if import_source.get().strip() else None,
            )
            messagebox.showinfo("Skill", json.dumps(added, ensure_ascii=False, indent=2), parent=root)
            load_catalog_editor()
            notebook.select(2)
        except Exception as exc:
            show_error(exc)

    ttk.Button(page, text="Add / Import", command=add).grid(row=7, column=1, sticky="e", pady=12)

    page = pages[2]
    ttk.Label(
        page,
        text="pack順序、skill metadata、depends-on / composes-with / contrasts-withをcatalogで編集します。INDEXはcatalogから生成されます。",
        wraplength=820,
    ).pack(anchor="w", pady=(0, 8))
    catalog_editor = tk.Text(page, wrap="none", undo=True)
    catalog_editor.pack(fill="both", expand=True)

    def load_catalog_editor() -> None:
        try:
            text = (require_repository() / CATALOG_FILENAME).read_text(encoding="utf-8")
            catalog_editor.delete("1.0", "end")
            catalog_editor.insert("1.0", text)
        except Exception as exc:
            show_error(exc)

    def save_catalog() -> None:
        try:
            value = json.loads(catalog_editor.get("1.0", "end"))
            path = require_repository() / CATALOG_FILENAME
            previous = path.read_bytes()
            try:
                path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                from .library_manager import render_index

                (require_repository() / "INDEX.md").write_text(
                    render_index(value), encoding="utf-8", newline="\n"
                )
                validate_library(require_repository())
            except Exception:
                path.write_bytes(previous)
                raise
            messagebox.showinfo("Catalog", "CatalogとINDEXを検証して保存しました。", parent=root)
            notebook.select(3)
        except Exception as exc:
            show_error(exc)

    controls = ttk.Frame(page)
    controls.pack(fill="x", pady=8)
    ttk.Button(controls, text="Reload", command=load_catalog_editor).pack(side="left")
    ttk.Button(controls, text="Validate & Save", command=save_catalog).pack(side="right")

    page = pages[3]
    validation_output = tk.Text(page, wrap="word", state="disabled")
    validation_output.pack(fill="both", expand=True)

    def set_text(widget: Any, value: Any) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", json.dumps(value, ensure_ascii=False, indent=2))
        widget.configure(state="disabled")

    def validate() -> None:
        try:
            checked = validate_library(require_repository()).as_dict()
            set_text(validation_output, checked)
            notebook.select(4)
        except Exception as exc:
            show_error(exc)

    ttk.Button(page, text="Run fail-closed validation", command=validate).pack(anchor="e", pady=8)

    page = pages[4]
    preview_output = tk.Text(page, wrap="word", state="disabled")
    preview_output.pack(fill="both", expand=True)

    def prepare() -> None:
        try:
            if not remote.get().strip():
                raise SkillMagnetError("Git remote is required")
            transaction = LibraryTransaction(state_dir)
            preview = transaction.prepare(
                draft=require_repository(), remote=remote.get().strip()
            )
            transaction_id.set(transaction.transaction_id)
            set_text(preview_output, preview)
            notebook.select(5)
        except Exception as exc:
            show_error(exc)

    ttk.Button(page, text="Prepare isolated preview", command=prepare).pack(anchor="e", pady=8)

    page = pages[5]
    row(page, 0, "Transaction ID", transaction_id)
    ttk.Checkbutton(
        page,
        text="表示されたrepository、branch、file、pack、digestを確認しました",
        variable=publish_confirmed,
    ).grid(row=1, column=1, sticky="w", pady=8)
    publish_output = tk.Text(page, height=18, wrap="word", state="disabled")
    publish_output.grid(row=2, column=0, columnspan=3, sticky="nsew")
    page.rowconfigure(2, weight=1)

    def transaction() -> LibraryTransaction:
        if not transaction_id.get().strip():
            raise SkillMagnetError("Transaction ID is required")
        return LibraryTransaction(state_dir, transaction_id.get().strip())

    def publish() -> None:
        try:
            if not publish_confirmed.get() or not messagebox.askyesno(
                "Publish", "専用branchへcommit・pushしてPRを作成しますか？", parent=root
            ):
                raise SkillMagnetError("Publish was not explicitly confirmed")
            published = transaction().publish(confirmed=True)
            set_text(publish_output, published)
        except Exception as exc:
            show_error(exc)

    def verify_merged() -> None:
        try:
            verified = transaction().mark_merged()
            set_text(publish_output, verified)
            notebook.select(6)
        except Exception as exc:
            show_error(exc)

    buttons = ttk.Frame(page)
    buttons.grid(row=3, column=0, columnspan=3, sticky="e", pady=8)
    ttk.Button(buttons, text="Publish PR", command=publish).pack(side="left", padx=4)
    ttk.Button(buttons, text="Verify merged remote", command=verify_merged).pack(side="left", padx=4)

    page = pages[6]
    ttk.Label(page, text="Platform").grid(row=0, column=0, sticky="w")
    ttk.Combobox(page, textvariable=platform, values=("windows", "macos"), state="readonly").grid(
        row=0, column=1, sticky="w"
    )
    ttk.Checkbutton(
        page,
        text="検証済みcommitを本体へ有効化し、必要な場合だけmenuを更新します",
        variable=activate_confirmed,
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=8)
    receipt_output = tk.Text(page, wrap="word", state="disabled")
    receipt_output.grid(row=2, column=0, columnspan=3, sticky="nsew")
    page.rowconfigure(2, weight=1)
    page.columnconfigure(1, weight=1)

    def activate() -> None:
        nonlocal result
        try:
            if not activate_confirmed.get() or not messagebox.askyesno(
                "Activate", "現在の有効版を更新しますか？失敗時は直前版へ戻します。", parent=root
            ):
                raise SkillMagnetError("Activation was not explicitly confirmed")

            def update(path: Path) -> Any:
                return menu_update(path, platform.get()) if menu_update else None

            result = transaction().activate(
                config_path=config_path,
                confirmed=True,
                menu_update=update if menu_update else None,
            )
            set_text(receipt_output, result)
            messagebox.showinfo("Skill Library Manager", "有効化が完了しました。", parent=root)
        except Exception as exc:
            show_error(exc)

    ttk.Button(page, text="Publish and Activate", command=activate).grid(
        row=3, column=2, sticky="e", pady=8
    )

    root.mainloop()
    return result
