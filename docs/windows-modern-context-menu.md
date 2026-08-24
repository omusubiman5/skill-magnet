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
2. A sparse package supplies package identity and registers the DLL with
   `windows.comServer`.
3. `windows.fileExplorerContextMenus` binds the same command CLSID to both
   `Directory` and `Directory\Background`.
4. Windows 11's compact surface reliably renders one dynamic flyout level. A
   configured `selection_kind=package` is therefore one direct item labeled
   `Package: <menu_label>`; opening it enumerates only that package's immediate
   `Skill: <id> | Codex / Claude` leaves. Package members are never flattened
   into separate direct context-menu items. A configured standalone
   `selection_kind=skill` uses the corresponding direct `Skill: <menu_label>`
   item and the same explicit runtime leaves. The installer-generated v2
   manifest carries `pack_id`, `menu_label`, `selection_kind`, `skill_id`,
   runtime, and immutable command argv. The classic fallback retains the
   nested `Skill Magnet -> Pack -> individual skill -> Codex / Claude` layout.
   Menu construction performs no Git, network, Python, or activation work.
5. A runtime leaf invokes the existing windowless Python `context` command with
   one Windows argv vector containing project path, pack, skill, runtime, fixed
   commit, membership digest, instruction digest, and acceptance digest.

The existing classic `HKCU` menu remains a fallback. Modern installation does
not rewrite another owner's registry keys. Product CLI installation snapshots
Skill Magnet-owned classic roots and package state before mutation; failure,
uninstall, and rollback restore that starting state. A config or fixed-version
change requires reinstalling both generated menu representations.

## Build and registration boundary

The repository contains native source and deterministic build inputs. The
installed external location contains only the built DLL, sparse-package
manifest/assets, generated menu manifest, and launcher metadata required by the
extension. Installation is per-user. The extension is Explorer-compatible x64
and performs longer validation/launch work only after `Invoke`.

The CLI verifies executable/DLL/config/menu-manifest existence and matching
digests before registration. Status distinguishes classic fallback presence,
modern package registration, and actual Explorer acceptance; registry/package
existence alone is never reported as menu-use success.

## Acceptance gates

- Focused tests cover hierarchy, both contexts, special-character paths, exact
  argv, cancellation, launch failure, cleanup, uninstall, and rollback.
- Existing regression tests remain green.
- Actual Explorer evidence separately proves modern direct visibility and
  classic fallback visibility for folder bodies and backgrounds.
- Actual Explorer then proves one Codex `verified_applied`, one failure with no
  retained process/temp output, cancellation with no side effects, and complete
  restoration of the initial registry/package state.
- `sm-62a.15` is performed by an independent auditor only after `sm-62a.6.7`
  passes.

## Primary references

- Microsoft Learn, *Add a File Explorer context menu command to a packaged
  desktop app*: native `IExplorerCommand`, `windows.comServer`,
  `windows.fileExplorerContextMenus`, sparse package identity, and both
  Directory item types.
- Microsoft ExplorerCommandVerb sample: native Shell command implementation
  pattern.
