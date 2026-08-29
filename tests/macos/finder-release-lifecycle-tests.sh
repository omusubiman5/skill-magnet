#!/bin/zsh
set -euo pipefail

test_root="$(mktemp -d "${RUNNER_TEMP}/skill-magnet-finder.XXXXXX")"
original_home="${HOME}"
export HOME="${test_root}/home"
export SKILL_MAGNET_FINDER_E2E_PROBE="${test_root}/finder-probe.txt"
mkdir -p "${HOME}" "${test_root}/selected folder"

cleanup() {
  export HOME="${original_home}"
  if [[ -d "${test_root}" && "${test_root}" == "${RUNNER_TEMP}/skill-magnet-finder."* ]]; then
    rm -rf -- "${test_root}"
  fi
}
trap cleanup EXIT

python -m skill_magnet install-context-menu --platform macos --confirm
workflow="${HOME}/Library/Services/Skill Magnet.workflow"
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
actual_selected="$(<"${SKILL_MAGNET_FINDER_E2E_PROBE}")"
[[ "${actual_selected}" == "${test_root}/selected folder" ]] || {
  print -u2 "Finder selected path did not reach the adapter intact"
  exit 1
}

python -m skill_magnet uninstall-context-menu --platform macos --confirm
[[ ! -e "${workflow}" ]] || { print -u2 "Finder workflow remains installed"; exit 1; }
if find "${HOME}/Library/Services" -maxdepth 1 -name '.skill-magnet-workflow-*' -print -quit |
    grep -q .; then
  print -u2 "Finder workflow transaction residue remains"
  exit 1
fi

print "finder-release-lifecycle-tests: OK"
