"""Transaction decisions shared by the library service and its UI.

This module has no filesystem, Git, process, or Tk side effects. The journal
remains durable metadata; a disposable checkout is never the source of state.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .core import SkillMagnetError


TERMINAL_STATES = frozenset({"active", "rolled_back", "abandoned", "no_changes"})
REMOTE_EFFECT_STATES = frozenset({
    "publishing", "published_pending", "verified", "activating", "menu_pending", "active",
})
TRANSACTION_STATES = TERMINAL_STATES | REMOTE_EFFECT_STATES | {
    "draft", "preparing", "interrupted", "prepared",
}


@dataclass(frozen=True)
class LibraryState:
    status: str
    resume_status: str
    has_remote_reference: bool
    draft_unavailable: bool
    wait_state: str

    @classmethod
    def from_journal(cls, journal: Mapping[str, Any]) -> LibraryState:
        status = str(journal.get("status", "draft"))
        resume = str(journal.get("resume_status", "draft"))
        if status not in TRANSACTION_STATES or (
            status in {"preparing", "interrupted"} and resume not in TRANSACTION_STATES
        ):
            raise SkillMagnetError("GitHub反映の作業状態を確認できません")
        return cls(
            status=status,
            resume_status=resume if status in {"preparing", "interrupted"} else status,
            has_remote_reference=bool(journal.get("commit") or journal.get("pr_url")),
            draft_unavailable=bool(journal.get("draft_unavailable")),
            wait_state=str(journal.get("wait_state", "")),
        )

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def remote_effect_possible(self) -> bool:
        return (
            self.has_remote_reference
            or self.status in REMOTE_EFFECT_STATES
            or self.resume_status in REMOTE_EFFECT_STATES
        )

    @property
    def can_abandon(self) -> bool:
        return not self.remote_effect_possible

    @property
    def can_start_edit(self) -> bool:
        return self.terminal or (
            self.status in {"draft", "prepared"} and self.can_abandon
        )

    @property
    def needs_reselection(self) -> bool:
        return self.draft_unavailable and not self.remote_effect_possible

    def action_stage(self, fallback: str = "prepare") -> str:
        if self.status == "active":
            return "complete"
        if self.status in {"prepared", "published_pending", "verified", "activating", "menu_pending"}:
            return "sync"
        return fallback

    def automatic_stage(self) -> str:
        if self.status == "published_pending":
            return "reopen_pr" if self.wait_state == "closed_unmerged" else "waiting"
        return "complete" if self.status == "active" else "sync"
