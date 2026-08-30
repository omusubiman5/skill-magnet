from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import stat
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .core import Config, Engine, Pack, SafetyError, SkillMagnetError, _is_link, hash_directory


CODEX_PROCESS_CONFIG_OVERRIDES = (
    "mcp_servers.cloudflare-builds.enabled=false",
    "mcp_servers.cloudflare-observability.enabled=false",
    "mcp_servers.unreal-mcp.enabled=false",
)


def codex_process_config_args() -> list[str]:
    """Return official per-process Codex config overrides for verification runs."""
    return [
        item
        for override in CODEX_PROCESS_CONFIG_OVERRIDES
        for item in ("-c", override)
    ]


def _windows_hidden_process_kwargs() -> dict[str, Any]:
    """Suppress a console for a product-owned runtime without hiding Tk windows."""
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


class _LaunchFailed(SafetyError):
    """The verified runtime process could not be started."""


class _RuntimeFailed(SafetyError):
    """The verified runtime started but exited before producing an envelope."""

    def __init__(self, *, exit_code: int, stderr: str, stdout: str = "") -> None:
        self.diagnostic = _runtime_failure_diagnostic(exit_code, stderr, stdout)
        super().__init__(
            "Runtime exited before producing verified output; "
            "skill use is not guaranteed"
        )


class _OutputFailed(SafetyError):
    """The runtime did not produce a valid evidence envelope."""


class _AcceptanceFailed(SafetyError):
    """The runtime output did not satisfy skill-specific acceptance."""


class _CleanupFailed(SafetyError):
    """One or more temporary runtime artifacts remain."""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths
        super().__init__("Temporary activation artifact cleanup failed")


