from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skill_magnet.activation import ActivationEngine
from skill_magnet.core import Config


PURPOSE = (
    "Design a headless, non-interactive Codex CI audit and fix workflow. "
    "Use a process-scoped API key; parallelize only read-heavy independent research and "
    "use one writer; keep the generation job read-only and hand a binary patch to a "
    "separate write job; choose only the minimal context entry with an explicit task "
    "contract; control every egress surface without bypass; use stdin, JSONL events, a "
    "strict final schema, and non-zero failure handling; select codex exec; require the "
    "docs MCP with a read-only tool allowlist and deny write tools; run read-only with no "
    "mid-run approval."
)


def main() -> int:
    config = Config.load(ROOT / "skill-magnet.json")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        project = root / "target-project"
        project.mkdir()
        engine = ActivationEngine(config, root / "state")
        plan = engine.plan(
            platform="windows" if sys.platform == "win32" else "macos",
            project=project,
            pack_id="codex-delivery-assurance",
            runtime="codex",
            purpose=PURPOSE,
            ttl_minutes=30,
        )
        contract = engine.confirm(plan, confirmed=True)
        result = engine.execute(contract.contract_id, codex_executable="codex")
        # Keep the audit artifact printable on Windows consoles using CP932.
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0 if result.get("status") == "verified_applied" else 1


if __name__ == "__main__":
    raise SystemExit(main())
