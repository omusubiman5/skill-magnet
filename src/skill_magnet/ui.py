from __future__ import annotations

import hashlib
import json
import os
import webbrowser
from pathlib import Path
from typing import Callable

from .activation import ActivationEngine, LaunchContract
from .core import SkillMagnetError


def context_error_message(error: Exception | str) -> str:
    message = str(error)
    if "Pack HEAD is not the pinned expected_commit" in message:
        message += (
            "\n\nUpdate safely: review and approve the new source commit; update the "
            "configured expected commit and skill digests; reinstall the Explorer "
            "menu; verify the selected leaf matches; then retry from a clean source HEAD."
        )
    return message


def show_context_error(message: str) -> None:
    """Show the only normal Explorer UI: one human-readable failure dialog."""
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            message,
            "Skill Magnet",
            0x00000000 | 0x00000010 | 0x00010000,
        )
        return

    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror("Skill Magnet", message, parent=root)
    finally:
        root.quit()
    root.destroy()


def deliver_web_claude_prompt(prompt: str, destination: str) -> None:
    """Open Web Claude and retain exactly one complete prompt on the clipboard."""
    if os.name != "nt":
        raise SkillMagnetError("Web Claude Explorer handoff is only implemented on Windows")
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        root.clipboard_clear()
        root.clipboard_append(prompt)
        root.update()
        if not webbrowser.open(destination, new=2):
            raise SkillMagnetError("Web Claude could not be opened")
    finally:
        root.destroy()


def context_selection_details(
    engine: ActivationEngine,
    *,
    project: Path,
    pack_id: str,
    skill_id: str | None = None,
    runtime: str,
    menu_commit: str | None = None,
    menu_skill_digest: str | None = None,
    menu_instruction_digest: str | None = None,
    menu_acceptance_digest: str | None = None,
) -> dict[str, object]:
    if pack_id not in engine.config.packs:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="unknown_pack"
        )
        raise SkillMagnetError(f"Unknown skill pack: {pack_id}")
    if runtime not in {"codex", "claude"}:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="unknown_runtime"
        )
        raise SkillMagnetError(f"Unknown target AI: {runtime}")
    pack = engine.config.packs[pack_id]
    if skill_id is not None and skill_id not in pack.skills:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="unknown_skill"
        )
        raise SkillMagnetError(f"Unknown skill for pack {pack_id}: {skill_id}")
    skill_digest = hashlib.sha256(
        json.dumps(pack.skills, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    if menu_commit is not None and menu_commit != pack.expected_commit:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="stale_menu_commit"
        )
        raise SkillMagnetError("Pack version changed after menu installation; reinstall required")
    if menu_skill_digest is not None and menu_skill_digest != skill_digest:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="stale_menu_membership"
        )
        raise SkillMagnetError("Pack membership changed after menu installation; reinstall required")
    selected_skills = (skill_id,) if skill_id is not None else pack.skills
    instruction_digest = (
        engine.approved_blob_digest(pack, skill_id, "SKILL.md")
        if skill_id is not None
        else None
    )
    acceptance_digest = (
        engine.approved_blob_digest(pack, skill_id, "acceptance.json")
        if skill_id is not None
        else None
    )
    if menu_instruction_digest is not None and menu_instruction_digest != instruction_digest:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="stale_menu_instruction"
        )
        raise SkillMagnetError("Skill instructions changed after menu installation; reinstall required")
    if menu_acceptance_digest is not None and menu_acceptance_digest != acceptance_digest:
        engine.record_rejection(
            pack_id=pack_id, runtime=runtime, reason="stale_menu_acceptance"
        )
        raise SkillMagnetError("Skill acceptance changed after menu installation; reinstall required")
    return {
        "selection_kind": "skill" if skill_id is not None else "pack",
        "selected_skill_id": skill_id,
        "project": str(project.resolve()),
        "pack_id": pack.pack_id,
        "skill_count": len(selected_skills),
        "skill_ids": selected_skills,
        "skill_ids_digest": skill_digest,
        "instruction_digest": instruction_digest,
        "acceptance_digest": acceptance_digest,
        "runtime": runtime,
        "repository_url": pack.repo_url,
        "expected_commit": pack.expected_commit,
        "approved_by": pack.approved_by,
        "approved_at": pack.approved_at,
        "purpose": pack.purpose,
        "verified_runtime": runtime in engine.SUPPORTED_RUNTIMES,
    }


