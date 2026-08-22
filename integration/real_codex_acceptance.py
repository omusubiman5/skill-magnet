from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from skill_magnet.activation import ActivationEngine
from tests.test_activation import ActivationEndToEndTest


def main() -> int:
    fixture = ActivationEndToEndTest(methodName="runTest")
    fixture.setUp()
    try:
        engine = ActivationEngine(fixture.config, fixture.state)
        plan = fixture._plan(engine, "windows" if sys.platform == "win32" else "macos")
        contract = engine.confirm(plan, confirmed=True)
        result = engine.execute(contract.contract_id, codex_executable="codex")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status") == "verified_applied" else 1
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    raise SystemExit(main())
