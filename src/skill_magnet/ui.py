from __future__ import annotations

from pathlib import Path

from .activation import ActivationEngine, LaunchContract


def show_context_selection(
    engine: ActivationEngine, *, platform: str, project: Path
) -> LaunchContract | None:
    """Minimal shared UI used by both OS context-menu adapters."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("Skill Magnet")
    root.resizable(False, False)
    selected_pack = tk.StringVar(value="")
    purpose = tk.StringVar()
    summary = tk.StringVar(value="Select one skill pack explicitly.")
    result: dict[str, LaunchContract] = {}

    ttk.Label(root, text=f"Project: {project.resolve()}").grid(
        row=0, column=0, columnspan=2, padx=12, pady=8, sticky="w"
    )
    ttk.Label(root, text="Skill pack").grid(row=1, column=0, padx=12, sticky="w")
    pack_box = ttk.Combobox(
        root,
        textvariable=selected_pack,
        values=tuple(engine.config.packs),
        state="readonly",
    )
    pack_box.grid(row=1, column=1, padx=12, pady=4)

    def update_pack_summary(_: object = None) -> None:
        if not selected_pack.get():
            summary.set("Select one skill pack explicitly.")
            return
        pack = engine.config.packs[selected_pack.get()]
        summary.set(f"Pack purpose: {pack.purpose}\nVersion: {pack.expected_commit}")

    pack_box.bind("<<ComboboxSelected>>", update_pack_summary)
    ttk.Label(root, text="Purpose").grid(row=2, column=0, padx=12, sticky="w")
    ttk.Entry(root, textvariable=purpose, width=48).grid(
        row=2, column=1, padx=12, pady=4
    )
    ttk.Label(root, textvariable=summary, wraplength=520).grid(
        row=3, column=0, columnspan=2, padx=12, pady=8, sticky="w"
    )

    def confirm() -> None:
        try:
            plan = engine.plan(
                platform=platform,
                project=project,
                pack_id=selected_pack.get(),
                runtime="codex",
                purpose=purpose.get(),
            )
        except Exception as exc:
            messagebox.showerror("Skill Magnet", str(exc), parent=root)
            return
        detail = (
            f"Pack: {plan['pack_id']}\nCommit: {plan['commit_sha']}\n"
            f"Target: Codex / {plan['project']}\nPurpose: {plan['purpose']}\n\n"
            "No skill will be installed locally. Launch this verified task?"
        )
        if not messagebox.askyesno("Confirm Skill Magnet launch", detail, parent=root):
            return
        result["contract"] = engine.confirm(plan, confirmed=True)
        root.destroy()

    ttk.Button(root, text="Confirm and create launch", command=confirm).grid(
        row=4, column=0, padx=12, pady=12
    )
    ttk.Button(root, text="Cancel", command=root.destroy).grid(
        row=4, column=1, padx=12, pady=12
    )
    root.mainloop()
    return result.get("contract")
