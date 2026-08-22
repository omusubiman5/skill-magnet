from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class SkillMagnetError(RuntimeError):
    """A safe, user-facing failure."""


class SafetyError(SkillMagnetError):
    """A write was refused because managed-state guarantees did not hold."""


SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SECRET_FILE_NAMES = (
    re.compile(r"^\.env(?:\..+)?$", re.IGNORECASE),
    re.compile(r"^(?:id_rsa|id_ed25519|credentials\.json|auth\.json|\.npmrc|\.pypirc|\.netrc|secrets?\.(?:json|ya?ml|toml))$", re.IGNORECASE),
    re.compile(r".*\.(?:pem|key|p12|pfx)$", re.IGNORECASE),
)
SECRET_CONTENT = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        rb"(?im)^\s*[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY)[A-Z0-9_]*"
        rb"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"
    ),
)


def _expand_path(value: str, base: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute():
        expanded = base / expanded
    return expanded.resolve()


def _parse_github_repo(url: str) -> tuple[str, str]:
    value = url.strip().rstrip("/")
    patterns = (
        r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$",
        r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+)/([^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, value, re.IGNORECASE)
        if match:
            return match.group(1).lower(), match.group(2).lower()
    raise SkillMagnetError(f"GitHub repository URL is required: {url}")


def _run_git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise SkillMagnetError("git executable was not found") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "git command failed").strip()
        raise SkillMagnetError(detail) from exc
    return result.stdout.strip()


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def hash_directory(directory: Path) -> str:
    if not directory.is_dir():
        raise SkillMagnetError(f"Skill directory does not exist: {directory}")
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        if _is_link(path):
            raise SafetyError(f"Links are not allowed inside a managed skill: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def scan_skill_safety(directory: Path) -> None:
    """Reject filesystem indirection and high-confidence secret material."""
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        if _is_link(path):
            raise SafetyError(f"Links are not allowed inside a managed skill: {path}")
        if not path.is_file():
            continue
        if any(pattern.fullmatch(path.name) for pattern in SECRET_FILE_NAMES):
            raise SafetyError(f"Secret-like file is not allowed in a skill pack: {path}")
        with path.open("rb") as handle:
            content = handle.read()
        if any(pattern.search(content) for pattern in SECRET_CONTENT):
            raise SafetyError(f"Secret-like content is not allowed in a skill pack: {path}")


def _frontmatter(skill_file: Path) -> dict[str, str]:
    lines = skill_file.read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillMagnetError(f"SKILL.md is missing YAML frontmatter: {skill_file}")
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
        if match:
            values[match.group(1)] = match.group(2).strip("'\"")
    else:
        raise SkillMagnetError(f"SKILL.md frontmatter is not closed: {skill_file}")
    return values


@dataclass(frozen=True)
class Pack:
    pack_id: str
    repo_url: str
    expected_commit: str
    source: Path
    skills: tuple[str, ...]


@dataclass(frozen=True)
class PlanItem:
    target: str
    skill: str
    action: str
    destination: Path
    source_hash: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "target": self.target,
            "skill": self.skill,
            "action": self.action,
            "destination": str(self.destination),
            "source_hash": self.source_hash,
            "detail": self.detail,
        }


class Config:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path.resolve()
        self.base = self.path.parent
        if data.get("version") != 1:
            raise SkillMagnetError("Unsupported configuration version")
        owners = data.get("allowed_github_owners")
        if not isinstance(owners, list) or not owners:
            raise SkillMagnetError("allowed_github_owners must be a non-empty list")
        self.allowed_owners = {str(owner).lower() for owner in owners}
        targets = data.get("targets")
        if not isinstance(targets, dict) or set(targets) != {"codex", "claude"}:
            raise SkillMagnetError("targets must define exactly codex and claude")
        self.targets = {
            name: _expand_path(str(value), self.base) for name, value in targets.items()
        }
        self.state_dir = _expand_path(str(data.get("state_dir", ".skill-magnet")), self.base)
        self.packs: dict[str, Pack] = {}
        raw_packs = data.get("packs")
        if not isinstance(raw_packs, list) or not raw_packs:
            raise SkillMagnetError("packs must be a non-empty list")
        for raw in raw_packs:
            if not isinstance(raw, dict):
                raise SkillMagnetError("Each pack must be an object")
            pack_id = str(raw.get("id", ""))
            if not SKILL_NAME.fullmatch(pack_id) or pack_id in self.packs:
                raise SkillMagnetError(f"Invalid or duplicate pack id: {pack_id}")
            skills = tuple(str(item) for item in raw.get("skills", []))
            if not skills or len(skills) != len(set(skills)):
                raise SkillMagnetError(f"Pack {pack_id} must list unique skills")
            for skill in skills:
                if not SKILL_NAME.fullmatch(skill):
                    raise SkillMagnetError(f"Invalid skill name: {skill}")
            self.packs[pack_id] = Pack(
                pack_id=pack_id,
                repo_url=str(raw.get("repo_url", "")),
                expected_commit=str(raw.get("expected_commit", "")).lower(),
                source=_expand_path(str(raw.get("source", "")), self.base),
                skills=skills,
            )
            if not COMMIT_SHA.fullmatch(self.packs[pack_id].expected_commit):
                raise SkillMagnetError(f"Pack {pack_id} requires a full expected_commit SHA")

    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillMagnetError(f"Cannot read configuration: {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise SkillMagnetError("Configuration root must be an object")
        return cls(path, data)


class Engine:
    def __init__(self, config: Config, state_dir: Path | None = None) -> None:
        self.config = config
        self.state_dir = (state_dir or config.state_dir).resolve()
        self.state_file = self.state_dir / "state.json"
        self.pending_file = self.state_dir / "pending-transaction.json"

    def _pack(self, pack_id: str) -> Pack:
        try:
            return self.config.packs[pack_id]
        except KeyError as exc:
            raise SkillMagnetError(f"Unknown pack: {pack_id}") from exc

    def _validate_pack(self, pack: Pack) -> tuple[str, dict[str, str]]:
        expected_owner, expected_repo = _parse_github_repo(pack.repo_url)
        if expected_owner not in self.config.allowed_owners:
            raise SafetyError(
                f"Repository owner {expected_owner} is not in allowed_github_owners"
            )
        if not pack.source.is_dir():
            raise SkillMagnetError(f"Pack source does not exist: {pack.source}")
        root = Path(_run_git(pack.source, "rev-parse", "--show-toplevel")).resolve()
        if root != pack.source:
            raise SafetyError(f"Pack source must be the Git repository root: {pack.source}")
        actual_owner, actual_repo = _parse_github_repo(
            _run_git(pack.source, "remote", "get-url", "origin")
        )
        if (actual_owner, actual_repo) != (expected_owner, expected_repo):
            raise SafetyError(
                "Configured repository does not match the source origin: "
                f"expected {expected_owner}/{expected_repo}, got {actual_owner}/{actual_repo}"
            )
        commit = _run_git(pack.source, "rev-parse", "HEAD").lower()
        if commit != pack.expected_commit:
            raise SafetyError(
                f"Pack HEAD is not the pinned expected_commit: "
                f"expected {pack.expected_commit}, got {commit}"
            )
        dirty = _run_git(pack.source, "status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise SafetyError(f"Pack source has uncommitted changes: {pack.source}")
        hashes: dict[str, str] = {}
        for skill in pack.skills:
            skill_dir = pack.source / skill
            if not skill_dir.is_dir() or _is_link(skill_dir):
                raise SkillMagnetError(f"Invalid skill directory: {skill_dir}")
            metadata = _frontmatter(skill_dir / "SKILL.md")
            if metadata.get("name") != skill:
                raise SkillMagnetError(
                    f"Skill name does not match its directory: {skill_dir}"
                )
            if not metadata.get("description"):
                raise SkillMagnetError(f"Skill description is required: {skill_dir}")
            scan_skill_safety(skill_dir)
            hashes[skill] = hash_directory(skill_dir)
        return commit, hashes

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"version": 1, "installs": {}, "transactions": []}
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyError(f"Cannot read state file: {self.state_file}: {exc}") from exc
        if state.get("version") != 1:
            raise SafetyError("Unsupported state-file version")
        state.setdefault("installs", {})
        state.setdefault("transactions", [])
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        self._write_json_atomic(self.state_file, state)

    @staticmethod
    def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.stem + "-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _state_bytes(self) -> bytes | None:
        return self.state_file.read_bytes() if self.state_file.exists() else None

    def _restore_state_bytes(self, previous: bytes | None) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if previous is None:
            if self.state_file.exists():
                self.state_file.unlink()
            return
        fd, temporary = tempfile.mkstemp(prefix="state-restore-", suffix=".json", dir=self.state_dir)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(previous)
            os.replace(temporary, self.state_file)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _remove_internal_tree(path: Path) -> None:
        if not path.exists():
            return
        if _is_link(path) or not path.is_dir():
            raise SafetyError(f"Refusing to remove unexpected transaction path: {path}")
        shutil.rmtree(path)

    def _transaction_is_committed(self, pending: dict[str, Any]) -> bool:
        try:
            state = self._load_state()
        except SkillMagnetError:
            return False
        transaction = next(
            (
                item
                for item in state.get("transactions", [])
                if item.get("id") == pending.get("id")
            ),
            None,
        )
        if not transaction:
            return False
        if pending.get("mode") == "sync":
            return not transaction.get("rolled_back", False)
        return bool(transaction.get("rolled_back", False))

    def _recover_pending(self, *, force_revert: bool = False) -> None:
        if not self.pending_file.exists():
            return
        try:
            pending = json.loads(self.pending_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyError(f"Cannot recover pending transaction: {exc}") from exc
        committed = not force_revert and self._transaction_is_committed(pending)
        records = pending.get("records", [])
        if committed:
            for record in records:
                self._remove_internal_tree(Path(record["stage"]))
                self._remove_internal_tree(Path(record["backup"]))
            if pending.get("delete_snapshot_on_commit"):
                self._remove_internal_tree(Path(pending["snapshot_root"]))
            self.pending_file.unlink(missing_ok=True)
            return

        for record in reversed(records):
            destination = Path(record["destination"])
            backup = Path(record["backup"])
            stage = Path(record["stage"])
            if backup.exists():
                self._remove_internal_tree(destination)
                os.replace(backup, destination)
            elif not record.get("before_existed", False):
                self._remove_internal_tree(destination)
            self._remove_internal_tree(stage)
        encoded = pending.get("state_before")
        previous = base64.b64decode(encoded) if encoded is not None else None
        self._restore_state_bytes(previous)
        if pending.get("remove_snapshot_on_revert"):
            self._remove_internal_tree(Path(pending["snapshot_root"]))
        self.pending_file.unlink(missing_ok=True)

    def _target_names(self, targets: Iterable[str] | None) -> tuple[str, ...]:
        selected = tuple(targets or ("codex", "claude"))
        if not selected or len(selected) != len(set(selected)):
            raise SkillMagnetError("Targets must be unique")
        unknown = set(selected) - set(self.config.targets)
        if unknown:
            raise SkillMagnetError(f"Unknown target(s): {', '.join(sorted(unknown))}")
        return selected

    def plan(self, pack_id: str, targets: Iterable[str] | None = None) -> dict[str, Any]:
        pack = self._pack(pack_id)
        selected = self._target_names(targets)
        commit, source_hashes = self._validate_pack(pack)
        state = self._load_state()
        managed_pack = state["installs"].get(pack_id, {})
        managed_targets = managed_pack.get("targets", {})
        items: list[PlanItem] = []
        for target in selected:
            root = self.config.targets[target]
            if _is_link(root) or (root.exists() and not root.is_dir()):
                raise SafetyError(f"Target root must be a normal directory: {root}")
            managed_target = managed_targets.get(target, {})
            old_root = managed_target.get("root")
            if old_root and Path(old_root).resolve() != root:
                raise SafetyError(
                    f"Configured {target} root changed since the last sync: {old_root} -> {root}"
                )
            managed_skills = managed_target.get("skills", {})
            for skill in pack.skills:
                destination = root / skill
                source_hash = source_hashes[skill]
                prior = managed_skills.get(skill)
                if destination.exists() or _is_link(destination):
                    if not destination.is_dir() or _is_link(destination):
                        action, detail = "conflict", "destination is not a normal directory"
                    elif not prior:
                        action, detail = "conflict", "destination is not managed by Skill Magnet"
                    else:
                        current_hash = hash_directory(destination)
                        if current_hash != prior.get("hash"):
                            action, detail = "drift", "managed destination was modified locally"
                        elif current_hash == source_hash:
                            action, detail = "unchanged", "already current"
                        else:
                            action, detail = "update", "managed content differs from source"
                elif prior:
                    action, detail = "restore", "managed destination is missing"
                else:
                    action, detail = "create", "destination does not exist"
                items.append(
                    PlanItem(target, skill, action, destination, source_hash, detail)
                )
        return {
            "pack": pack_id,
            "source": str(pack.source),
            "source_commit": commit,
            "targets": list(selected),
            "items": [item.as_dict() for item in items],
        }

    def _activate_stage(self, record: dict[str, Any]) -> None:
        """Activate one prepared record while retaining its rollback backup."""
        destination = Path(record["destination"])
        backup = Path(record["backup"])
        stage = Path(record["stage"])
        if destination.exists():
            os.replace(destination, backup)
        if record.get("install", True):
            os.replace(stage, destination)

    def _prepare_sync_records(
        self,
        pack: Pack,
        changed: list[dict[str, Any]],
        token: str,
        snapshot_root: Path,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            for item in changed:
                destination = Path(item["destination"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                stage = destination.parent / f".skill-magnet-stage-{token}-{destination.name}"
                backup = destination.parent / f".skill-magnet-old-{token}-{destination.name}"
                if stage.exists() or backup.exists():
                    raise SafetyError(f"Temporary path already exists near {destination}")
                shutil.copytree(pack.source / item["skill"], stage)
                if hash_directory(stage) != item["source_hash"]:
                    raise SafetyError(f"Staged content hash mismatch: {stage}")
                existed = destination.exists()
                snapshot = snapshot_root / item["target"] / item["skill"]
                if existed:
                    snapshot.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(destination, snapshot)
                records.append(
                    {
                        "target": item["target"],
                        "skill": item["skill"],
                        "destination": str(destination),
                        "stage": str(stage),
                        "backup": str(backup),
                        "before_existed": existed,
                        "snapshot": str(snapshot) if existed else None,
                        "install": True,
                    }
                )
        except Exception:
            for record in records:
                self._remove_internal_tree(Path(record["stage"]))
            self._remove_internal_tree(snapshot_root)
            raise
        return records

    def sync(self, pack_id: str, targets: Iterable[str] | None = None) -> dict[str, Any]:
        self._recover_pending()
        pack = self._pack(pack_id)
        plan = self.plan(pack_id, targets)
        unsafe = [item for item in plan["items"] if item["action"] in {"conflict", "drift"}]
        if unsafe:
            first = unsafe[0]
            raise SafetyError(
                f"Sync refused: {first['target']}/{first['skill']}: {first['detail']}"
            )
        changed = [item for item in plan["items"] if item["action"] != "unchanged"]
        if not changed:
            return {**plan, "result": "unchanged", "transaction": None}

        state = self._load_state()
        previous_install = copy.deepcopy(state["installs"].get(pack_id))
        token = uuid.uuid4().hex
        state_before = self._state_bytes()
        snapshot_root = self.state_dir / "snapshots" / token
        records = self._prepare_sync_records(pack, changed, token, snapshot_root)
        pending = {
            "version": 1,
            "id": token,
            "mode": "sync",
            "pack": pack_id,
            "state_before": base64.b64encode(state_before).decode("ascii") if state_before is not None else None,
            "records": records,
            "snapshot_root": str(snapshot_root),
            "remove_snapshot_on_revert": True,
            "delete_snapshot_on_commit": False,
        }
        self._write_json_atomic(self.pending_file, pending)
        try:
            for record in records:
                self._activate_stage(record)
        except Exception:
            self._recover_pending(force_revert=True)
            raise

        new_install = copy.deepcopy(previous_install) if previous_install else {"targets": {}}
        new_install["source_commit"] = plan["source_commit"]
        new_install["repo_url"] = pack.repo_url
        for target in plan["targets"]:
            target_state = new_install["targets"].setdefault(
                target,
                {"root": str(self.config.targets[target]), "skills": {}},
            )
            target_state["root"] = str(self.config.targets[target])
            for skill in pack.skills:
                target_state["skills"][skill] = {
                    "hash": next(
                        item["source_hash"]
                        for item in plan["items"]
                        if item["target"] == target and item["skill"] == skill
                    )
                }
        state["installs"][pack_id] = new_install
        state["transactions"].append(
            {
                "id": token,
                "pack": pack_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "rolled_back": False,
                "previous_install": previous_install,
                "before": [
                    {
                        "target": record["target"],
                        "skill": record["skill"],
                        "destination": record["destination"],
                        "existed": record["before_existed"],
                        "snapshot": record["snapshot"],
                    }
                    for record in records
                ],
                "after": [
                    {
                        "target": item["target"],
                        "skill": item["skill"],
                        "destination": item["destination"],
                        "hash": item["source_hash"],
                    }
                    for item in changed
                ],
            }
        )
        try:
            self._write_state(state)
        except Exception:
            self._recover_pending(force_revert=True)
            raise
        self._recover_pending()
        return {**plan, "result": "synced", "transaction": token}

    def status(self, pack_id: str, targets: Iterable[str] | None = None) -> dict[str, Any]:
        return self.plan(pack_id, targets)

    def rollback(self, pack_id: str) -> dict[str, Any]:
        self._recover_pending()
        self._pack(pack_id)
        state = self._load_state()
        transaction = next(
            (
                item
                for item in reversed(state["transactions"])
                if item.get("pack") == pack_id and not item.get("rolled_back")
            ),
            None,
        )
        if not transaction:
            raise SkillMagnetError(f"No transaction is available to roll back for {pack_id}")
        for after in transaction["after"]:
            destination = Path(after["destination"])
            if not destination.is_dir() or hash_directory(destination) != after["hash"]:
                raise SafetyError(
                    f"Rollback refused because destination drifted: {destination}"
                )
        token = transaction["id"] + "-rollback"
        records: list[dict[str, Any]] = []
        try:
            for before in transaction["before"]:
                destination = Path(before["destination"])
                stage = destination.parent / f".skill-magnet-stage-{token}-{destination.name}"
                backup = destination.parent / f".skill-magnet-old-{token}-{destination.name}"
                if stage.exists() or backup.exists():
                    raise SafetyError(f"Temporary path already exists near {destination}")
                if before["existed"]:
                    snapshot = Path(before["snapshot"])
                    if not snapshot.is_dir() or _is_link(snapshot):
                        raise SafetyError(f"Rollback snapshot is missing or unsafe: {snapshot}")
                    shutil.copytree(snapshot, stage)
                records.append(
                    {
                        "target": before["target"],
                        "skill": before["skill"],
                        "destination": before["destination"],
                        "stage": str(stage),
                        "backup": str(backup),
                        "before_existed": True,
                        "install": bool(before["existed"]),
                    }
                )
        except Exception:
            for record in records:
                self._remove_internal_tree(Path(record["stage"]))
            raise
        state_before = self._state_bytes()
        snapshot_root = self.state_dir / "snapshots" / transaction["id"]
        pending = {
            "version": 1,
            "id": transaction["id"],
            "mode": "rollback",
            "pack": pack_id,
            "state_before": base64.b64encode(state_before).decode("ascii") if state_before is not None else None,
            "records": records,
            "snapshot_root": str(snapshot_root),
            "remove_snapshot_on_revert": False,
            "delete_snapshot_on_commit": True,
        }
        self._write_json_atomic(self.pending_file, pending)
        try:
            for record in records:
                self._activate_stage(record)
        except Exception:
            self._recover_pending(force_revert=True)
            raise
        previous = transaction.get("previous_install")
        if previous is None:
            state["installs"].pop(pack_id, None)
        else:
            state["installs"][pack_id] = previous
        transaction["rolled_back"] = True
        transaction["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        try:
            self._write_state(state)
        except Exception:
            self._recover_pending(force_revert=True)
            raise
        self._recover_pending()
        return {"pack": pack_id, "result": "rolled-back", "transaction": transaction["id"]}
