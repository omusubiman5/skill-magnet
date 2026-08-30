from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skill_magnet.activation import ActivationEngine
from skill_magnet.core import Config
from skill_magnet.ui import deliver_prepared_codex_handoff


ACTUAL_REQUEST = (
    "このプロジェクトの未完了リリースをCIで監査して修正patchを安全に引き渡す計画を、"
    "実行mode、sandboxとapproval、egress、MCP、bounded subagents、CI patch handoffの"
    "観点をINDEXに従って必要なものだけ組み合わせ、日本語で具体化してください。"
    "この実機受入ではファイルを変更せず、採用したskillと組合せ理由も自然文で説明してください。"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()

    config = Config.load(args.config)
    engine = ActivationEngine(config, args.state_dir)
    plan = engine.plan(
        platform="windows",
        project=args.project,
        pack_id="codex-delivery-assurance",
        runtime="codex",
        purpose=ACTUAL_REQUEST,
    )
    contract = engine.confirm(plan, confirmed=True)
    result = deliver_prepared_codex_handoff(engine, contract.contract_id)
    if result["status"] != "desktop_handoff_ready":
        raise RuntimeError(f"unexpected handoff state: {result['status']}")
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": result["status"],
        "desktop_result_verification": result["desktop_result_verification"],
        "verified_completed": result["verified_completed"],
        "contract_id": result["contract_id"],
        "attempt_id": result["attempt_id"],
        "skill_ids": list(contract.skill_ids),
        "project": str(args.project.resolve()),
        "actual_request": ACTUAL_REQUEST,
        "actual_request_sha256": hashlib.sha256(
            ACTUAL_REQUEST.encode("utf-8")
        ).hexdigest(),
        "prompt_sha256": result["prompt_sha256"],
        "instruction_digest": result["instruction_digest"],
        "acceptance_digests": result["acceptance_digests"],
        "destination": result["destination"],
    }
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
