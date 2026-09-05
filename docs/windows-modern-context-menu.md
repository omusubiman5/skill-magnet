# Windows 11 Modern Context Menu design

Beads: `sm-62a` / implementation chain `sm-62a.9` through `sm-62a.15`.

## Observed baseline

On 2026-08-23, actual Explorer checks showed the current `HKCU` classic
registration under both `Directory\shell\SkillMagnet` and
`Directory\Background\shell\SkillMagnet`. It appears only under **Show more
options**. The restored registry hashes match the pre-test baseline, but that
baseline is the older pack-only menu and is not evidence of a Windows 11 modern
menu registration. No competing `HKLM`, `ShellEx`, Command Store, or foreign
executable registration was found.

## Chosen Windows contract

The modern entry uses Microsoft's supported desktop-app integration:

1. An x64 native COM DLL implements one root `IExplorerCommand`; it does not
   publish pack, skill, runtime, or manager child commands to Explorer.
2. A full MSIX package supplies package identity and contains/registers the DLL with
   `windows.comServer`.
3. `windows.fileExplorerContextMenus` binds the same command CLSID to both
   `Directory` and `Directory\Background`.
4. Windows 11's compact surface renders exactly one direct root item named
   `Skill Magnet` for both a selected folder and a folder background. Selecting
   it passes that folder as the project and opens the common selector UI.
   The UI reads the current config at launch, then the user chooses the pack or
   skill, Codex or Claude, and enters the actual request. There is no separate
   Explorer item for Library Manager and no classic fallback.
5. The root invokes the windowless Python `context` command with an immutable
   argv vector containing the config path, selected project, platform, and
   launcher marker. Pack membership, fixed commit, content digests, runtime,
   and actual request are bound only after the user selects them in the UI.

The previous classic `HKCU` menu is not a supported fallback. Its self-signed
process adapter can be rejected by Windows Smart App Control with error 4551,
even when the local certificate is trusted. Product CLI installation snapshots
Skill Magnet-owned roots and package state before mutation; a modern install
failure removes partial registration, restores the prior state, and reports an
error. Windows classic rendering and registration APIs fail closed before they
emit registry content or run `reg add`. Only detection, backup, rollback, and
removal of previously installed classic roots remain for migration and recovery.
Pack, skill, commit, and config-content changes do not regenerate the
modern menu; only an executable/config-location, native contract, or package
integration change requires explicit reinstall or repair.

## Build and registration boundary

The repository contains native source and deterministic build inputs. The
installed package contains the built DLL, an identity-only executable,
manifest/assets, and generated menu manifest. Installation is per-user. The
identity executable is never a process adapter. On `Invoke`, the
DLL starts the Authenticode-valid Python executable from the immutable argv
with `CREATE_NO_WINDOW`.

The CLI verifies identity/DLL/config/menu-manifest existence and matching
digests before registration. The build emits `SkillMagnetNativeSource.json`
from the fixed native input set, embeds the same source-tree digest exactly once
in the DLL/export, and packages the manifest and DLL in the signed MSIX. Status
recomputes that source identity and rejects a manifest, artifact hash, or DLL
binding mismatch. Field evidence additionally requires byte equality across the
signed MSIX payload, registered package root, and external install root. Status
also verifies that the command
target exists, has a valid Authenticode signature, and is not the removed
`SkillMagnetLauncher.exe`. Registry/package existence alone is never reported
as menu-use success. Changing config contents, packs, or skills does not require
menu reinstallation because the single root reads the current config when it
opens. Reinstallation is required only when the installed executable or config
location, native adapter contract, or package integration changes.

## Acceptance gates

- Focused tests cover the single direct root, both contexts, selected-folder
  transfer, special-character paths, exact argv, cancellation, duplicate-launch
  control, launch failure, cleanup, uninstall, and rollback.
- Rollback validates the owned path, complete metadata schema, registry hashes,
  package identities, and external-file manifest before uninstalling or deleting
  current state; an invalid snapshot fails closed without destructive mutation.
- Release evidence rejects installed split generations by binding Appx, imported
  module, distribution ownership, runtime-tree/wheel/source digests, native
  source manifest, DLL binding, and signed-MSIX payload to one build.
- Existing regression tests remain green.
- Actual Explorer evidence proves modern direct visibility for folder bodies
  and backgrounds, and proves that no classic entry remains.
- Actual Explorer then proves the unified selector UI and Codex Desktop handoff
  for a pack selected from the current config. Handoff remains
  `desktop_handoff_ready`; it is not relabeled as `verified_applied` or task
  completion. Failure and cancellation retain no process/temp output, and
  lifecycle testing restores the initial registry/package state.
- `sm-62a.15` is performed by an independent auditor only after `sm-62a.6.7`
  passes.

Actual 0.5.9 Explorer acceptance and final counts remain `PENDING` until one
installed build satisfies every gate above.

## Primary references

- Microsoft Learn, *Add a File Explorer context menu command to a packaged
  desktop app*: native `IExplorerCommand`, `windows.comServer`,
  `windows.fileExplorerContextMenus`, sparse package identity, and both
  Directory item types.
- Microsoft ExplorerCommandVerb sample: native Shell command implementation
  pattern.