def _runtime_failure_diagnostic(
    exit_code: int, stderr: str, stdout: str = ""
) -> dict[str, Any]:
    """Return an allow-listed runtime diagnostic without persisting raw stderr."""
    normalized = f"{stderr}\n{stdout}".lower()
    if "invalid_json_schema" in normalized or "invalid schema for response_format" in normalized:
        failure_class = "invalid_output_schema"
        summary = "Codex rejected the verification output schema."
    elif "invalid transport" in normalized or "mcp_servers." in normalized:
        failure_class = "config_parse"
        summary = "Codex configuration could not be parsed."
    elif (
        "unexpected argument" in normalized
        or "unrecognized option" in normalized
        or "usage:" in normalized
    ):
        failure_class = "cli_usage"
        summary = "Codex rejected the runtime command line."
    elif any(
        marker in normalized
        for marker in ("unauthorized", "authentication", "not logged in", "401")
    ):
        failure_class = "authentication"
        summary = "Codex authentication failed."
    elif any(
        marker in normalized
        for marker in ("rate limit", "rate_limit", "too many requests", "429")
    ):
        failure_class = "rate_limit"
        summary = "Codex rate limit prevented the run."
    elif any(
        marker in normalized
        for marker in (
            "connection refused",
            "connection reset",
            "dns",
            "network",
            "timed out",
        )
    ):
        failure_class = "network"
        summary = "Codex could not complete its network request."
    else:
        failure_class = "runtime_error"
        summary = "Codex exited before returning verified output."
    return {
        "exit_code": exit_code,
        "failure_class": failure_class,
        "stderr_present": bool(stderr),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stderr_summary": summary,
        "stdout_present": bool(stdout),
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class LaunchContract:
    attempt_id: str
    contract_id: str
    platform: str
    project: str
    pack_id: str
    runtime: str
    repository_url: str
    commit_sha: str
    approved_by: str
    approved_at: str
    purpose: str
    selection_kind: str
    selected_skill_id: str | None
    skill_ids: tuple[str, ...]
    skill_hashes: dict[str, str]
    index_digest: str | None
    instruction_digest: str
    acceptance_digests: dict[str, str]
    confirmed_at: str
    expires_at: str
    nonce: str
    contract_digest: str

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["skill_ids"] = list(self.skill_ids)
        return value


class ActivationEngine:
    """Fail-closed, one-shot activation path. It never installs local skills."""

    SUPPORTED_PLATFORMS = {"windows", "macos"}
    SUPPORTED_RUNTIMES = {"codex", "claude"}

    def __init__(
        self,
        config: Config,
        state_dir: Path | None = None,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.config = config
        self.engine = Engine(config, state_dir)
        self.state_dir = self.engine.state_dir
        self.contract_dir = self.state_dir / "launch-contracts"
        self.evidence_dir = self.state_dir / "evidence"
        self.events_dir = self.state_dir / "events"
        self.process_dir = self.state_dir / "process-markers"
        self.materialization_dir = self.state_dir / "desktop-materializations"
        self.desktop_receipt_dir = self.state_dir / "desktop-completion-receipts"
        self.now = now

    @staticmethod
    def _make_tree_writable(root: Path) -> None:
        if not root.exists():
            return
        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD | stat.S_IWRITE)

    def _cleanup_expired_materializations(self) -> list[str]:
        removed: list[str] = []
        if not self.materialization_dir.is_dir():
            return removed
        for root in sorted(self.materialization_dir.iterdir()):
            if (
                root.is_dir()
                and not _is_link(root)
                and re.fullmatch(r"[0-9a-f]{32}-[a-z0-9_]+", root.name)
            ):
                self._make_tree_writable(root)
                shutil.rmtree(root)
                removed.append(root.name)
                continue
            if not root.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", root.name):
                raise SafetyError("Unexpected Desktop materialization entry")
            manifest_path = root / "materialization.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(str(manifest["expires_at"]))
            except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
                raise SafetyError("Invalid Desktop materialization metadata") from exc
            if self.now() >= expires_at:
                self._make_tree_writable(root)
                shutil.rmtree(root)
                removed.append(root.name)
        return removed

    def _cleanup_expired_desktop_receipts(self) -> list[str]:
        removed: list[str] = []
        if not self.desktop_receipt_dir.is_dir():
            return removed
        for receipt_path in sorted(self.desktop_receipt_dir.glob("*.json")):
            contract_id = receipt_path.stem
            if not re.fullmatch(r"[0-9a-f]{32}", contract_id):
                raise SafetyError("Unexpected Desktop completion receipt")
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(str(receipt["expires_at"]))
            except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
                raise SafetyError("Invalid Desktop completion receipt metadata") from exc
            if self.now() < expires_at:
                continue
            expected_schema = self.evidence_dir / f"{contract_id}-desktop-schema.json"
            expected_output = self.evidence_dir / f"{contract_id}-desktop-output.json"
            self._cleanup_temporary_artifacts(
                (expected_schema, expected_output, receipt_path)
            )
            removed.append(contract_id)
        return removed

    def _materialize_desktop_pack(
        self,
        contract: LaunchContract,
        pack: Pack,
        expected_hashes: dict[str, str],
        expected_index_digest: str | None,
    ) -> tuple[Pack, dict[str, Any]]:
        self._cleanup_expired_materializations()
        self.materialization_dir.mkdir(parents=True, exist_ok=True)
        final = self.materialization_dir / contract.contract_id
        if final.exists():
            raise SafetyError("Desktop materialization already exists")
        temporary = Path(
            tempfile.mkdtemp(
                prefix=f"{contract.contract_id}-", dir=self.materialization_dir
            )
        )
        try:
            index_source = pack.source / "INDEX.md"
            index_digest = None
            if index_source.is_file():
                shutil.copy2(index_source, temporary / "INDEX.md")
                index_digest = hashlib.sha256(
                    (temporary / "INDEX.md").read_bytes()
                ).hexdigest()
            if index_digest != expected_index_digest:
                raise SafetyError("Pack INDEX changed during Desktop materialization")
            copied_hashes: dict[str, str] = {}
            for skill_id in contract.skill_ids:
                shutil.copytree(pack.source / skill_id, temporary / skill_id)
                copied_hash = hash_directory(temporary / skill_id)
                if copied_hash != expected_hashes[skill_id]:
                    raise SafetyError(
                        f"Pack changed during Desktop materialization: {skill_id}"
                    )
                copied_hashes[skill_id] = copied_hash
            manifest = {
                "version": 1,
                "contract_id": contract.contract_id,
                "commit_sha": contract.commit_sha,
                "expires_at": contract.expires_at,
                "index_sha256": index_digest,
                "skill_hashes": copied_hashes,
            }
            (temporary / "materialization.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for path in temporary.rglob("*"):
                if path.is_file():
                    path.chmod(stat.S_IREAD)
            os.replace(temporary, final)
        except Exception:
            self._make_tree_writable(temporary)
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return replace(pack, source=final), {**manifest, "path": str(final)}

    def _write_terminal_lifecycle(
        self,
        *,
        attempt_id: str,
        contract_id: str,
        status: str,
        terminal_event_id: str | None = None,
        terminal: bool = True,
    ) -> dict[str, Any]:
        event = {
            "attempt_id": attempt_id,
            "contract_id": contract_id,
            "terminal_event_id": terminal_event_id or uuid.uuid4().hex,
            "status": status,
            "terminal": terminal,
        }
        self.events_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f"{contract_id}-lifecycle-",
            suffix=".jsonl",
            dir=self.events_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            os.replace(
                temporary,
                self.events_dir / f"{contract_id}-lifecycle.jsonl",
            )
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return event

    def recover_interrupted_attempts(self) -> list[str]:
        """Finalize abandoned runtime attempts exactly once on a new public entry."""
        self._cleanup_expired_materializations()
        self._cleanup_expired_desktop_receipts()
        recovered: list[str] = []
        if not self.process_dir.is_dir():
            return recovered
        for marker_path in sorted(self.process_dir.glob("*-process.json")):
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                contract_id = str(marker["contract_id"])
                attempt_id = str(marker["attempt_id"])
                pack_id = str(marker["pack_id"])
                commit_sha = str(marker["commit_sha"])
                prompt_sha256 = str(marker["prompt_sha256"])
                schema_sha256 = str(marker["schema_sha256"])
                temporary_names = tuple(marker["temporary_names"])
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise SafetyError(f"Invalid interrupted-attempt marker: {marker_path.name}") from exc
            if marker_path.name != f"{contract_id}-process.json":
                raise SafetyError("Interrupted-attempt marker identity mismatch")
            temporary_paths: list[Path] = []
            for name in temporary_names:
                if not isinstance(name, str) or Path(name).name != name:
                    raise SafetyError("Interrupted-attempt marker contains an unsafe path")
                if not name.startswith(f"{contract_id}-"):
                    raise SafetyError("Interrupted-attempt artifact identity mismatch")
                temporary_paths.append(self.evidence_dir / name)
            failure_path = self.evidence_dir / f"{contract_id}-not-guaranteed.json"
            self._cleanup_temporary_artifacts(tuple(temporary_paths))
            if not failure_path.is_file():
                terminal_event_id = hashlib.sha256(
                    f"{attempt_id}:{contract_id}:interrupted".encode("utf-8")
                ).hexdigest()[:32]
                terminal_event = self._write_terminal_lifecycle(
                    attempt_id=attempt_id,
                    contract_id=contract_id,
                    status="interrupted",
                    terminal_event_id=terminal_event_id,
                )
                failure = {
                    "status": "interrupted",
                    "attempt_id": attempt_id,
                    "terminal_event_id": terminal_event_id,
                    "terminal_event": {"status": "interrupted", "terminal": True},
                    "contract_id": contract_id,
                    "pack_id": pack_id,
                    "commit_sha": commit_sha,
                    "reason": "interrupted",
                    "task_delivery_evidence": {"prompt_sha256": prompt_sha256},
                    "output_schema_evidence": {
                        "version": 1,
                        "sha256": schema_sha256,
                    },
                }
                self.engine._write_json_atomic(failure_path, failure)
                recovered.append(contract_id)
            marker_path.unlink()
        return recovered

    def record_rejection(
        self, *, pack_id: str, runtime: str, reason: str
    ) -> dict[str, Any]:
        """Persist a sanitized pre-contract refusal without success evidence."""
        attempt_id = uuid.uuid4().hex
        event = {
            "attempt_id": attempt_id,
            "status": "rejected",
            "terminal": True,
            "pack_id": pack_id,
            "runtime": runtime,
            "reason": reason,
        }
        self.engine._write_json_atomic(
            self.events_dir / f"{attempt_id}-rejected.json", event
        )
        return event

    @staticmethod
    def _cleanup_temporary_artifacts(paths: tuple[Path, ...]) -> None:
        """Remove runtime material before any success terminal is persisted."""
        failures: list[Path] = []
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                failures.append(path)
        if failures:
            raise _CleanupFailed(tuple(failures))

    def _validated_pack(
        self, pack_id: str
    ) -> tuple[Pack, str, dict[str, str], str | None]:
        pack = self.engine._pack(pack_id)
        commit, hashes = self.engine._validate_pack(pack)
        if not pack.purpose:
            raise SafetyError(f"Pack {pack_id} has no declared purpose")
        if not pack.approved_by or not pack.approved_at:
            raise SafetyError(f"Pack {pack_id} has no recorded approval")
        try:
            approved_at = datetime.fromisoformat(pack.approved_at)
        except ValueError as exc:
            raise SafetyError(f"Pack {pack_id} has an invalid approval timestamp") from exc
        if approved_at.tzinfo is None:
            raise SafetyError(f"Pack {pack_id} approval timestamp requires a timezone")
        if approved_at > self.now():
            raise SafetyError(f"Pack {pack_id} approval timestamp is in the future")
        index = pack.source / "INDEX.md"
        index_digest = (
            hashlib.sha256(index.read_bytes()).hexdigest()
            if index.is_file()
            else None
        )
        return pack, commit, hashes, index_digest

    @staticmethod
    def _acceptance_path(pack: Pack, skill: str) -> Path:
        return pack.source / skill / "acceptance.json"

    @staticmethod
    def approved_blob_digest(pack: Pack, skill: str, filename: str) -> str:
        if (pack.source / ".skill-magnet-snapshot.json").is_file():
            path = pack.source / skill / filename
            if not path.is_file():
                raise SafetyError(
                    f"Cannot read approved skill artifact: {skill}/{filename}"
                )
            return hashlib.sha256(path.read_bytes()).hexdigest()
        result = subprocess.run(
            [
                "git",
                "-C",
                str(pack.source),
                "show",
                f"{pack.expected_commit}:{skill}/{filename}",
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            raise SafetyError(
                f"Cannot read approved skill artifact: {skill}/{filename}"
            )
        return hashlib.sha256(result.stdout).hexdigest()

    def _load_acceptance(
        self, pack: Pack, skill_ids: tuple[str, ...] | None = None
    ) -> dict[str, dict[str, Any]]:
        checks: dict[str, dict[str, Any]] = {}
        for skill in skill_ids or pack.skills:
            path = self._acceptance_path(pack, skill)
            if not path.is_file():
                raise SafetyError(f"Skill-specific acceptance check is required: {path}")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SafetyError(f"Invalid acceptance check: {path}: {exc}") from exc
            if not isinstance(value, dict) or value.get("version") != 1:
                raise SafetyError(f"Unsupported acceptance check: {path}")
            assertions = value.get("assertions")
            if not isinstance(assertions, list) or not assertions:
                raise SafetyError(f"Acceptance assertions are required: {path}")
            for assertion in assertions:
                if (
                    not isinstance(assertion, dict)
                    or not isinstance(assertion.get("path"), str)
                    or "equals" not in assertion
                ):
                    raise SafetyError(f"Invalid acceptance assertion: {path}")
                field_path = assertion["path"].split(".")
                if (
                    len(field_path) != 2
                    or field_path[0] != "result"
                    or not field_path[1].replace("-", "_").isidentifier()
                ):
                    raise SafetyError(
                        f"MVP acceptance path must be result.<field>: {path}"
                    )
                if isinstance(assertion["equals"], (dict, list)):
                    raise SafetyError(
                        f"MVP acceptance equals must be a JSON primitive: {path}"
                    )
            checks[skill] = value
        return checks

    def plan(
        self,
        *,
        platform: str,
        project: Path,
        pack_id: str,
        runtime: str,
        purpose: str,
        skill_id: str | None = None,
        ttl_minutes: int = 30,
    ) -> dict[str, Any]:
        self.recover_interrupted_attempts()
        if platform not in self.SUPPORTED_PLATFORMS:
            raise SkillMagnetError(f"Unsupported platform: {platform}")
        if runtime not in self.SUPPORTED_RUNTIMES:
            raise SkillMagnetError(f"Unsupported verified runtime: {runtime}")
        if not purpose.strip():
            raise SkillMagnetError("Purpose is required")
        if not 1 <= ttl_minutes <= 120:
            raise SkillMagnetError("ttl_minutes must be between 1 and 120")
        project = project.resolve()
        if not project.is_dir():
            raise SkillMagnetError(f"Project directory does not exist: {project}")
        pack, commit, hashes, index_digest = self._validated_pack(pack_id)
        if skill_id is not None and skill_id not in pack.skills:
            raise SkillMagnetError(f"Unknown skill for pack {pack_id}: {skill_id}")
        selected_skills = (skill_id,) if skill_id is not None else pack.skills
        checks = self._load_acceptance(pack, selected_skills)
        instructions = self._instructions(pack, selected_skills)
        instruction_digest = (
            self.approved_blob_digest(pack, skill_id, "SKILL.md")
            if skill_id is not None
            else hashlib.sha256(instructions.encode("utf-8")).hexdigest()
        )
        return {
            "operation": "verified-runtime-launch",
            "writes": False,
            "platform": platform,
            "project": str(project),
            "pack_id": pack_id,
            "runtime": runtime,
            "repository_url": pack.repo_url,
            "commit_sha": commit,
            "approved_by": pack.approved_by,
            "approved_at": pack.approved_at,
            "pack_purpose": pack.purpose,
            "purpose": purpose.strip(),
            "selection_kind": "skill" if skill_id is not None else "pack",
            "selected_skill_id": skill_id,
            "skill_ids": list(selected_skills),
            "skill_hashes": {skill: hashes[skill] for skill in selected_skills},
            "index_digest": index_digest,
            "instruction_digest": instruction_digest,
            "acceptance_digests": {
                skill: self.approved_blob_digest(pack, skill, "acceptance.json")
                for skill in checks
            },
            "ttl_minutes": ttl_minutes,
            "requires_explicit_confirmation": True,
            "local_skill_placement": False,
        }

    @staticmethod
    def _instructions(
        pack: Pack, skill_ids: tuple[str, ...] | None = None
    ) -> str:
        parts: list[str] = []
        for skill in skill_ids or pack.skills:
            content = (pack.source / skill / "SKILL.md").read_text(encoding="utf-8-sig")
            parts.append(f"<skill id={json.dumps(skill)}>\n{content}\n</skill>")
        return "\n\n".join(parts)

    @staticmethod
    def _pack_index(pack: Pack, selection_kind: str) -> str:
        """Return the reviewed composition map for complete-package execution."""
        if selection_kind != "pack":
            return ""
        path = pack.source / "INDEX.md"
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8-sig")

    @staticmethod
    def _pack_relations(pack: Pack) -> dict[str, set[tuple[str, str]]]:
        """Parse enforceable INDEX Mermaid relationship edges."""
        path = pack.source / "INDEX.md"
        if not path.is_file():
            return {
                "depends-on": set(),
                "composes-with": set(),
                "contrasts-with": set(),
            }
        content = path.read_text(encoding="utf-8-sig")
        aliases: dict[str, str] = {}

        def resolve(short_id: str) -> str | None:
            if short_id in pack.skills:
                return short_id
            matches = [skill for skill in pack.skills if skill.endswith(short_id)]
            return matches[0] if len(matches) == 1 else None

        for alias, short_id in re.findall(r'(\w+)\["([^"]+)"\]', content):
            resolved = resolve(short_id)
            if resolved is not None:
                aliases[alias] = resolved
        relations = {
            "depends-on": set(),
            "composes-with": set(),
            "contrasts-with": set(),
        }
        edge_pattern = re.compile(
            r"^\s*(\w+)(?:\[\"[^\"]+\"\])?\s+[-.=]+>\|([^|]+)\|\s+"
            r"(\w+)(?:\[\"[^\"]+\"\])?",
            re.MULTILINE,
        )
        for source_alias, relation, target_alias in edge_pattern.findall(content):
            relation = relation.strip()
            source = aliases.get(source_alias)
            target = aliases.get(target_alias)
            if relation in relations and source is not None and target is not None:
                relations[relation].add((source, target))
        return relations

    @classmethod
    def _verify_pack_relations(
        cls,
        pack: Pack,
        completed_skill_ids: list[str],
        applied_rules: list[str],
    ) -> None:
        applied = set(completed_skill_ids)
        relations = cls._pack_relations(pack)
        for source, dependency in relations["depends-on"]:
            if source in applied and dependency not in applied:
                raise _AcceptanceFailed(
                    f"Applied skill dependency is missing: {source} depends-on {dependency}"
                )
        for left, right in relations["contrasts-with"]:
            if left in applied and right in applied:
                raise _AcceptanceFailed(
                    f"Contrasting skills cannot both be applied: {left}, {right}"
                )
        for left, right in relations["composes-with"]:
            if left not in applied or right not in applied:
                continue
            if not any(
                "composes-with" in rule and left in rule and right in rule
                for rule in applied_rules
            ):
                raise _AcceptanceFailed(
                    "Applied composition has no relationship evidence: "
                    f"{left} composes-with {right}"
                )

    def confirm(self, plan: dict[str, Any], *, confirmed: bool) -> LaunchContract:
        if not confirmed:
            raise SafetyError("Launch requires explicit user confirmation")
        required = {
            "platform",
            "project",
            "pack_id",
            "runtime",
            "repository_url",
            "commit_sha",
            "approved_by",
            "approved_at",
            "purpose",
            "selection_kind",
            "selected_skill_id",
            "skill_ids",
            "skill_hashes",
            "index_digest",
            "instruction_digest",
            "acceptance_digests",
            "ttl_minutes",
        }
        if plan.get("operation") != "verified-runtime-launch" or not required <= plan.keys():
            raise SafetyError("Invalid activation plan")
        refreshed = self.plan(
            platform=str(plan["platform"]),
            project=Path(str(plan["project"])),
            pack_id=str(plan["pack_id"]),
            runtime=str(plan["runtime"]),
            purpose=str(plan["purpose"]),
            skill_id=(
                str(plan["selected_skill_id"])
                if plan["selection_kind"] == "skill"
                else None
            ),
            ttl_minutes=int(plan["ttl_minutes"]),
        )
        for key in required - {"ttl_minutes"}:
            if refreshed[key] != plan[key]:
                raise SafetyError(f"Activation plan changed before confirmation: {key}")
        confirmed_at = self.now()
        payload: dict[str, Any] = {
            "attempt_id": uuid.uuid4().hex,
            "contract_id": uuid.uuid4().hex,
            "platform": plan["platform"],
            "project": plan["project"],
            "pack_id": plan["pack_id"],
            "runtime": plan["runtime"],
            "repository_url": plan["repository_url"],
            "commit_sha": plan["commit_sha"],
            "approved_by": plan["approved_by"],
            "approved_at": plan["approved_at"],
            "purpose": plan["purpose"],
            "selection_kind": plan["selection_kind"],
            "selected_skill_id": plan["selected_skill_id"],
            "skill_ids": tuple(plan["skill_ids"]),
            "skill_hashes": dict(plan["skill_hashes"]),
            "index_digest": plan["index_digest"],
            "instruction_digest": plan["instruction_digest"],
            "acceptance_digests": dict(plan["acceptance_digests"]),
            "confirmed_at": confirmed_at.isoformat(),
            "expires_at": (
                confirmed_at + timedelta(minutes=int(plan["ttl_minutes"]))
            ).isoformat(),
            "nonce": uuid.uuid4().hex,
        }
        digest_payload = {**payload, "skill_ids": list(payload["skill_ids"])}
        contract = LaunchContract(**payload, contract_digest=_digest(digest_payload))
        self.engine._write_json_atomic(
            self.contract_dir / f"{contract.contract_id}.json", contract.as_dict()
        )
        return contract

    def _read_contract_record(
        self, contract_id: str, *, allow_consumed: bool = False
    ) -> LaunchContract:
        path = self.contract_dir / f"{contract_id}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyError(f"Launch contract is missing or invalid: {contract_id}") from exc
        if value.get("consumed_at") and not allow_consumed:
            raise SafetyError("Launch contract was already used")
        contract = LaunchContract(
            **{key: value[key] for key in LaunchContract.__dataclass_fields__}
        )
        unsigned = contract.as_dict()
        digest = unsigned.pop("contract_digest")
        if _digest(unsigned) != digest:
            raise SafetyError("Launch contract integrity check failed")
        if self.now() >= datetime.fromisoformat(contract.expires_at):
            raise SafetyError("Launch contract expired")
        return contract

    def _read_contract(self, contract_id: str) -> LaunchContract:
        return self._read_contract_record(contract_id)

    def _consume(self, contract: LaunchContract) -> None:
        path = self.contract_dir / f"{contract.contract_id}.json"
        value = contract.as_dict()
        value["consumed_at"] = self.now().isoformat()
        self.engine._write_json_atomic(path, value)

    def _task_envelope(self, contract: LaunchContract, pack: Pack) -> str:
        provenance = {
            "pack_id": contract.pack_id,
            "repository_url": contract.repository_url,
            "commit_sha": contract.commit_sha,
            "approved_by": contract.approved_by,
            "approved_at": contract.approved_at,
            "skill_ids": list(contract.skill_ids),
            "instruction_digest": contract.instruction_digest,
            "challenge_nonce": contract.nonce,
            "actual_request_sha256": hashlib.sha256(
                contract.purpose.encode("utf-8")
            ).hexdigest(),
        }
        actual_request_sha256 = hashlib.sha256(
            contract.purpose.encode("utf-8")
        ).hexdigest()
        pack_index = self._pack_index(pack, contract.selection_kind)
        index_section = (
            f"\n\n<pack-index>\n{pack_index}\n</pack-index>" if pack_index else ""
        )
        return (
            "Skill Magnet verified task envelope.\n"
            f"PROVENANCE={json.dumps(provenance, ensure_ascii=False, sort_keys=True)}\n"
            f"PURPOSE={contract.purpose}\n"
            f"TARGET_PROJECT={contract.project}\n"
            "The PURPOSE field is the user's actual request. Complete that request, "
            "not a demonstration or readiness exercise. Read every supplied skill "
            "instruction and the pack INDEX, then select the smallest applicable skill "
            "set from each skill's triggers and boundaries. Close depends-on relations; "
            "add composes-with skills only when the request needs them; do not combine "
            "contrasts-with skills. Put the concrete user-facing "
            "deliverable in result.task_output. "
            "If files were saved or project content changed, report user-facing relative "
            "paths in result.saved_paths and short descriptions in result.changes. "
            "Always return both arrays; use [] when there are no saved paths or changes. "
            "Only after the actual request is complete, set "
            "evidence.skill_execution_status to completed, copy only the skill IDs "
            "actually applied to evidence.completed_skill_ids, and set "
            f"evidence.actual_request_sha256 to {actual_request_sha256}. "
            "In evidence.applied_rules, include at least one concrete applied rule for "
            "each applied skill and begin that rule with the exact skill ID followed "
            "by a colon. If both endpoints of a composes-with INDEX edge are applied, "
            "include one rule containing both exact skill IDs, the text composes-with, "
            "and the request-specific reason for combining them. "
            "Return only the JSON evidence envelope requested by the output schema.\n\n"
            f"{self._instructions(pack, contract.skill_ids)}"
            f"{index_section}"
        )

    def _desktop_task_prompt(
        self,
        contract: LaunchContract,
        pack: Pack,
        *,
        completion_schema_path: Path | None = None,
        completion_output_path: Path | None = None,
    ) -> str:
        """Build the human-readable Codex Desktop task bound to one contract."""
        actual_request_sha256 = hashlib.sha256(
            contract.purpose.encode("utf-8")
        ).hexdigest()
        acceptance_lines: list[str] = []
        for skill_id, check in self._load_acceptance(pack, contract.skill_ids).items():
            for assertion in check["assertions"]:
                if contract.selection_kind == "pack":
                    acceptance_lines.append(
                        f"- {skill_id}: {assertion['path']}へ依頼に適合する値を記述する"
                        f"（固定値ではない。形式例: "
                        f"{json.dumps(assertion['equals'], ensure_ascii=False)}）"
                    )
                else:
                    acceptance_lines.append(
                        f"- {skill_id}: {assertion['path']} = "
                        f"{json.dumps(assertion['equals'], ensure_ascii=False)}"
                    )
        instruction_refs = [
            "- "
            + skill_id
            + ": "
            + str((pack.source / skill_id / "SKILL.md").resolve())
            + " (SHA-256: "
            + hashlib.sha256(
                (pack.source / skill_id / "SKILL.md").read_bytes()
            ).hexdigest()
            + ")"
            for skill_id in contract.skill_ids
        ]
        index_path = (pack.source / "INDEX.md").resolve()
        index_section = ""
        if contract.selection_kind == "pack" and index_path.is_file():
            index_digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
            index_section = (
                "\nパックINDEX（最初に全文を読む）:\n"
                f"{index_path} (SHA-256: {index_digest})\n"
            )
        skill_label = (
            f"適用スキルID: {', '.join(contract.skill_ids)}\n"
            if contract.selection_kind == "skill"
            else f"パック収録スキルID: {', '.join(contract.skill_ids)}\n"
        )
        completion_section = ""
        if completion_schema_path is not None and completion_output_path is not None:
            completion_command = subprocess.list2cmdline(
                [
                    sys.executable,
                    "-m",
                    "skill_magnet",
                    "--config",
                    str(self.config.path),
                    "--state-dir",
                    str(self.state_dir),
                    "activation-complete",
                    "--contract",
                    contract.contract_id,
                ]
            )
            completion_section = (
                "\n\n完了証拠の提出（自然文回答とは別に必須）:\n"
                f"1. JSON Schemaを読む: {completion_schema_path}\n"
                f"2. 実際の依頼を完了した後だけ、schemaに適合する証拠JSONを次へ保存する: "
                f"{completion_output_path}\n"
                "3. 次の一回限りの検証commandを実行する:\n"
                f"{completion_command}\n"
                "検証JSONは利用者への最終回答へ表示せず、最終回答には依頼への自然文成果だけを返す。"
            )
        return (
            "Skill Magnetからの実行依頼です。\n"
            "これはデモ、準備確認、実行可否の説明ではありません。選択したパックの"
            "全スキルを読み、INDEXの関係と各スキルのtrigger/boundaryから必要最小限の"
            "集合を選んで、実際の依頼をこの新規タスクで完了してください。depends-onは"
            "依存先を含め、composes-withは必要な場合だけ加え、contrasts-withは同時採用"
            "しないでください。各スキルを個別の回答生成依頼として扱わないでください。\n\n"
            f"対象プロジェクト: {contract.project}\n"
            f"選択パックID: {contract.pack_id}\n"
            f"{skill_label}"
            f"Skill Magnet contract ID: {contract.contract_id}\n"
            f"Skill Magnet attempt ID: {contract.attempt_id}\n"
            f"依頼SHA-256: {actual_request_sha256}\n"
            f"指示SHA-256: {contract.instruction_digest}\n"
            "\n実際の依頼:\n"
            f"{contract.purpose}\n"
            "\n期待する成果:\n"
            "上記依頼へ直接答える具体的な自然文の最終回答を返してください。"
            "内部の契約JSONや検証用JSONを回答として表示しないでください。\n"
            "\n受入条件候補（実際に適用したスキルの条件だけが必須）:\n"
            f"{'\n'.join(acceptance_lines)}\n"
            f"{index_section}"
            "\n選択スキルの検証済み指示ファイル:\n"
            f"{'\n'.join(instruction_refs)}\n"
            "上記INDEXと全SKILL.mdをツールで省略せず読み終えてから、INDEXの関係と"
            "各スキルのtrigger/boundaryに従って必要なものだけを実際の依頼へ適用して"
            "ください。適用しなかったスキルの受入固定値を成果へ追加しないでください。"
            f"{completion_section}"
        )

    def prepare_codex_desktop_handoff(self, contract_id: str) -> dict[str, Any]:
        """Consume one Codex contract and prepare a new Desktop app task.

        Shell acceptance is not task completion.  The returned state therefore
        never claims that the Desktop task or its answer was machine-verified.
        """
        self.recover_interrupted_attempts()
        contract = self._read_contract(contract_id)
        if contract.runtime != "codex":
            raise _LaunchFailed("Codex Desktop handoff requires the Codex runtime")
        pack, commit, hashes, index_digest = self._validated_pack(contract.pack_id)
        if commit != contract.commit_sha or pack.repo_url != contract.repository_url:
            raise SafetyError("Pack provenance changed after confirmation")
        if (
            {skill: hashes[skill] for skill in contract.skill_ids}
            != contract.skill_hashes
            or index_digest != contract.index_digest
        ):
            raise SafetyError("Pack content changed after confirmation")
        materialized_pack, materialization = self._materialize_desktop_pack(
            contract, pack, contract.skill_hashes, contract.index_digest
        )
        checks = self._load_acceptance(materialized_pack, contract.skill_ids)
        schema = self._output_schema(contract, checks)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.desktop_receipt_dir.mkdir(parents=True, exist_ok=True)
        schema_path = self.evidence_dir / f"{contract.contract_id}-desktop-schema.json"
        output_path = self.evidence_dir / f"{contract.contract_id}-desktop-output.json"
        receipt_path = self.desktop_receipt_dir / f"{contract.contract_id}.json"
        self.engine._write_json_atomic(schema_path, schema)
        prompt = self._desktop_task_prompt(
            contract,
            materialized_pack,
            completion_schema_path=schema_path,
            completion_output_path=output_path,
        )
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self.engine._write_json_atomic(
            receipt_path,
            {
                "version": 1,
                "attempt_id": contract.attempt_id,
                "contract_id": contract.contract_id,
                "prompt_sha256": prompt_digest,
                "schema_path": str(schema_path),
                "schema_sha256": _digest(schema),
                "output_path": str(output_path),
                "expires_at": contract.expires_at,
            },
        )
        self._consume(contract)
        return {
            "status": "desktop_handoff_prepared",
            "runtime": "codex",
            "destination": "codex://threads/new",
            "contract_id": contract.contract_id,
            "attempt_id": contract.attempt_id,
            "pack_id": contract.pack_id,
            "commit_sha": contract.commit_sha,
            "project": contract.project,
            "skill_ids": list(contract.skill_ids),
            "actual_request_sha256": hashlib.sha256(
                contract.purpose.encode("utf-8")
            ).hexdigest(),
            "instruction_digest": contract.instruction_digest,
            "skill_hashes": dict(contract.skill_hashes),
            "index_digest": contract.index_digest,
            "acceptance_digests": dict(contract.acceptance_digests),
            "prompt": prompt,
            "prompt_sha256": prompt_digest,
            "completion_receipt": {
                "status": "pending",
                "schema_sha256": _digest(schema),
                "expires_at": contract.expires_at,
            },
            "materialization": materialization,
        }

    def record_codex_desktop_handoff(
        self, prepared: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist shell-accepted handoff evidence without inventing completion."""
        if prepared.get("status") != "desktop_handoff_prepared":
            raise SafetyError("Invalid Codex Desktop handoff state")
        contract_id = str(prepared["contract_id"])
        attempt_id = str(prepared["attempt_id"])
        terminal_event = self._write_terminal_lifecycle(
            attempt_id=attempt_id,
            contract_id=contract_id,
            status="desktop_handoff_ready",
            terminal=False,
        )
        evidence = {
            key: value
            for key, value in prepared.items()
            if key not in {"prompt", "status"}
        }
        evidence.update(
            {
                "status": "desktop_handoff_ready",
                "terminal_event_id": terminal_event["terminal_event_id"],
                "terminal_event": {
                    "status": "desktop_handoff_ready",
                    "terminal": False,
                },
                "desktop_result_verification": "not_available",
                "verified_completed": False,
            }
        )
        self.engine._write_json_atomic(
            self.evidence_dir / f"{contract_id}-desktop-handoff.json", evidence
        )
        return evidence

    def record_codex_desktop_launch_failure(
        self, prepared: dict[str, Any]
    ) -> dict[str, Any]:
        """Close a prepared receipt when the OS rejects the Desktop deep link."""
        if prepared.get("status") != "desktop_handoff_prepared":
            raise SafetyError("Invalid Codex Desktop handoff state")
        contract_id = str(prepared["contract_id"])
        attempt_id = str(prepared["attempt_id"])
        schema_path = self.evidence_dir / f"{contract_id}-desktop-schema.json"
        output_path = self.evidence_dir / f"{contract_id}-desktop-output.json"
        receipt_path = self.desktop_receipt_dir / f"{contract_id}.json"
        self._cleanup_temporary_artifacts((schema_path, output_path, receipt_path))
        materialization = self.materialization_dir / contract_id
        if materialization.is_dir() and not _is_link(materialization):
            self._make_tree_writable(materialization)
            shutil.rmtree(materialization)
        terminal_event = self._write_terminal_lifecycle(
            attempt_id=attempt_id,
            contract_id=contract_id,
            status="launch_failed",
        )
        failure = {
            "status": "launch_failed",
            "attempt_id": attempt_id,
            "contract_id": contract_id,
            "pack_id": prepared["pack_id"] if "pack_id" in prepared else None,
            "commit_sha": prepared.get("commit_sha"),
            "reason": "launch_failed",
            "task_delivery_evidence": {
                "prompt_sha256": prepared["prompt_sha256"]
            },
            "terminal_event_id": terminal_event["terminal_event_id"],
            "terminal_event": {"status": "launch_failed", "terminal": True},
        }
        self.engine._write_json_atomic(
            self.evidence_dir / f"{contract_id}-not-guaranteed.json", failure
        )
        return failure

    def complete_codex_desktop_handoff(self, contract_id: str) -> dict[str, Any]:
        """Verify and consume the one-shot evidence receipt produced by a Desktop task."""
        if not re.fullmatch(r"[0-9a-f]{32}", contract_id):
            raise SafetyError("Invalid Desktop completion contract ID")
        if (self.evidence_dir / f"{contract_id}-verified.json").is_file():
            raise SafetyError("Desktop completion receipt was already verified")
        receipt_path = self.desktop_receipt_dir / f"{contract_id}.json"
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyError("Desktop completion receipt is missing or invalid") from exc
        contract = self._read_contract_record(contract_id, allow_consumed=True)
        contract_record = json.loads(
            (self.contract_dir / f"{contract_id}.json").read_text(encoding="utf-8")
        )
        if not contract_record.get("consumed_at"):
            raise SafetyError("Desktop handoff was not consumed")
        expected_schema_path = self.evidence_dir / f"{contract_id}-desktop-schema.json"
        expected_output_path = self.evidence_dir / f"{contract_id}-desktop-output.json"
        expected_receipt = {
            "version": 1,
            "attempt_id": contract.attempt_id,
            "contract_id": contract.contract_id,
            "schema_path": str(expected_schema_path),
            "output_path": str(expected_output_path),
            "expires_at": contract.expires_at,
        }
        for key, expected in expected_receipt.items():
            if receipt.get(key) != expected:
                raise SafetyError(f"Desktop completion receipt mismatch: {key}")
        try:
            schema = json.loads(expected_schema_path.read_text(encoding="utf-8"))
            output = json.loads(expected_output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _OutputFailed("Desktop task returned no valid evidence envelope") from exc
        if receipt.get("schema_sha256") != _digest(schema):
            raise SafetyError("Desktop completion schema integrity check failed")
        prompt_digest = str(receipt.get("prompt_sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", prompt_digest):
            raise SafetyError("Desktop completion prompt digest is invalid")
        pack, commit, _, _ = self._validated_pack(contract.pack_id)
        if commit != contract.commit_sha or pack.repo_url != contract.repository_url:
            raise SafetyError("Pack provenance changed after Desktop handoff")
        checks = self._load_acceptance(pack, contract.skill_ids)
        verified = self._verify(contract, output, checks, prompt_digest)
        verified["user_result"] = self._user_result(contract, pack, output)
        verified["interactive_handoff"] = {
            "runtime": "codex",
            "state": "desktop_result_verified",
            "verification_session_resumed": False,
        }
        self._cleanup_temporary_artifacts(
            (expected_schema_path, expected_output_path, receipt_path)
        )
        materialization = self.materialization_dir / contract_id
        if materialization.is_dir() and not _is_link(materialization):
            self._make_tree_writable(materialization)
            shutil.rmtree(materialization)
        terminal_event = self._write_terminal_lifecycle(
            attempt_id=contract.attempt_id,
            contract_id=contract.contract_id,
            status="verified_completed",
        )
        verified["attempt_id"] = contract.attempt_id
        verified["terminal_event_id"] = terminal_event["terminal_event_id"]
        verified["terminal_event"] = {
            "status": "verified_completed",
            "terminal": True,
        }
        verified_path = self.evidence_dir / f"{contract_id}-verified.json"
        verified["user_result"]["details"]["evidence_file"] = str(verified_path)
        self.engine._write_json_atomic(verified_path, verified)
        return verified

    def prepare_web_handoff(self, contract_id: str) -> dict[str, Any]:
        """Consume one verified selection and return its single Web Claude prompt.

        Web Codex deliberately has no fallback here.  Its currently supported,
        authenticated prompt-input surface is absent, so calling code must show
        that leaf-specific error instead of relabelling ChatGPT or opening a CLI.
        """
        self.recover_interrupted_attempts()
        contract = self._read_contract(contract_id)
        if contract.runtime != "claude":
            raise _LaunchFailed(
                "Web Codex has no supported authenticated prompt input on this account"
            )
        pack, commit, _, _ = self._validated_pack(contract.pack_id)
        if commit != contract.commit_sha or pack.repo_url != contract.repository_url:
            raise SafetyError("Pack provenance changed after confirmation")
        prompt = self._task_envelope(contract, pack)
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self._consume(contract)
        return {
            "status": "web_prompt_ready",
            "runtime": "claude",
            "destination": "https://claude.ai/new",
            "contract_id": contract.contract_id,
            "attempt_id": contract.attempt_id,
            "project": contract.project,
            "skill_ids": list(contract.skill_ids),
            "prompt": prompt,
            "prompt_sha256": prompt_digest,
        }

    @staticmethod
    def _value_at(value: Any, path: str) -> Any:
        current = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                raise SafetyError(f"Acceptance evidence path is missing: {path}")
            current = current[part]
        return current

    @staticmethod
    def _const_schema(value: Any) -> dict[str, Any]:
        if value is None:
            value_type = "null"
        elif isinstance(value, bool):
            value_type = "boolean"
        elif isinstance(value, int):
            value_type = "integer"
        elif isinstance(value, float):
            value_type = "number"
        elif isinstance(value, list):
            return {
                "type": "array",
                "items": {"type": "string"},
                "const": value,
            }
        else:
            value_type = "string"
        return {"type": value_type, "const": value}

    @staticmethod
    def _request_aware_schema(example: Any) -> dict[str, Any]:
        """Keep an acceptance field's JSON shape without freezing its decision."""
        rule = ActivationEngine._const_schema(example)
        return {key: value for key, value in rule.items() if key != "const"}

    @staticmethod
    def _same_json_type(expected: Any, actual: Any) -> bool:
        if isinstance(expected, bool):
            return isinstance(actual, bool)
        if isinstance(expected, int):
            return isinstance(actual, int) and not isinstance(actual, bool)
        if isinstance(expected, float):
            return isinstance(actual, (int, float)) and not isinstance(actual, bool)
        if expected is None:
            return actual is None
        if isinstance(expected, list):
            return isinstance(actual, list) and all(
                isinstance(item, str) for item in actual
            )
        return isinstance(actual, str)

    @staticmethod
    def _output_schema(
        contract: LaunchContract, checks: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        result_properties: dict[str, Any] = {
            "task_output": {"type": "string", "minLength": 1},
            "saved_paths": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "changes": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
        }
        # Codex response_format uses strict JSON Schema: every declared property
        # must also be required. Empty saved_paths/changes arrays represent no change.
        required_result_fields = list(result_properties)
        package_selection = contract.selection_kind == "pack"
        for check in checks.values():
            for assertion in check["assertions"]:
                field = assertion["path"].split(".", 1)[1]
                expected = assertion["equals"]
                previous = result_properties.get(field)
                const_rule = ActivationEngine._const_schema(expected)
                rule = (
                    {
                        "anyOf": [
                            ActivationEngine._request_aware_schema(expected),
                            {"type": "null"},
                        ]
                    }
                    if package_selection
                    else const_rule
                )
                if previous is not None and previous != rule:
                    raise SafetyError(f"Conflicting acceptance assertions: result.{field}")
                result_properties[field] = rule
                if field not in required_result_fields:
                    required_result_fields.append(field)
        provenance_properties = {
            "pack_id": ActivationEngine._const_schema(contract.pack_id),
            "repository_url": ActivationEngine._const_schema(contract.repository_url),
            "commit_sha": ActivationEngine._const_schema(contract.commit_sha),
            "approved_by": ActivationEngine._const_schema(contract.approved_by),
            "approved_at": ActivationEngine._const_schema(contract.approved_at),
            "skill_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(contract.skill_ids)},
                "minItems": len(contract.skill_ids),
                "maxItems": len(contract.skill_ids),
            },
            "instruction_digest": ActivationEngine._const_schema(
                contract.instruction_digest
            ),
            "challenge_nonce": ActivationEngine._const_schema(contract.nonce),
            "applied_rules": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "completed_skill_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(contract.skill_ids)},
                "minItems": 1,
                "maxItems": (
                    len(contract.skill_ids) if package_selection else 1
                ),
            },
            "skill_execution_status": {"type": "string", "const": "completed"},
            "actual_request_sha256": ActivationEngine._const_schema(
                hashlib.sha256(contract.purpose.encode("utf-8")).hexdigest()
            ),
        }
        return {
            "type": "object",
            "required": ["evidence", "result"],
            "properties": {
                "evidence": {
                    "type": "object",
                    "required": list(provenance_properties),
                    "properties": provenance_properties,
                    "additionalProperties": False,
                },
                "result": {
                    "type": "object",
                    "required": required_result_fields,
                    "properties": result_properties,
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        }

    def _verify(
        self,
        contract: LaunchContract,
        output: dict[str, Any],
        checks: dict[str, dict[str, Any]],
        prompt_digest: str,
    ) -> dict[str, Any]:
        evidence = output.get("evidence")
        if not isinstance(evidence, dict):
            raise _OutputFailed("Codex output is missing evidence")
        read_evidence = {
            "pack_id": contract.pack_id,
            "repository_url": contract.repository_url,
            "commit_sha": contract.commit_sha,
            "approved_by": contract.approved_by,
            "approved_at": contract.approved_at,
            "skill_ids": list(contract.skill_ids),
            "instruction_digest": contract.instruction_digest,
            "challenge_nonce": contract.nonce,
        }
        for key, value in read_evidence.items():
            if evidence.get(key) != value:
                raise _OutputFailed(f"Skill read evidence mismatch: {key}")
        applied_rules = evidence.get("applied_rules")
        if not isinstance(applied_rules, list) or not applied_rules:
            raise _OutputFailed("Applied-rules evidence is required")
        if any(not isinstance(item, str) for item in applied_rules):
            raise _OutputFailed("Applied-rules evidence must contain only strings")
        completed_skill_ids = evidence.get("completed_skill_ids")
        if (
            not isinstance(completed_skill_ids, list)
            or not completed_skill_ids
            or len(completed_skill_ids) != len(set(completed_skill_ids))
            or any(skill not in contract.skill_ids for skill in completed_skill_ids)
        ):
            raise _AcceptanceFailed("Completed skill IDs are not a valid applied subset")
        expected_order = [
            skill for skill in contract.skill_ids if skill in completed_skill_ids
        ]
        if completed_skill_ids != expected_order:
            raise _AcceptanceFailed("Completed skill IDs must follow pack order")
        if contract.selection_kind == "skill" and completed_skill_ids != list(
            contract.skill_ids
        ):
            raise _AcceptanceFailed("Completed skill IDs do not match the selection")
        if contract.selection_kind == "pack":
            self._verify_pack_relations(
                self.config.packs[contract.pack_id],
                completed_skill_ids,
                applied_rules,
            )
        for skill in completed_skill_ids:
            if not any(item.startswith(f"{skill}:") for item in applied_rules):
                raise _AcceptanceFailed(
                    f"Applied-rules evidence does not identify selected skill: {skill}"
                )
        result = output.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("task_output"), str):
            raise _OutputFailed("Actual-request deliverable is missing: result.task_output")
        if not result["task_output"].strip():
            raise _OutputFailed("Actual-request deliverable is empty: result.task_output")
        if evidence.get("skill_execution_status") != "completed":
            raise _AcceptanceFailed("Skill execution did not report completion")
        actual_request_sha256 = hashlib.sha256(
            contract.purpose.encode("utf-8")
        ).hexdigest()
        if evidence.get("actual_request_sha256") != actual_request_sha256:
            raise _AcceptanceFailed("Completed skill evidence targets a different request")
        for skill, check in checks.items():
            for assertion in check["assertions"]:
                try:
                    actual = self._value_at(output, assertion["path"])
                except SafetyError as exc:
                    raise _AcceptanceFailed(str(exc)) from exc
                if skill in completed_skill_ids:
                    if contract.selection_kind == "pack":
                        if actual is None or not self._same_json_type(
                            assertion["equals"], actual
                        ):
                            raise _AcceptanceFailed(
                                f"Request-aware acceptance failed for {skill}: "
                                f"{assertion['path']}"
                            )
                        if not any(
                            item.startswith(f"{skill}:")
                            and assertion["path"] in item
                            for item in applied_rules
                        ):
                            raise _AcceptanceFailed(
                                f"Request-aware acceptance has no applied rule for {skill}: "
                                f"{assertion['path']}"
                            )
                    elif actual != assertion["equals"]:
                        raise _AcceptanceFailed(
                            f"Skill-specific acceptance failed for {skill}: "
                            f"{assertion['path']}"
                        )
                if skill not in completed_skill_ids and actual is not None:
                    raise _AcceptanceFailed(
                        f"Unapplied skill claimed an acceptance value: {skill}"
                    )
        return {
            "status": "verified_completed",
            "contract_id": contract.contract_id,
            "pack_id": contract.pack_id,
            "commit_sha": contract.commit_sha,
            "task_delivery_evidence": {"prompt_sha256": prompt_digest},
            "skill_read_evidence": read_evidence,
            "skill_execution_completion_evidence": {
                "completed_skill_ids": completed_skill_ids,
                "skill_execution_status": "completed",
                "actual_request_sha256": actual_request_sha256,
            },
            "output": output,
        }

    def _user_result(
        self,
        contract: LaunchContract,
        pack: Pack,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a Japanese result surface without exposing verification JSON."""
        result = output["result"]
        completed_skill_ids = output.get("evidence", {}).get("completed_skill_ids", [])
        skill_names = [
            pack.skill_display_name(skill) for skill in completed_skill_ids
        ]
        saved_paths = result.get("saved_paths", [])
        changes = result.get("changes", [])
        for field_name, values in (("saved_paths", saved_paths), ("changes", changes)):
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise _OutputFailed(
                    f"User-facing result field is invalid: result.{field_name}"
                )
        change_lines = [*(f"保存先: {path}" for path in saved_paths)]
        change_lines.extend(f"変更: {change}" for change in changes)
        if not change_lines:
            change_lines.append("保存先/変更の申告なし（結果のみ）")
        return {
            "title": "完了",
            "executed_skill": "、".join(skill_names),
            "request": contract.purpose,
            "result": result["task_output"],
            "saved_or_changed": "\n".join(change_lines),
            "details": {
                "verification_status": "verified_completed",
                "contract_id": contract.contract_id,
            },
        }

    def execute(
        self,
        contract_id: str,
        *,
        codex_executable: str | tuple[str, ...] = "codex",
        runtime_executable: str | tuple[str, ...] | None = None,
        interactive_handoff: bool = False,
    ) -> dict[str, Any]:
        self.recover_interrupted_attempts()
        contract = self._read_contract(contract_id)
        pack, commit, _, _ = self._validated_pack(contract.pack_id)
        if commit != contract.commit_sha or pack.repo_url != contract.repository_url:
            raise SafetyError("Pack provenance changed after confirmation")
        checks = self._load_acceptance(pack, contract.skill_ids)
        prompt = self._task_envelope(contract, pack)
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self._consume(contract)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.evidence_dir / f"{contract.contract_id}-output.json"
        event_path = self.evidence_dir / f"{contract.contract_id}-events.jsonl"
        schema_path = self.evidence_dir / f"{contract.contract_id}-schema.json"
        schema = self._output_schema(contract, checks)
        self.engine._write_json_atomic(schema_path, schema)
        process_marker_path = (
            self.process_dir / f"{contract.contract_id}-process.json"
        )
        self.engine._write_json_atomic(
            process_marker_path,
            {
                "attempt_id": contract.attempt_id,
                "contract_id": contract.contract_id,
                "pack_id": contract.pack_id,
                "commit_sha": contract.commit_sha,
                "prompt_sha256": prompt_digest,
                "schema_sha256": _digest(schema),
                "temporary_names": [
                    schema_path.name,
                    output_path.name,
                    event_path.name,
                ],
            },
        )
        requested_executable = runtime_executable
        if requested_executable is None:
            requested_executable = "claude" if contract.runtime == "claude" else codex_executable
        wrapper: str | None = None
        if isinstance(requested_executable, tuple):
            executable = list(requested_executable)
            resolved_executable = executable[0]
        else:
            resolved = requested_executable
            if os.name == "nt" and requested_executable.lower() == "codex":
                resolved = shutil.which("codex.cmd") or shutil.which("codex.exe") or requested_executable
            elif requested_executable.lower() == "claude":
                resolved = shutil.which("claude.exe") or shutil.which("claude") or requested_executable
            resolved_executable = resolved
            if os.name == "nt" and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
                wrapper = resolved
                executable = []
            else:
                executable = [resolved]
        if contract.runtime == "codex":
            runtime_args = [
                *codex_process_config_args(),
                "--ask-for-approval",
                "never",
                "exec",
                "--ephemeral",
                "--ignore-rules",
                "--json",
                "--sandbox",
                "read-only",
                "--cd",
                contract.project,
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
        else:
            runtime_args = [
                "--print",
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                "--permission-mode",
                "plan",
                "--tools",
                "",
            ]
        if wrapper is not None:
            command = [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                wrapper,
                *runtime_args,
            ]
        else:
            command = [*executable, *runtime_args]
        try:
            try:
                result = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **_windows_hidden_process_kwargs(),
                )
            except OSError as exc:
                raise _LaunchFailed(
                    f"Codex could not be started; skill use is not guaranteed: {exc}"
                ) from exc
            event_path.write_text(result.stdout, encoding="utf-8")
            if result.returncode != 0:
                raise _RuntimeFailed(
                    exit_code=result.returncode,
                    stderr=result.stderr,
                    stdout=result.stdout,
                )
            try:
                if contract.runtime == "codex":
                    response: object = None
                    output = json.loads(output_path.read_text(encoding="utf-8"))
                else:
                    response = json.loads(result.stdout)
                    if not isinstance(response, dict):
                        raise TypeError("Claude response is not an object")
                    output = response.get("structured_output")
                    if not isinstance(output, dict):
                        raise TypeError("Claude response has no structured_output")
                    self.engine._write_json_atomic(output_path, output)
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                raise _OutputFailed(
                    f"{contract.runtime.title()} returned no valid evidence envelope"
                ) from exc
            verified = self._verify(contract, output, checks, prompt_digest)
            verified["user_result"] = self._user_result(contract, pack, output)
            verified["interactive_handoff"] = {
                "runtime": contract.runtime,
                "state": (
                    "result_surface_ready" if interactive_handoff else "test_suppressed"
                ),
                "verification_session_resumed": False,
            }
            self._cleanup_temporary_artifacts(
                (schema_path, output_path, event_path, process_marker_path)
            )
            terminal_event = self._write_terminal_lifecycle(
                attempt_id=contract.attempt_id,
                contract_id=contract.contract_id,
                status="verified_completed",
            )
            verified["attempt_id"] = contract.attempt_id
            verified["terminal_event_id"] = terminal_event["terminal_event_id"]
            verified["terminal_event"] = {
                "status": "verified_completed",
                "terminal": True,
            }
            verified_path = self.evidence_dir / f"{contract.contract_id}-verified.json"
            verified["user_result"]["details"]["evidence_file"] = str(verified_path)
            self.engine._write_json_atomic(verified_path, verified)
            return verified
        except Exception as exc:
            failure_path = (
                self.evidence_dir / f"{contract.contract_id}-not-guaranteed.json"
            )
            if not isinstance(exc, _CleanupFailed):
                try:
                    self._cleanup_temporary_artifacts(
                        (schema_path, output_path, event_path, process_marker_path)
                    )
                except _CleanupFailed as cleanup_exc:
                    exc = cleanup_exc
            if isinstance(exc, _LaunchFailed):
                status = "launch_failed"
            elif isinstance(exc, _RuntimeFailed):
                status = "runtime_failed"
            elif isinstance(exc, _AcceptanceFailed):
                status = "acceptance_failed"
            elif isinstance(exc, _CleanupFailed):
                status = "cleanup_failed"
            else:
                status = "output_failed"
            terminal_event = self._write_terminal_lifecycle(
                attempt_id=contract.attempt_id,
                contract_id=contract.contract_id,
                status=status,
            )
            failure = {
                "status": status,
                "attempt_id": contract.attempt_id,
                "terminal_event_id": terminal_event["terminal_event_id"],
                "terminal_event": {"status": status, "terminal": True},
                "contract_id": contract.contract_id,
                "pack_id": contract.pack_id,
                "commit_sha": contract.commit_sha,
                "reason": status,
                "task_delivery_evidence": {"prompt_sha256": prompt_digest},
                "output_schema_evidence": {
                    "version": 1,
                    "sha256": _digest(schema),
                },
            }
            if isinstance(exc, _CleanupFailed):
                failure["unresolved_artifacts"] = [
                    {"name": path.name} for path in exc.paths
                ]
            if isinstance(exc, _RuntimeFailed):
                failure["runtime_failure_evidence"] = exc.diagnostic
            self.engine._write_json_atomic(
                self.evidence_dir / f"{contract.contract_id}-not-guaranteed.json",
                failure,
            )
            if isinstance(exc, SkillMagnetError):
                raise
            raise SafetyError(
                f"Skill use is not guaranteed: {exc}"
            ) from exc
