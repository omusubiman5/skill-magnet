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

1. An x64 native COM DLL implements `IExplorerCommand` and
   `IEnumExplorerCommand`.
2. A full MSIX package supplies package identity and contains/registers the DLL with
   `windows.comServer`.
3. `windows.fileExplorerContextMenus` binds the same command CLSID to both
   `Directory` and `Directory\Background`.
4. Windows 11's compact surface renders one direct item for each configured
   package. The current release has one item labeled `Delivery Assurance`.
   Package members and runtime choices are not emitted as Explorer leaves.
   Selecting the package opens the common confirmation UI, where the user
   chooses Codex or Claude and enters the actual request. The installer-generated
   v3 manifest carries the package ID, complete skill set, fixed commit, content
   digests, and immutable command argv. There is no classic fallback.
5. The package leaf invokes the windowless Python `context` command with one
   Windows argv vector containing project path, package, fixed commit,
   membership digest, instruction digest, and acceptance digest. Runtime and
   actual request enter only through the subsequent confirmation UI.

The previous classic `HKCU` menu is not a supported fallback. Its self-signed
process adapter can be rejected by Windows Smart App Control with error 4551,
even when the local certificate is trusted. Product CLI installation snapshots
Skill Magnet-owned roots and package state before mutation; a modern install
failure removes partial registration, restores the prior state, and reports an
error. A config or fixed-version change requires regenerating the modern menu.

## Build and registration boundary

The repository contains native source and deterministic build inputs. The
installed package contains the built DLL, an identity-only executable,
manifest/assets, and generated menu manifest. Installation is per-user. The
identity executable is never a process adapter. On `Invoke`, the
DLL starts the Authenticode-valid Python executable from the immutable argv
with `CREATE_NO_WINDOW`.

The CLI verifies identity/DLL/config/menu-manifest existence and matching
digests before registration. Status additionally verifies that the command
target exists, has a valid Authenticode signature, and is not the removed
`SkillMagnetLauncher.exe`. Registry/package existence alone is never reported
as menu-use success.

## Acceptance gates

- Focused tests cover hierarchy, both contexts, special-character paths, exact
  argv, cancellation, launch failure, cleanup, uninstall, and rollback.
- Existing regression tests remain green.
- Actual Explorer evidence proves modern direct visibility for folder bodies
  and backgrounds, and proves that no classic entry remains.
- Actual Explorer then proves the package confirmation UI and Codex Desktop
  handoff for the current fixed package. Handoff remains
  `desktop_handoff_ready`; it is not relabeled as `verified_applied` or task
  completion. Failure and cancellation retain no process/temp output, and
  lifecycle testing restores the initial registry/package state.
- `sm-62a.15` is performed by an independent auditor only after `sm-62a.6.7`
  passes.

## Primary references

- Microsoft Learn, *Add a File Explorer context menu command to a packaged
  desktop app*: native `IExplorerCommand`, `windows.comServer`,
  `windows.fileExplorerContextMenus`, sparse package identity, and both
  Directory item types.
- Microsoft ExplorerCommandVerb sample: native Shell command implementation
  pattern.
