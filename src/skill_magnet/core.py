from __future__ import annotations

import copy
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


_HTML_U0020_REFERENCE = re.compile(r"&#(?:0*32|[xX]0*20);")


def normalize_display_text(value: str) -> str:
    """Render only numeric U+0020 references as spaces at display boundaries.

    This intentionally is not a general HTML entity decoder. Values such as
    ``&lt;`` remain literal, and callers retain the original value for contracts,
    identifiers, hashes, and execution.
    """
    return _HTML_U0020_REFERENCE.sub(" ", value)


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


def _pack_source(value: str, base: Path) -> Path:
    if value.startswith("package://"):
        raise SkillMagnetError(
            "Packaged skill sources are prohibited; use the pinned GitHub repository"
        )
    return _expand_path(value, base)


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
    source: Path | None
    skills: tuple[str, ...]
    approved_by: str = ""
    approved_at: str = ""
    purpose: str = ""
    menu_label: str = ""
    selection_kind: str = "package"
    skill_metadata: dict[str, dict[str, str]] = field(default_factory=dict)

    def skill_display_name(self, skill_id: str) -> str:
        return normalize_display_text(
            self.skill_metadata.get(skill_id, {}).get("display_name", skill_id)
        )

    def skill_purpose(self, skill_id: str) -> str:
        return normalize_display_text(
            self.skill_metadata.get(skill_id, {}).get("purpose", self.purpose)
        )


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
        targets = data.get("targets", {})
        if not isinstance(targets, dict) or (
            targets and set(targets) != {"codex", "claude"}
        ):
            raise SkillMagnetError(
                "targets may be omitted; legacy mode must define exactly codex and claude"
            )
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
            raw_skill_metadata = raw.get("skill_metadata", {})
            if not isinstance(raw_skill_metadata, dict):
                raise SkillMagnetError(f"Pack {pack_id} skill_metadata must be an object")
            unknown_metadata = set(raw_skill_metadata) - set(skills)
            if unknown_metadata:
                raise SkillMagnetError(
                    f"Pack {pack_id} has metadata for unknown skills: "
                    + ", ".join(sorted(unknown_metadata))
                )
            skill_metadata: dict[str, dict[str, str]] = {}
            for skill, metadata in raw_skill_metadata.items():
                if not isinstance(metadata, dict):
                    raise SkillMagnetError(
                        f"Skill metadata must be an object: {pack_id}/{skill}"
                    )
                display_name = str(metadata.get("display_name", "")).strip()
                purpose = str(metadata.get("purpose", "")).strip()
                if not display_name or not purpose:
                    raise SkillMagnetError(
                        f"Skill metadata requires display_name and purpose: {pack_id}/{skill}"
                    )
                skill_metadata[skill] = {
                    "display_name": display_name,
                    "purpose": purpose,
                }
            source_value = raw.get("source")
            self.packs[pack_id] = Pack(
                pack_id=pack_id,
                repo_url=str(raw.get("repo_url", "")),
                expected_commit=str(raw.get("expected_commit", "")).lower(),
                source=(
                    _pack_source(str(source_value), self.base)
                    if source_value is not None
                    else None
                ),
                skills=skills,
                approved_by=str(raw.get("approved_by", "")).strip(),
                approved_at=str(raw.get("approved_at", "")).strip(),
                purpose=str(raw.get("purpose", "")).strip(),
                menu_label=str(raw.get("menu_label", pack_id)).strip(),
                selection_kind=str(raw.get("selection_kind", "package")).strip(),
                skill_metadata=skill_metadata,
            )
            if self.packs[pack_id].selection_kind not in {"package", "skill"}:
                raise SkillMagnetError(
                    f"Pack {pack_id} selection_kind must be package or skill"
                )
            if not self.packs[pack_id].menu_label:
                raise SkillMagnetError(f"Pack {pack_id} requires a menu_label")
            if not COMMIT_SHA.fullmatch(self.packs[pack_id].expected_commit):
                raise SkillMagnetError(f"Pack {pack_id} requires a full expected_commit SHA")

    @classmethod
    def load(cls, path: Path) -> "Config":
        if sys.version_info < (3, 12):
            raise SkillMagnetError(
                "Skill Magnet requires Python 3.12 or later for Windows junction detection"
            )
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
        self._remote_pack_files: dict[str, dict[str, bytes]] = {}

    @staticmethod
    def _github_archive_url(pack: Pack) -> str:
        owner, repository = _parse_github_repo(pack.repo_url)
        return (
            f"https://codeload.github.com/{owner}/{repository}/tar.gz/"
            f"{pack.expected_commit}"
        )

    def _load_remote_pack(self, pack: Pack) -> dict[str, bytes]:
        cached = self._remote_pack_files.get(pack.pack_id)
        if cached is not None:
            return cached
        archive_url = self._github_archive_url(pack)
        request = urllib.request.Request(
            archive_url,
            headers={"User-Agent": "Skill-Magnet/0.5"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.geturl() != archive_url:
                    raise SafetyError(
                        "Pinned GitHub skill archive redirected to an unexpected URL"
                    )
                archive = response.read(16 * 1024 * 1024 + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise SkillMagnetError(
                f"Cannot read pinned GitHub skill repository: {exc}"
            ) from exc
        if len(archive) > 16 * 1024 * 1024:
            raise SafetyError("Pinned GitHub skill archive exceeds 16 MiB")
        files: dict[str, bytes] = {}
        total = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
                members = bundle.getmembers()
                if len(members) > 5000:
                    raise SafetyError("Pinned GitHub skill archive has too many entries")
                for member in members:
                    parts = Path(member.name).parts
                    if len(parts) < 2:
                        continue
                    relative_parts = parts[1:]
                    if any(part in {"", ".", ".."} for part in relative_parts):
                        raise SafetyError("Unsafe path in pinned GitHub skill archive")
                    relative = "/".join(relative_parts)
                    if member.isdir():
                        continue
                    if not member.isfile() or member.issym() or member.islnk():
                        raise SafetyError("Links are not allowed in a GitHub skill pack")
                    if relative in files:
                        raise SafetyError("Duplicate path in pinned GitHub skill archive")
                    extracted = bundle.extractfile(member)
                    if extracted is None:
                        raise SafetyError("Cannot read pinned GitHub skill archive entry")
                    content = extracted.read(4 * 1024 * 1024 + 1)
                    if len(content) > 4 * 1024 * 1024:
                        raise SafetyError(f"GitHub skill file exceeds 4 MiB: {relative}")
                    total += len(content)
                    if total > 32 * 1024 * 1024:
                        raise SafetyError("Expanded GitHub skill pack exceeds 32 MiB")
                    files[relative] = content
        except (tarfile.TarError, OSError) as exc:
            raise SafetyError(f"Invalid pinned GitHub skill archive: {exc}") from exc
        self._remote_pack_files[pack.pack_id] = files
        return files

    def pack_bytes(self, pack: Pack, relative: str) -> bytes:
        if pack.source is not None:
            path = pack.source.joinpath(*Path(relative).parts)
            if not path.is_file() or _is_link(path):
                raise SafetyError(f"Cannot read approved skill artifact: {relative}")
            return path.read_bytes()
        try:
            return self._load_remote_pack(pack)[relative]
        except KeyError as exc:
            raise SafetyError(f"Cannot read approved skill artifact: {relative}") from exc

    def pack_text(self, pack: Pack, relative: str) -> str:
        try:
            return self.pack_bytes(pack, relative).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SafetyError(f"Approved skill artifact is not UTF-8: {relative}") from exc

    def _remote_skill_hash(self, pack: Pack, skill: str) -> str:
        files = self._load_remote_pack(pack)
        prefix = f"{skill}/"
        selected = sorted(
            (path[len(prefix):], content)
            for path, content in files.items()
            if path.startswith(prefix)
        )
        if not selected:
            raise SkillMagnetError(f"Invalid GitHub skill directory: {skill}")
        digest = hashlib.sha256()
        for relative, content in selected:
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
        return digest.hexdigest()

    def _validate_remote_pack(self, pack: Pack) -> tuple[str, dict[str, str]]:
        files = self._load_remote_pack(pack)
        hashes: dict[str, str] = {}
        for skill in pack.skills:
            skill_file = f"{skill}/SKILL.md"
            content = self.pack_text(pack, skill_file)
            lines = content.splitlines()
            if not lines or lines[0].strip() != "---":
                raise SkillMagnetError(f"SKILL.md is missing YAML frontmatter: {skill}")
            metadata: dict[str, str] = {}
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
                if match:
                    metadata[match.group(1)] = match.group(2).strip("'\"")
            else:
                raise SkillMagnetError(f"SKILL.md frontmatter is not closed: {skill}")
            if metadata.get("name") != skill or not metadata.get("description"):
                raise SafetyError(f"Invalid GitHub skill metadata: {skill}")
            prefix = f"{skill}/"
            for relative, value in files.items():
                if not relative.startswith(prefix):
                    continue
                name = relative.rsplit("/", 1)[-1]
                if any(pattern.fullmatch(name) for pattern in SECRET_FILE_NAMES):
                    raise SafetyError(
                        f"Secret-like file is not allowed in a skill pack: {relative}"
                    )
                if any(pattern.search(value) for pattern in SECRET_CONTENT):
                    raise SafetyError(
                        f"Secret-like content is not allowed in a skill pack: {relative}"
                    )
            hashes[skill] = self._remote_skill_hash(pack, skill)
        return pack.expected_commit, hashes

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
        if pack.source is None:
            return self._validate_remote_pack(pack)
        if not pack.source.is_dir():
            raise SkillMagnetError(f"Pack source does not exist: {pack.source}")
        snapshot_path = pack.source / ".skill-magnet-snapshot.json"
        if snapshot_path.is_file():
            return self._validate_bundled_pack(pack, snapshot_path)
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

    def _validate_bundled_pack(
        self, pack: Pack, snapshot_path: Path
    ) -> tuple[str, dict[str, str]]:
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyError(f"Cannot read bundled snapshot metadata: {exc}") from exc
        if snapshot.get("version") != 1:
            raise SafetyError("Unsupported bundled snapshot metadata")
        expected_repo = _parse_github_repo(pack.repo_url)
        actual_repo = _parse_github_repo(str(snapshot.get("repo_url", "")))
        if actual_repo != expected_repo:
            raise SafetyError("Bundled snapshot repository does not match configuration")
        commit = str(snapshot.get("commit", "")).lower()
        if commit != pack.expected_commit:
            raise SafetyError("Bundled snapshot commit does not match expected_commit")
        index = pack.source / "INDEX.md"
        if not index.is_file() or hashlib.sha256(index.read_bytes()).hexdigest() != str(
            snapshot.get("index_sha256", "")
        ):
            raise SafetyError("Bundled snapshot INDEX.md digest does not match")
        recorded = snapshot.get("skills")
        if not isinstance(recorded, dict) or set(recorded) != set(pack.skills):
            raise SafetyError("Bundled snapshot skill set does not match configuration")
        hashes: dict[str, str] = {}
        for skill in pack.skills:
            skill_dir = pack.source / skill
            if not skill_dir.is_dir() or _is_link(skill_dir):
                raise SafetyError(f"Invalid bundled skill directory: {skill_dir}")
            metadata = _frontmatter(skill_dir / "SKILL.md")
            if metadata.get("name") != skill or not metadata.get("description"):
                raise SafetyError(f"Invalid bundled skill metadata: {skill_dir}")
            scan_skill_safety(skill_dir)
            actual_hash = hash_directory(skill_dir)
            if actual_hash != recorded.get(skill):
                raise SafetyError(f"Bundled skill digest does not match: {skill}")
            hashes[skill] = actual_hash
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
            self._cleanup_empty_state_dirs(pending)
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
        for parent_text in {
            str(Path(record["destination"]).parent)
            for record in records
            if not record.get("parent_preexisting", True)
        }:
            try:
                Path(parent_text).rmdir()
            except OSError:
                pass
        encoded = pending.get("state_before")
        previous = base64.b64decode(encoded) if encoded is not None else None
        self._restore_state_bytes(previous)
        if pending.get("remove_snapshot_on_revert"):
            self._remove_internal_tree(Path(pending["snapshot_root"]))
        self.pending_file.unlink(missing_ok=True)
        self._cleanup_empty_state_dirs(pending)

    def _cleanup_empty_state_dirs(self, pending: dict[str, Any]) -> None:
        """Remove transaction-created empty state directories without touching user data."""
        snapshots = self.state_dir / "snapshots"
        try:
            snapshots.rmdir()
        except OSError:
            pass
        if not pending.get("state_dir_preexisting", True):
            try:
                self.state_dir.rmdir()
            except OSError:
                pass

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

    def _sync_record_specs(
        self,
        pack: Pack,
        changed: list[dict[str, Any]],
        token: str,
        snapshot_root: Path,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for item in changed:
            destination = Path(item["destination"])
            stage = destination.parent / f".skill-magnet-stage-{token}-{destination.name}"
            backup = destination.parent / f".skill-magnet-old-{token}-{destination.name}"
            if stage.exists() or backup.exists():
                raise SafetyError(f"Temporary path already exists near {destination}")
            existed = destination.exists()
            snapshot = snapshot_root / item["target"] / item["skill"]
            records.append(
                {
                    "target": item["target"],
                    "skill": item["skill"],
                    "source": str(pack.source / item["skill"]),
                    "expected_hash": item["source_hash"],
                    "destination": str(destination),
                    "stage": str(stage),
                    "backup": str(backup),
                    "before_existed": existed,
                    "parent_preexisting": destination.parent.exists(),
                    "snapshot": str(snapshot) if existed else None,
                    "install": True,
                }
            )
        return records

    def _prepare_sync_records(self, records: list[dict[str, Any]]) -> None:
        """Prepare every stage and snapshot after the recovery journal exists."""
        for record in records:
            destination = Path(record["destination"])
            stage = Path(record["stage"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(Path(record["source"]), stage)
            if hash_directory(stage) != record["expected_hash"]:
                raise SafetyError(f"Staged content hash mismatch: {stage}")
            if record["before_existed"]:
                snapshot = Path(record["snapshot"])
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(destination, snapshot)

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
        state_dir_preexisting = self.state_dir.exists()
        snapshot_root = self.state_dir / "snapshots" / token
        records = self._sync_record_specs(pack, changed, token, snapshot_root)
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
            "state_dir_preexisting": state_dir_preexisting,
        }
        try:
            self._write_json_atomic(self.pending_file, pending)
        except Exception:
            if self.pending_file.exists():
                self._recover_pending(force_revert=True)
            else:
                self._cleanup_empty_state_dirs(pending)
            raise
        try:
            self._prepare_sync_records(records)
            for record in records:
                self._activate_stage(record)
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
        for before in transaction["before"]:
            destination = Path(before["destination"])
            stage = destination.parent / f".skill-magnet-stage-{token}-{destination.name}"
            backup = destination.parent / f".skill-magnet-old-{token}-{destination.name}"
            if stage.exists() or backup.exists():
                raise SafetyError(f"Temporary path already exists near {destination}")
            snapshot = Path(before["snapshot"]) if before["existed"] else None
            if snapshot is not None and (not snapshot.is_dir() or _is_link(snapshot)):
                raise SafetyError(f"Rollback snapshot is missing or unsafe: {snapshot}")
            records.append(
                {
                    "target": before["target"],
                    "skill": before["skill"],
                    "destination": before["destination"],
                    "stage": str(stage),
                    "backup": str(backup),
                    "before_existed": True,
                    "snapshot": str(snapshot) if snapshot is not None else None,
                    "install": bool(before["existed"]),
                }
            )
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
            "state_dir_preexisting": True,
        }
        try:
            self._write_json_atomic(self.pending_file, pending)
        except Exception:
            if self.pending_file.exists():
                self._recover_pending(force_revert=True)
            raise
        try:
            for record in records:
                if record["install"]:
                    shutil.copytree(Path(record["snapshot"]), Path(record["stage"]))
            for record in records:
                self._activate_stage(record)
            previous = transaction.get("previous_install")
            if previous is None:
                state["installs"].pop(pack_id, None)
            else:
                state["installs"][pack_id] = previous
            transaction["rolled_back"] = True
            transaction["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
            self._write_state(state)
        except Exception:
            self._recover_pending(force_revert=True)
            raise
        self._recover_pending()
        return {"pack": pack_id, "result": "rolled-back", "transaction": transaction["id"]}
