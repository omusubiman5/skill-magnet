from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .core import Config, Engine, Pack, SafetyError, SkillMagnetError


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
    skill_ids: tuple[str, ...]
    instruction_digest: str
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
    SUPPORTED_RUNTIMES = {"codex"}

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
        self.now = now

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

    def _load_acceptance(self, pack: Pack) -> dict[str, dict[str, Any]]:
        checks: dict[str, dict[str, Any]] = {}
        for skill in pack.skills:
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
        ttl_minutes: int = 30,
    ) -> dict[str, Any]:
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
        checks = self._load_acceptance(pack)
        instructions = self._instructions(pack)
        instruction_digest = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
        return {
            "operation": "verified-codex-launch",
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
            "skill_ids": list(pack.skills),
            "skill_hashes": hashes,
            "instruction_digest": instruction_digest,
            "acceptance_digests": {
                skill: _digest(check) for skill, check in checks.items()
            },
            "ttl_minutes": ttl_minutes,
            "requires_explicit_confirmation": True,
            "local_skill_placement": False,
        }

    @staticmethod
    def _instructions(pack: Pack) -> str:
        parts: list[str] = []
        for skill in pack.skills:
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
            "skill_ids",
            "instruction_digest",
            "ttl_minutes",
        }
        if plan.get("operation") != "verified-codex-launch" or not required <= plan.keys():
            raise SafetyError("Invalid activation plan")
        refreshed = self.plan(
            platform=str(plan["platform"]),
            project=Path(str(plan["project"])),
            pack_id=str(plan["pack_id"]),
            runtime=str(plan["runtime"]),
            purpose=str(plan["purpose"]),
            ttl_minutes=int(plan["ttl_minutes"]),
        )
        for key in required - {"ttl_minutes"}:
            if refreshed[key] != plan[key]:
                raise SafetyError(f"Activation plan changed before confirmation: {key}")
        confirmed_at = self.now()
        payload: dict[str, Any] = {
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
            "skill_ids": tuple(plan["skill_ids"]),
            "instruction_digest": plan["instruction_digest"],
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
            "Read every supplied skill instruction and apply it to the user purpose. "
            "In evidence.applied_rules, include at least one concrete applied rule for "
            "each selected skill and begin that rule with the exact skill ID followed "
            "by a colon. "
            "Return only the JSON evidence envelope requested by the output schema.\n\n"
            f"{self._instructions(pack)}"
        )

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
            raise SafetyError("Codex output is missing evidence")
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
                raise SafetyError(f"Skill read evidence mismatch: {key}")
        applied_rules = evidence.get("applied_rules")
        if not isinstance(applied_rules, list) or not applied_rules:
            raise SafetyError("Applied-rules evidence is required")
        if any(not isinstance(item, str) for item in applied_rules):
            raise SafetyError("Applied-rules evidence must contain only strings")
        for skill in contract.skill_ids:
            if not any(skill in item for item in applied_rules):
                raise SafetyError(
                    f"Applied-rules evidence does not identify selected skill: {skill}"
                )
        for skill, check in checks.items():
            for assertion in check["assertions"]:
                actual = self._value_at(output, assertion["path"])
                if actual != assertion["equals"]:
                    raise SafetyError(
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

    def execute(
        self,
        contract_id: str,
        *,
        codex_executable: str | tuple[str, ...] = "codex",
    ) -> dict[str, Any]:
        contract = self._read_contract(contract_id)
        pack, commit, _ = self._validated_pack(contract.pack_id)
        if commit != contract.commit_sha or pack.repo_url != contract.repository_url:
            raise SafetyError("Pack provenance changed after confirmation")
        checks = self._load_acceptance(pack)
        prompt = self._task_envelope(contract, pack)
        prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self._consume(contract)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.evidence_dir / f"{contract.contract_id}-output.json"
        event_path = self.evidence_dir / f"{contract.contract_id}-events.jsonl"
        schema_path = self.evidence_dir / f"{contract.contract_id}-schema.json"
        schema = self._output_schema(contract, checks)
        self.engine._write_json_atomic(schema_path, schema)
        wrapper: str | None = None
        if isinstance(codex_executable, tuple):
            executable = list(codex_executable)
        else:
            resolved = codex_executable
            if os.name == "nt" and codex_executable.lower() == "codex":
                resolved = shutil.which("codex.cmd") or shutil.which("codex.exe") or codex_executable
            if os.name == "nt" and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
                wrapper = resolved
                executable = []
            else:
                executable = [resolved]
        runtime_args = [
            "--ask-for-approval",
            "never",
            "exec",
            "--ephemeral",
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
        if wrapper is not None:
            command = [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/s",
                "/c",
                subprocess.list2cmdline([wrapper, *runtime_args]),
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
                raise SafetyError(
                    f"Codex could not be started; skill use is not guaranteed: {exc}"
                ) from exc
            event_path.write_text(result.stdout, encoding="utf-8")
            if result.returncode != 0:
                detail = result.stderr.strip()
                if result.stdout.strip():
                    detail = (detail + "\nCodex events:\n" + result.stdout.strip()).strip()
                raise SafetyError(
                    f"Codex execution failed; skill use is not guaranteed: "
                    f"{detail or result.returncode}"
                )
            try:
                output = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SafetyError("Codex returned no valid evidence envelope") from exc
            verified = self._verify(contract, output, checks, prompt_digest)
            self.engine._write_json_atomic(
                self.evidence_dir / f"{contract.contract_id}-verified.json", verified
            )
            return verified
        except Exception as exc:
            failure = {
                "status": "not_guaranteed",
                "contract_id": contract.contract_id,
                "pack_id": contract.pack_id,
                "commit_sha": contract.commit_sha,
                "reason": str(exc),
                "task_delivery_evidence": {"prompt_sha256": prompt_digest},
            }
            self.engine._write_json_atomic(
                self.evidence_dir / f"{contract.contract_id}-not-guaranteed.json",
                failure,
            )
            if isinstance(exc, SkillMagnetError):
                raise
            raise SafetyError(
                f"Skill use is not guaranteed: {exc}"
            ) from exc
        finally:
            schema_path.unlink(missing_ok=True)
