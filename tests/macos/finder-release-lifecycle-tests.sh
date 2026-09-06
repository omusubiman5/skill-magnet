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
  probe="${test_root}/finder-${runtime}-probe.json"
  python -m skill_magnet install-context-menu --platform macos --confirm
  document="${workflow}/Contents/document.wflow"
  [[ -f "${document}" ]] || { print -u2 "Finder workflow was not installed"; exit 1; }
  production_digest="$(shasum -a 256 "${document}" | awk '{print $1}')"

  menu_status="$(python -m skill_magnet context-menu-status --platform macos)"
  python - "${menu_status}" <<'PY'
import json
import sys

status = json.loads(sys.argv[1])
assert status["installed"] is True
assert status["workflow_contract_valid"] is True
assert status["workflow_contract_matches_config"] is True
assert status["release_probe_present"] is False
assert status["transaction_residue"] == []
assert status["usable_installed_state"] is True
PY

  # Exercise Automator with a test-only copy.  The installed production
  # workflow remains byte-for-byte free of release-probe behavior.
  test_workflow="${test_root}/Skill Magnet ${runtime} probe.workflow"
  cp -R -- "${workflow}" "${test_workflow}"
  test_document="${test_workflow}/Contents/document.wflow"
  python - "${test_document}" "${probe}" "${runtime}" <<'PY'
import pathlib
import plistlib
import shlex
import sys

document = pathlib.Path(sys.argv[1])
payload = plistlib.loads(document.read_bytes())
parameters = payload["actions"][0]["action"]["ActionParameters"]
command = parameters["COMMAND_STRING"]
assert "--release-probe" not in command
parameters["COMMAND_STRING"] = (
    command
    + " --release-probe "
    + shlex.quote(sys.argv[2])
    + " --release-probe-runtime "
    + shlex.quote(sys.argv[3])
)
document.write_bytes(plistlib.dumps(payload))
PY

  /usr/bin/automator -v -i "${test_root}/selected folder" "${test_workflow}"
  for attempt in {1..100}; do
    [[ -f "${probe}" ]] && break
    sleep 0.1
  done
  [[ -f "${probe}" ]] || {
    print -u2 "Finder workflow did not execute its adapter"
    exit 1
  }
  python - "${probe}" "${test_root}/selected folder" "${runtime}" <<'PY'
import json
import pathlib
import sys

record = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_path = str(pathlib.Path(sys.argv[2]).resolve())
runtime = sys.argv[3]
assert record["adapter"] == "macos_finder_quick_action"
assert record["selected_path"] == expected_path
assert record["pack_id"] == "codex-cli"
assert record["selection_kind"] == "pack"
assert len(record["skill_ids"]) == 9
assert record["runtime"] == runtime
assert record["status"] == (
    "desktop_handoff_ready" if runtime == "codex" else "desktop_handoff_prepared"
)
assert record["result_verification"] == "not_claimed_by_design"
assert record["handoff_completed"] is True
assert record["answer_completion_claimed"] is False
assert record["billing_boundary"] == "existing_plan_no_api_key"
assert "verified_completed" not in record
assert record["delivery"] == {
    "project": expected_path,
    "destination": (
        "codex://threads/new" if runtime == "codex" else "claude://code/new"
    ),
    "prompt_present": True,
}
assert record["actual_request_sha256"]
assert record["instruction_digest"]
assert record["index_digest"]
assert record["prompt_sha256"]
PY
  [[ "$(shasum -a 256 "${document}" | awk '{print $1}')" == "${production_digest}" ]] || {
    print -u2 "Finder production workflow was modified by the release probe"
    exit 1
  }
  python -m skill_magnet uninstall-context-menu --platform macos --confirm
  [[ ! -e "${workflow}" ]] || { print -u2 "Finder workflow remains installed"; exit 1; }
done
if find "${HOME}/Library/Services" -maxdepth 1 -name '.skill-magnet-workflow-*' -print -quit |
    grep -q .; then
  print -u2 "Finder workflow transaction residue remains"
  exit 1
fi

print "finder-release-lifecycle-tests: OK"
