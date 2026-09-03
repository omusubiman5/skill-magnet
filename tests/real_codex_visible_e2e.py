from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from skill_magnet.activation import ActivationEngine
from skill_magnet.core import Config


ACTUAL_REQUEST = (
    "このプロジェクトの未完了リリースをCIで監査して修正patchを安全に引き渡す計画を、"
    "実行mode、sandboxとapproval、egress、MCP、bounded subagents、CI patch handoffの"
    "観点をINDEXに従って必要なものだけ組み合わせ、日本語で具体化してください。"
    "この実機受入ではファイルを変更せず、採用したskillと組合せ理由も自然文で説明してください。"
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()

    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = evidence_dir / "visible-runtime-metadata.json"
    engine = ActivationEngine(Config.load(args.config.resolve()), evidence_dir / "state")
    plan = engine.plan(
        platform="windows",
        project=args.project.resolve(),
        pack_id="codex-cli",
        runtime="codex",
        purpose=ACTUAL_REQUEST,
    )
    contract = engine.confirm(plan, confirmed=True)
    adapter = Path(__file__).with_name("visible_codex_runtime_adapter.py").resolve()
    result = engine.execute(
        contract.contract_id,
        runtime_executable=(sys.executable, str(adapter), str(metadata_path)),
    )
    record = {
        "captured_at": _timestamp(),
        "status": result["status"],
        "contract_id": contract.contract_id,
        "attempt_id": contract.attempt_id,
        "selected_skill_ids": list(contract.skill_ids),
        "actual_request": contract.purpose,
        "actual_request_sha256": hashlib.sha256(
            contract.purpose.encode("utf-8")
        ).hexdigest(),
        "instruction_digest": contract.instruction_digest,
        "acceptance_digests": contract.acceptance_digests,
        "verified_evidence_path": result["user_result"]["details"]["evidence_file"],
        "task_output": result["user_result"]["result"],
        "user_result": result["user_result"],
    }
    (evidence_dir / "verified-visible-run.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(record, ensure_ascii=False))
    return 0 if result["status"] == "verified_completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