def confirm_context_selection(
    engine: ActivationEngine,
    *,
    platform: str,
    details: dict[str, object],
    purpose: str,
    confirmed: bool,
) -> LaunchContract | None:
    """Create no state until the user has explicitly accepted the immutable selection."""
    if not confirmed:
        return None
    if not details["verified_runtime"]:
        engine.record_rejection(
            pack_id=str(details["pack_id"]),
            runtime=str(details["runtime"]),
            reason="unsupported_runtime",
        )
        raise SkillMagnetError(
            f"{str(details['runtime']).title()} has no verified runtime adapter; launch blocked"
        )
    try:
        plan = engine.plan(
            platform=platform,
            project=Path(str(details["project"])),
            pack_id=str(details["pack_id"]),
            runtime=str(details["runtime"]),
            purpose=purpose,
            skill_id=(
                str(details["selected_skill_id"])
                if details["selection_kind"] == "skill"
                else None
            ),
        )
    except SkillMagnetError:
        engine.record_rejection(
            pack_id=str(details["pack_id"]),
            runtime=str(details["runtime"]),
            reason="preflight_validation_failed",
        )
        raise
    if tuple(plan["skill_ids"]) != tuple(details["skill_ids"]):
        engine.record_rejection(
            pack_id=str(details["pack_id"]),
            runtime=str(details["runtime"]),
            reason="stale_menu_membership",
        )
        raise SkillMagnetError("Pack membership changed after menu selection; reinstall required")
    return engine.confirm(plan, confirmed=True)


