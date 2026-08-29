from __future__ import annotations

import json
import sys
from pathlib import Path

from skill_magnet.ui import show_context_result


def main() -> None:
    record = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    show_context_result(
        {"status": record["status"], "user_result": record["user_result"]}
    )


if __name__ == "__main__":
    main()
