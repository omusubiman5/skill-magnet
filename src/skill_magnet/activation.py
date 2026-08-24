from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .core import Config, Engine, Pack, SafetyError, SkillMagnetError


class _LaunchFailed(SafetyError):
    """The verified runtime process could not be started."""


class _OutputFailed(SafetyError):
    """The runtime did not produce a valid evidence envelope."""


class _AcceptanceFailed(SafetyError):
    """The runtime output did not satisfy skill-specific acceptance."""


class _CleanupFailed(SafetyError):
    """One or more temporary runtime artifacts remain."""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths
        super().__init__("Temporary activation artifact cleanup failed")


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
        self.now = now

    def _write_terminal_lifecycle(
        self,
        *,
        attempt_id: str,
        contract_id: str,
        status: str,
        terminal_event_id: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "attempt_id": attempt_id,
            "contract_id": contract_id,
            "terminal_event_id": terminal_event_id or uuid.uuid4().hex,
            "status": status,
            "terminal": True,
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

    def _validated_pack(self, pack_id: str) -> tuple[Pack, str, dict[str, str]]:
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
        return pack, commit, hashes

    @staticmethod
    def _acceptance_path(pack: Pack, skill: str) -> Path:
        return pack.source / skill / "acceptance.json"

    @staticmethod
    def approved_blob_digest(pack: Pack, skill: str, filename: str) -> str:
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
        pack, commit, hashes = self._validated_pack(pack_id)
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

    def _read_contract(self, contract_id: str) -> LaunchContract:
        path = self.contract_dir / f"{contract_id}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyError(f"Launch contract is missing or invalid: {contract_id}") from exc
        if value.get("consumed_at"):
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
        }
        return (
            "Skill Magnet verified task envelope.\n"
            f"PROVENANCE={json.dumps(provenance, ensure_ascii=False, sort_keys=True)}\n"
            f"PURPOSE={contract.purpose}\n"
            f"TARGET_PROJECT={contract.project}\n"
            "Read every supplied skill instruction and apply it to the user purpose. "
            "In evidence.applied_rules, include at least one concrete applied rule for "
            "each selected skill and begin that rule with the exact skill ID followed "
            "by a colon. "
            "Return only the JSON evidence envelope requested by the output schema.\n\n"
            f"{self._instructions(pack, contract.skill_ids)}"
        )

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
        pack, commit, _ = self._validated_pack(contract.pack_id)
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
        else:
            value_type = "string"
        return {"type": value_type, "const": value}

    @staticmethod
    def _output_schema(
        contract: LaunchContract, checks: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        result_properties: dict[str, Any] = {}
        for check in checks.values():
            for assertion in check["assertions"]:
                field = assertion["path"].split(".", 1)[1]
                expected = assertion["equals"]
                previous = result_properties.get(field)
                rule = ActivationEngine._const_schema(expected)
                if previous is not None and previous != rule:
                    raise SafetyError(f"Conflicting acceptance assertions: result.{field}")
                result_properties[field] = rule
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
                    "required": list(result_properties),
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
        expected = {
            "pack_id": contract.pack_id,
            "repository_url": contract.repository_url,
            "commit_sha": contract.commit_sha,
            "approved_by": contract.approved_by,
            "approved_at": contract.approved_at,
            "skill_ids": list(contract.skill_ids),
            "instruction_digest": contract.instruction_digest,
            "challenge_nonce": contract.nonce,
        }
        for key, value in expected.items():
            if evidence.get(key) != value:
                raise _OutputFailed(f"Skill read evidence mismatch: {key}")
        applied_rules = evidence.get("applied_rules")
        if not isinstance(applied_rules, list) or not applied_rules:
            raise _OutputFailed("Applied-rules evidence is required")
        if any(not isinstance(item, str) for item in applied_rules):
            raise _OutputFailed("Applied-rules evidence must contain only strings")
        for skill in contract.skill_ids:
            if not any(skill in item for item in applied_rules):
                raise _AcceptanceFailed(
                    f"Applied-rules evidence does not identify selected skill: {skill}"
                )
        for skill, check in checks.items():
            for assertion in check["assertions"]:
                try:
                    actual = self._value_at(output, assertion["path"])
                except SafetyError as exc:
                    raise _AcceptanceFailed(str(exc)) from exc
                if actual != assertion["equals"]:
                    raise _AcceptanceFailed(
                        f"Skill-specific acceptance failed for {skill}: "
                        f"{assertion['path']}"
                    )
        return {
            "status": "verified_applied",
            "contract_id": contract.contract_id,
            "pack_id": contract.pack_id,
            "commit_sha": contract.commit_sha,
            "task_delivery_evidence": {"prompt_sha256": prompt_digest},
            "skill_read_evidence": expected,
            "skill_specific_application_evidence": {
                skill: _digest(check) for skill, check in checks.items()
            },
            "output": output,
        }

    @staticmethod
    def _session_id(runtime: str, stdout: str, response: object) -> str:
        """Read the persisted session identity emitted by the real runtime."""
        if runtime == "codex":
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "thread.started" and isinstance(
                    event.get("thread_id"), str
                ):
                    return str(event["thread_id"])
        elif isinstance(response, dict) and isinstance(response.get("session_id"), str):
            return str(response["session_id"])
        raise _OutputFailed(f"{runtime.title()} returned no resumable session identity")

    @staticmethod
    def _codex_interactive_executable(wrapper: str) -> str | None:
        """Prefer the native Codex executable so the recorded PID is the app PID."""
        wrapper_path = Path(wrapper)
        package_root = wrapper_path.parent / "node_modules" / "@openai" / "codex"
        matches = sorted(
            package_root.glob(
                "node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe"
            )
        )
        return str(matches[0]) if matches else shutil.which("codex.exe")

    @staticmethod
    def _windows_handoff_processes(session_id: str) -> list[dict[str, Any]]:
        """Read only processes whose argv proves ownership by this handoff session."""
        script = (
            "$needle=$env:SKILL_MAGNET_HANDOFF_SESSION;"
            "$rows=Get-CimInstance Win32_Process|Where-Object{"
            "$_.Name -in @('codex.exe','claude.exe','cmd.exe','pythonw.exe') -and "
            "$_.CommandLine -like ('*'+$needle+'*')}|"
            "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine;"
            "@($rows)|ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "SKILL_MAGNET_HANDOFF_SESSION": session_id},
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return []
        if isinstance(value, dict):
            value = [value]
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    @classmethod
    def _terminate_failed_handoff(
        cls,
        terminal_process: subprocess.Popen[Any],
        session_id: str,
        *,
        owned_runtime_pids: tuple[int, ...] = (),
    ) -> None:
        """Terminate/wait only the failed attempt launcher and proven runtime trees."""
        owned = set(owned_runtime_pids)
        owned.update(
            row["ProcessId"]
            for row in cls._windows_handoff_processes(session_id)
            if isinstance(row.get("ProcessId"), int)
        )
        if terminal_process.poll() is None:
            terminal_process.terminate()
            try:
                terminal_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                terminal_process.kill()
                terminal_process.wait(timeout=5)
        for process_id in sorted(owned):
            subprocess.run(
                ["taskkill.exe", "/PID", str(process_id), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            residual = cls._windows_handoff_processes(session_id)
            live_owned = []
            for process_id in owned:
                query = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        f"if(Get-Process -Id {process_id} -ErrorAction SilentlyContinue){{exit 1}}",
                    ],
                    capture_output=True,
                    check=False,
                )
                if query.returncode != 0:
                    live_owned.append(process_id)
            if not residual and not live_owned:
                return
            time.sleep(0.1)
        raise _CleanupFailed(())

    def _launch_interactive_session(
        self,
        contract: LaunchContract,
        *,
        runtime: str,
        session_id: str,
        resolved_executable: str,
    ) -> dict[str, Any]:
        """Open the verified session in the selected real interactive runtime."""
        if os.name != "nt":
            raise _LaunchFailed(
                "A verified user-visible runtime handoff is not implemented on this platform"
            )
        if runtime == "codex":
            native = self._codex_interactive_executable(resolved_executable)
            if native:
                target_command = [
                    native,
                    "--cd",
                    contract.project,
                    "--no-alt-screen",
                    "resume",
                    session_id,
                ]
            else:
                target_command = [
                    os.environ.get("COMSPEC", "cmd.exe"),
                    "/d",
                    "/k",
                    resolved_executable,
                    "--cd",
                    contract.project,
                    "--no-alt-screen",
                    "resume",
                    session_id,
                ]
        else:
            target_command = [resolved_executable, "--resume", session_id]
        terminal = shutil.which("wt.exe")
        if not terminal:
            raise _LaunchFailed("Windows Terminal is required for a visible runtime handoff")
        command = [
            terminal,
            "-w",
            "new",
            "new-tab",
            "--title",
            f"Skill Magnet — {runtime.title()}",
            "--startingDirectory",
            contract.project,
            *target_command,
        ]
        try:
            terminal_process = subprocess.Popen(
                command,
                cwd=contract.project,
            )
        except OSError as exc:
            raise _LaunchFailed(
                f"{runtime.title()} interactive application could not be started: {exc}"
            ) from exc
        process_name = Path(target_command[0]).name
        probe = (
            "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
            "& { param($needle,$name) "
            "$p=Get-CimInstance Win32_Process|Where-Object{"
            "$_.Name -eq $name -and $_.CommandLine -like ('*'+$needle+'*')"
            "}|Select-Object -First 1 ProcessId,ExecutablePath,CommandLine;"
            "if($p){$p|ConvertTo-Json -Compress} }"
        )
        deadline = time.monotonic() + 10
        record: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            query = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-Command",
                    probe,
                    session_id,
                    process_name,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if query.returncode == 0 and query.stdout.strip():
                try:
                    value = json.loads(query.stdout)
                except json.JSONDecodeError:
                    value = None
                if isinstance(value, dict) and isinstance(value.get("ProcessId"), int):
                    record = {
                        "runtime": runtime,
                        "session_id": session_id,
                        "pid": value["ProcessId"],
                        "executable": value.get("ExecutablePath") or target_command[0],
                        "command_line": value.get("CommandLine"),
                        "command": target_command,
                        "state": "interactive_ready",
                        "terminal_launcher_pid": terminal_process.pid,
                    }
                    break
            time.sleep(0.2)
        if record is None:
            self._terminate_failed_handoff(terminal_process, session_id)
            raise _LaunchFailed(
                f"{runtime.title()} interactive application did not report a live PID"
            )
        return record

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
        pack, commit, _ = self._validated_pack(contract.pack_id)
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
                "--ask-for-approval",
                "never",
                "exec",
                "--ignore-user-config",
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
                )
            except OSError as exc:
                raise _LaunchFailed(
                    f"Codex could not be started; skill use is not guaranteed: {exc}"
                ) from exc
            event_path.write_text(result.stdout, encoding="utf-8")
            if result.returncode != 0:
                raise _OutputFailed(
                    f"{contract.runtime.title()} execution failed; skill use is not guaranteed"
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
            default_runtime_executable = (
                runtime_executable is None
                and (
                    (contract.runtime == "codex" and codex_executable == "codex")
                    or contract.runtime == "claude"
                )
            )
            should_handoff = (
                interactive_handoff
                and default_runtime_executable
                and isinstance(result, subprocess.CompletedProcess)
            )
            if should_handoff:
                session_id = self._session_id(
                    contract.runtime, result.stdout, response
                )
                verified["interactive_handoff"] = self._launch_interactive_session(
                    contract,
                    runtime=contract.runtime,
                    session_id=session_id,
                    resolved_executable=resolved_executable,
                )
            else:
                verified["interactive_handoff"] = {
                    "runtime": contract.runtime,
                    "state": "test_suppressed",
                }
            self._cleanup_temporary_artifacts(
                (schema_path, output_path, event_path, process_marker_path)
            )
            terminal_event = self._write_terminal_lifecycle(
                attempt_id=contract.attempt_id,
                contract_id=contract.contract_id,
                status="verified_applied",
            )
            verified["attempt_id"] = contract.attempt_id
            verified["terminal_event_id"] = terminal_event["terminal_event_id"]
            verified["terminal_event"] = {
                "status": "verified_applied",
                "terminal": True,
            }
            self.engine._write_json_atomic(
                self.evidence_dir / f"{contract.contract_id}-verified.json", verified
            )
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
            self.engine._write_json_atomic(
                self.evidence_dir / f"{contract.contract_id}-not-guaranteed.json",
                failure,
            )
            if isinstance(exc, SkillMagnetError):
                raise
            raise SafetyError(
                f"Skill use is not guaranteed: {exc}"
            ) from exc
