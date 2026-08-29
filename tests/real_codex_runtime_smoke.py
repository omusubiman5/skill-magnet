from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from skill_magnet.activation import (
    ActivationEngine,
    LaunchContract,
    _runtime_failure_diagnostic,
    _windows_hidden_process_kwargs,
    codex_process_config_args,
)
from skill_magnet.core import Config


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contract(path: Path) -> LaunchContract:
    raw = json.loads(path.read_text(encoding="utf-8"))
    fields = LaunchContract.__dataclass_fields__
    value = {name: raw[name] for name in fields}
    value["skill_ids"] = tuple(value["skill_ids"])
    return LaunchContract(**value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-contract", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()

    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    contract = _contract(args.source_contract.resolve())
    config = Config.load(args.config.resolve())
    engine = ActivationEngine(config, evidence_dir / "state")
    pack, commit, _, _ = engine._validated_pack(contract.pack_id)
    if commit != contract.commit_sha:
        raise RuntimeError("source contract commit does not match current approved pack")
    checks = engine._load_acceptance(pack, contract.skill_ids)
    prompt = engine._task_envelope(contract, pack)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    schema = engine._output_schema(contract, checks)
    run_id = uuid.uuid4().hex
    codex = shutil.which("codex.cmd") or shutil.which("codex.exe") or "codex"

    started_at = _timestamp()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(
        prefix="skill-magnet-real-runtime-", dir=evidence_dir
    ) as raw_dir_name:
        raw_dir = Path(raw_dir_name)
        schema_path = raw_dir / "schema.json"
        output_path = raw_dir / "output.json"
        raw_stderr_path = raw_dir / "raw-stderr.txt"
        schema_path.write_text(
            json.dumps(schema, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
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
        if Path(codex).suffix.lower() in {".cmd", ".bat"}:
            command = [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                codex,
                *runtime_args,
            ]
        else:
            command = [codex, *runtime_args]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_windows_hidden_process_kwargs(),
        )
        stdout, stderr = process.communicate(prompt)
        raw_stderr_path.write_text(stderr, encoding="utf-8")
        diagnostic = _runtime_failure_diagnostic(process.returncode, stderr, stdout)
        runtime_pid = process.pid
        runtime_pid_terminated = process.poll() is not None
        verified = None
        status = "runtime_failed"
        if process.returncode == 0:
            output = json.loads(output_path.read_text(encoding="utf-8"))
            verified = engine._verify(contract, output, checks, prompt_sha256)
            status = verified["status"]

    runtime_stderr_evidence = {
        "stderr_present": diagnostic["stderr_present"],
        "stderr_sha256": diagnostic["stderr_sha256"],
    }
    manifest = {
        "run_id": run_id,
        "source_contract_id": contract.contract_id,
        "source_attempt_id": contract.attempt_id,
        "skill_id": contract.selected_skill_id,
        "actual_request_sha256": hashlib.sha256(
            contract.purpose.encode("utf-8")
        ).hexdigest(),
        "prompt_sha256": prompt_sha256,
        "started_at": started_at,
        "finished_at": _timestamp(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "runtime": "codex-cli 0.148.0",
        "runtime_pid": runtime_pid,
        "runtime_pid_terminated": runtime_pid_terminated,
        "console_creation_flag": "CREATE_NO_WINDOW",
        "process_local_disabled_mcp_servers": [
            "cloudflare-builds",
            "cloudflare-observability",
            "unreal-mcp",
        ],
        "status": status,
        "runtime_failure_evidence": diagnostic if process.returncode != 0 else None,
        "runtime_stderr_evidence": runtime_stderr_evidence,
        "verified_contract_id": verified["contract_id"] if verified else None,
    }
    manifest_path = evidence_dir / f"{run_id}-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(str(manifest_path))
    print(status)
    return 0 if status == "verified_completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