def launch_context_leaf(
    engine: ActivationEngine,
    *,
    platform: str,
    project: Path,
    pack_id: str,
    skill_id: str,
    runtime: str,
    menu_commit: str,
    menu_skill_digest: str,
    menu_instruction_digest: str,
    menu_acceptance_digest: str,
    codex_executable: str | tuple[str, ...] = "codex",
    interactive_handoff: bool = False,
    destination: str = "verified_runtime",
    web_delivery: Callable[[str, str], None] | None = None,
    error_ui: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Execute one explicit leaf silently; the leaf selection is the consent event."""
    existing_rejections = set(engine.events_dir.glob("*-rejected.json"))
    try:
        if destination == "web" and runtime == "codex":
            engine.record_rejection(
                pack_id=pack_id,
                runtime=runtime,
                reason="web_codex_destination_unavailable",
            )
            raise SkillMagnetError(
                "Web Codex has no supported authenticated prompt input on this account. "
                "No ChatGPT or terminal fallback was used."
            )
        details = context_selection_details(
            engine,
            project=project,
            pack_id=pack_id,
            skill_id=skill_id,
            runtime=runtime,
            menu_commit=menu_commit,
            menu_skill_digest=menu_skill_digest,
            menu_instruction_digest=menu_instruction_digest,
            menu_acceptance_digest=menu_acceptance_digest,
        )
        contract = confirm_context_selection(
            engine,
            platform=platform,
            details=details,
            purpose=str(details["purpose"]),
            confirmed=True,
        )
    except SkillMagnetError as exc:
        if set(engine.events_dir.glob("*-rejected.json")) == existing_rejections:
            engine.record_rejection(
                pack_id=pack_id,
                runtime=runtime,
                reason="preflight_validation_failed",
            )
        if error_ui is not None:
            error_ui(context_error_message(exc))
        raise
    if contract is None:  # Defensive: confirmed=True must always return a contract.
        raise SkillMagnetError("Explicit leaf did not create a launch contract")
    try:
        if destination == "web":
            handoff = engine.prepare_web_handoff(contract.contract_id)
            delivery = web_delivery or deliver_web_claude_prompt
            delivery(str(handoff["prompt"]), str(handoff["destination"]))
            return {key: value for key, value in handoff.items() if key != "prompt"}
        return engine.execute(
            contract.contract_id,
            codex_executable=codex_executable,
            interactive_handoff=interactive_handoff,
        )
    except SkillMagnetError as exc:
        if error_ui is not None:
            error_ui(context_error_message(exc))
        raise


def show_context_selection(
    engine: ActivationEngine,
    *,
    platform: str,
    project: Path,
    pack_id: str | None = None,
    skill_id: str | None = None,
    runtime: str | None = None,
    menu_commit: str | None = None,
    menu_skill_digest: str | None = None,
    menu_instruction_digest: str | None = None,
    menu_acceptance_digest: str | None = None,
) -> LaunchContract | None:
    """Minimal shared UI used by both OS context-menu adapters."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("Skill Magnet")
    root.resizable(False, False)
    if platform == "windows" and any(
        value is None
        for value in (
            pack_id,
            skill_id,
            runtime,
            menu_commit,
            menu_skill_digest,
            menu_instruction_digest,
            menu_acceptance_digest,
        )
    ):
        raise SkillMagnetError(
            "Windows context launch requires an explicit pack, target AI, and installed menu version"
        )
    selected_pack = tk.StringVar(value=pack_id or "")
    selected_runtime = tk.StringVar(value=runtime or "")
    purpose = tk.StringVar()
    summary = tk.StringVar(value="Select one skill pack explicitly.")
    result: dict[str, LaunchContract] = {}

    ttk.Label(root, text=f"Project: {project.resolve()}").grid(
        row=0, column=0, columnspan=2, padx=12, pady=8, sticky="w"
    )
    ttk.Label(root, text="Selection").grid(row=1, column=0, padx=12, sticky="w")
    ttk.Label(root, text="Pack (all included skills)").grid(row=1, column=1, padx=12, sticky="w")
    ttk.Label(root, text="Skill pack").grid(row=2, column=0, padx=12, sticky="w")
    pack_box = ttk.Combobox(
        root,
        textvariable=selected_pack,
        values=tuple(engine.config.packs),
        state="disabled" if pack_id else "readonly",
    )
    pack_box.grid(row=2, column=1, padx=12, pady=4)

    def update_pack_summary(_: object = None) -> None:
        if not selected_pack.get():
            summary.set("Select one skill pack explicitly.")
            return
        pack = engine.config.packs[selected_pack.get()]
        summary.set(
            f"Included skills ({len(pack.skills)}): {', '.join(pack.skills)}\n"
            f"Repository: {pack.repo_url}\nVersion: {pack.expected_commit}\n"
            f"Approved: {pack.approved_by} at {pack.approved_at}"
        )

    pack_box.bind("<<ComboboxSelected>>", update_pack_summary)
    if pack_id:
        update_pack_summary()
    ttk.Label(root, text="Target AI").grid(row=3, column=0, padx=12, sticky="w")
    runtime_box = ttk.Combobox(
        root,
        textvariable=selected_runtime,
        values=("codex", "claude"),
        state="disabled" if runtime else "readonly",
    )
    runtime_box.grid(row=3, column=1, padx=12, pady=4)
    ttk.Label(root, text="Purpose").grid(row=4, column=0, padx=12, sticky="w")
    ttk.Entry(root, textvariable=purpose, width=48).grid(
        row=4, column=1, padx=12, pady=4
    )
    ttk.Label(root, textvariable=summary, wraplength=520).grid(
        row=5, column=0, columnspan=2, padx=12, pady=8, sticky="w"
    )
    unsupported = tk.StringVar(
        value=(
            "Claude has no verified runtime adapter. Launch is fail-closed."
            if runtime == "claude"
            else "Verification requires delivery, read, and skill-specific application evidence."
        )
    )
    ttk.Label(root, textvariable=unsupported, wraplength=520).grid(
        row=6, column=0, columnspan=2, padx=12, pady=4, sticky="w"
    )

    def confirm() -> None:
        try:
            details = context_selection_details(
                engine,
                project=project,
                pack_id=selected_pack.get(),
                skill_id=skill_id,
                runtime=selected_runtime.get(),
                menu_commit=menu_commit,
                menu_skill_digest=menu_skill_digest,
                menu_instruction_digest=menu_instruction_digest,
                menu_acceptance_digest=menu_acceptance_digest,
            )
        except Exception as exc:
            messagebox.showerror("Skill Magnet", str(exc), parent=root)
            return
        detail = (
            f"Selection: Pack (all {details['skill_count']} included skills)\n"
            f"Pack: {details['pack_id']}\nSkills: {', '.join(details['skill_ids'])}\n"
            f"Repository: {details['repository_url']}\n"
            f"Commit: {details['expected_commit']}\n"
            f"Target AI: {str(details['runtime']).title()}\n"
            f"Project: {details['project']}\nPurpose: {purpose.get()}\n\n"
            "No skill will be installed locally. Launch this verified task?"
        )
        if not messagebox.askyesno("Confirm Skill Magnet launch", detail, parent=root):
            return
        try:
            contract = confirm_context_selection(
                engine,
                platform=platform,
                details=details,
                purpose=purpose.get(),
                confirmed=True,
            )
        except Exception as exc:
            messagebox.showerror("Skill Magnet", str(exc), parent=root)
            return
        if contract is not None:
            result["contract"] = contract
        root.destroy()

    ttk.Button(root, text="Confirm and create launch", command=confirm).grid(
        row=7, column=0, padx=12, pady=12
    )
    ttk.Button(root, text="Cancel", command=root.destroy).grid(
        row=7, column=1, padx=12, pady=12
    )
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return result.get("contract")
