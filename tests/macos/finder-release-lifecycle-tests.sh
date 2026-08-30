#!/bin/zsh
set -euo pipefail

test_root="$(mktemp -d "${RUNNER_TEMP}/skill-magnet-finder.XXXXXX")"
original_home="${HOME}"
export HOME="${test_root}/home"
mkdir -p "${HOME}" "${test_root}/selected folder"

cleanup() {
  export HOME="${original_home}"
  if [[ -d "${test_root}" && "${test_root}" == "${RUNNER_TEMP}/skill-magnet-finder."* ]]; then
    rm -rf -- "${test_root}"
  fi
}
trap cleanup EXIT

workflow="${HOME}/Library/Services/Skill Magnet.workflow"
for runtime in codex claude; do
  export SKILL_MAGNET_FINDER_E2E_RUNTIME="${runtime}"
  export SKILL_MAGNET_FINDER_E2E_PROBE="${test_root}/finder-${runtime}-probe.json"
  python -m skill_magnet install-context-menu --platform macos --confirm
  document="${workflow}/Contents/document.wflow"
  [[ -f "${document}" ]] || { print -u2 "Finder workflow was not installed"; exit 1; }

  /usr/bin/automator -v -i "${test_root}/selected folder" "${workflow}"
  for attempt in {1..100}; do
    [[ -f "${SKILL_MAGNET_FINDER_E2E_PROBE}" ]] && break
    sleep 0.1
  done
  [[ -f "${SKILL_MAGNET_FINDER_E2E_PROBE}" ]] || {
    print -u2 "Finder workflow did not execute its adapter"
    exit 1
  }
  python - "${SKILL_MAGNET_FINDER_E2E_PROBE}" "${test_root}/selected folder" "${runtime}" <<'PY'
import json
import pathlib
import sys

record = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_path = str(pathlib.Path(sys.argv[2]).resolve())
runtime = sys.argv[3]
assert record["adapter"] == "macos_finder_quick_action"
assert record["selected_path"] == expected_path
assert record["pack_id"] == "codex-delivery-assurance"
assert record["selection_kind"] == "pack"
assert len(record["skill_ids"]) == 9
assert record["runtime"] == runtime
assert record["status"] == (
    "desktop_handoff_ready" if runtime == "codex" else "web_prompt_ready"
)
assert record["result_verification"] == "not_available"
assert record["verified_completed"] is False
assert record["delivery"] == {
    "project": expected_path,
    "destination": (
        "codex://threads/new" if runtime == "codex" else "https://claude.ai/new"
    ),
    "prompt_present": True,
}
assert record["actual_request_sha256"]
assert record["instruction_digest"]
assert record["index_digest"]
assert record["prompt_sha256"]
PY
  python -m skill_magnet uninstall-context-menu --platform macos --confirm
  [[ ! -e "${workflow}" ]] || { print -u2 "Finder workflow remains installed"; exit 1; }
done
if find "${HOME}/Library/Services" -maxdepth 1 -name '.skill-magnet-workflow-*' -print -quit |
    grep -q .; then
  print -u2 "Finder workflow transaction residue remains"
  exit 1
fi

print "finder-release-lifecycle-tests: OK"
