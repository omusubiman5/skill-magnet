from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from .core import Config, SKILL_NAME, SkillMagnetError, _is_link


CATALOG_FILENAME = "skill-magnet.catalog.json"
CATALOG_VERSION = 1
TRANSACTION_VERSION = 1
DEFAULT_REPOSITORY_NAME = "skill-magnet-skills"
RELATION_TYPES = ("depends-on", "composes-with", "contrasts-with")
TERMINAL_STATES = {"active", "rolled_back", "abandoned", "no_changes"}
SECRET_RULES = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("generic-secret", re.compile(r"(?i)\b(?:api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}")),
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillMagnetError(f"Cannot read JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SkillMagnetError(f"JSON root must be an object: {path}")
    return value


def _safe_relative(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise SkillMagnetError(f"Unsafe relative path: {value}")
    return path


def _frontmatter(text: str, source: str) -> dict[str, str]:
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillMagnetError(f"SKILL.md is missing YAML frontmatter: {source}")
    result: dict[str, str] = {}
    index = 1
    while index < len(lines):
        line = lines[index]
        if line.strip() == "---":
            break
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
        if match:
            key, raw = match.groups()
            if raw in {"|", ">"}:
                block: list[str] = []
                index += 1
                while index < len(lines) and (
                    lines[index].startswith((" ", "\t")) or not lines[index].strip()
                ):
                    if lines[index].strip():
                        block.append(lines[index].strip())
                    index += 1
                result[key] = " ".join(block)
                continue
            result[key] = raw.strip("'\"")
        index += 1
    else:
        raise SkillMagnetError(f"SKILL.md frontmatter is not closed: {source}")
    return result


def _scan_secret(relative: str, data: bytes) -> None:
    text = data.decode("utf-8", errors="replace")
    for rule, pattern in SECRET_RULES:
        if pattern.search(text):
            raise SkillMagnetError(f"Secret candidate rejected: {relative} ({rule})")


def _repository_files(root: Path) -> dict[str, bytes]:
    root = root.resolve()
    if not root.is_dir():
        raise SkillMagnetError(f"Library directory does not exist: {root}")
    files: dict[str, bytes] = {}
    for candidate in sorted(root.rglob("*")):
        if ".git" in candidate.relative_to(root).parts:
            continue
        if candidate.is_symlink():
            raise SkillMagnetError(
                f"Symbolic links are not allowed: {candidate.relative_to(root)}"
            )
        if candidate.is_dir():
            continue
        relative = candidate.relative_to(root).as_posix()
        _safe_relative(relative)
        data = candidate.read_bytes()
        _scan_secret(relative, data)
        files[relative] = data
    return files


def _pack_map(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    packs = catalog.get("packs")
    if not isinstance(packs, list) or not packs:
        raise SkillMagnetError("Catalog packs must be a non-empty list")
    result: dict[str, dict[str, Any]] = {}
    for pack in packs:
        if not isinstance(pack, dict):
            raise SkillMagnetError("Each catalog pack must be an object")
        pack_id = str(pack.get("id", ""))
        if not SKILL_NAME.fullmatch(pack_id) or pack_id in result:
            raise SkillMagnetError(f"Invalid or duplicate pack id: {pack_id}")
        result[pack_id] = pack
    return result


def _catalog_skills(catalog: dict[str, Any]) -> tuple[str, ...]:
    ordered: list[str] = []
    for pack in _pack_map(catalog).values():
        skills = pack.get("skills")
        if not isinstance(skills, list) or not skills:
            raise SkillMagnetError(f"Pack {pack['id']} must list skills")
        if len(skills) != len(set(map(str, skills))):
            raise SkillMagnetError(f"Pack {pack['id']} has duplicate skills")
        for raw in skills:
            skill = str(raw)
            if not SKILL_NAME.fullmatch(skill):
                raise SkillMagnetError(f"Invalid skill id: {skill}")
            if skill not in ordered:
                ordered.append(skill)
    return tuple(ordered)


def _validate_relations(catalog: dict[str, Any], all_skills: set[str]) -> None:
    for pack in _pack_map(catalog).values():
        selected = set(map(str, pack["skills"]))
        relations = pack.get("relations", {})
        if not isinstance(relations, dict) or set(relations) - set(RELATION_TYPES):
            raise SkillMagnetError(f"Pack {pack['id']} has invalid relations")
        graph: dict[str, set[str]] = {skill: set() for skill in all_skills}
        for kind in RELATION_TYPES:
            entries = relations.get(kind, [])
            if not isinstance(entries, list):
                raise SkillMagnetError(f"Relation {kind} must be a list")
            for pair in entries:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise SkillMagnetError(f"Relation {kind} must contain [source, target]")
                left, right = map(str, pair)
                unknown = {left, right} - all_skills
                if unknown:
                    raise SkillMagnetError(
                        f"Relation {kind} references unknown skills: {', '.join(sorted(unknown))}"
                    )
                if left == right:
                    raise SkillMagnetError(f"Relation {kind} cannot be self-referential: {left}")
                if kind == "depends-on":
                    graph[left].add(right)
                    if left in selected and right not in selected:
                        raise SkillMagnetError(
                            f"Pack {pack['id']} omits dependency: {left} depends-on {right}"
                        )
        visiting: list[str] = []
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                cycle = visiting[visiting.index(node) :] + [node]
                raise SkillMagnetError("Dependency cycle: " + " -> ".join(cycle))
            if node in visited:
                return
            visiting.append(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.pop()
            visited.add(node)

        for skill in sorted(graph):
            visit(skill)


def render_index(catalog: dict[str, Any]) -> str:
    packs = _pack_map(catalog)
    if any(str(pack.get("source_index", "")).strip() for pack in packs.values()):
        lines = ["# Skill Library INDEX", ""]
        for pack in packs.values():
            lines.extend((f"## {pack.get('display_name', pack['id'])}", ""))
            entry_skill = str(pack.get("entry_skill", "")).strip()
            if entry_skill:
                lines.extend(
                    (
                        f"Pack entry skill: [`{entry_skill}`](./{entry_skill}/SKILL.md)",
                        "",
                    )
                )
            source_index = str(pack.get("source_index", "")).strip()
            if source_index:
                lines.extend((source_index, ""))
                continue
            lines.extend(("```mermaid", "flowchart TD"))
            for kind in RELATION_TYPES:
                connector = "-.->" if kind == "contrasts-with" else "-->"
                for left, right in pack.get("relations", {}).get(kind, []):
                    lines.append(
                        f'  {left}["{left}"] {connector}|{kind}| {right}["{right}"]'
                    )
            lines.extend(("```", ""))
        return "\n".join(lines)
    lines = ["# Skill Library INDEX", "", "```mermaid", "flowchart TD"]
    seen: set[tuple[str, str, str]] = set()
    for pack in _pack_map(catalog).values():
        relations = pack.get("relations", {})
        for kind in RELATION_TYPES:
            for left, right in relations.get(kind, []):
                edge = (str(left), kind, str(right))
                if edge in seen:
                    continue
                seen.add(edge)
                connector = "-.->" if kind == "contrasts-with" else "-->"
                lines.append(f'  {left}["{left}"] {connector}|{kind}| {right}["{right}"]')
    lines.extend(["```", ""])
    return "\n".join(lines)


def _source_skill_metadata(source: Path) -> tuple[str, str, str]:
    skill_file = source / "SKILL.md"
    if not skill_file.is_file() or _is_link(skill_file):
        raise SkillMagnetError(f"Skill folder is missing SKILL.md: {source}")
    try:
        text = skill_file.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SkillMagnetError(f"SKILL.md must be UTF-8: {source}") from exc
    metadata = _frontmatter(text, str(skill_file))
    skill_id = metadata.get("name", "").strip() or source.name
    if not SKILL_NAME.fullmatch(skill_id):
        raise SkillMagnetError(f"Invalid skill name: {skill_id}")
    heading = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    display_name = heading.group(1).strip() if heading else skill_id
    purpose = metadata.get("description", "").strip() or f"Imported skill: {display_name}"
    return skill_id, display_name, purpose


def _index_skill_ids(pack_source: Path, available: set[str]) -> tuple[str, ...]:
    index_path = pack_source / "INDEX.md"
    if not index_path.is_file():
        return tuple(sorted(available))
    text = index_path.read_text(encoding="utf-8-sig")
    linked = re.findall(r"\]\((?:\./)?([a-z0-9][a-z0-9-]*)/SKILL\.md\)", text)
    missing = set(linked) - available
    if missing:
        raise SkillMagnetError(
            f"INDEX.md references missing skill folders in {pack_source.name}: "
            + ", ".join(sorted(missing))
        )
    ordered = list(dict.fromkeys(linked))
    ordered.extend(sorted(available - set(ordered)))
    return tuple(ordered)


def _index_relations(text: str, skill_ids: set[str]) -> dict[str, list[list[str]]]:
    relations: dict[str, list[list[str]]] = {kind: [] for kind in RELATION_TYPES}
    aliases = {
        alias: label
        for alias, label in re.findall(
            r'([A-Za-z0-9_]+)\["?([a-z0-9][a-z0-9-]*)"?\]', text
        )
    }

    def resolve(label: str) -> str | None:
        if label in skill_ids:
            return label
        matches = [skill for skill in skill_ids if skill.endswith(f"-{label}")]
        return matches[0] if len(matches) == 1 else None

    for line in text.splitlines():
        relation_match = re.search(
            r'^\s*([A-Za-z0-9_]+)(?:\[[^\]]+\])?\s+'
            r'(?:-->|-\.->|==+>)\|"?(depends-on|composes-with|contrasts-with)"?\|\s+'
            r'([A-Za-z0-9_]+)(?:\[[^\]]+\])?',
            line,
        )
        if relation_match is None:
            continue
        left = resolve(aliases.get(relation_match.group(1), relation_match.group(1)))
        right = resolve(aliases.get(relation_match.group(3), relation_match.group(3)))
        if left is None or right is None:
            raise SkillMagnetError(
                "INDEX.md contains a relation whose skill cannot be resolved: "
                + line.strip()
            )
        pair = [left, right]
        bucket = relations[relation_match.group(2)]
        if pair not in bucket:
            bucket.append(pair)
    return relations


def discover_skill_sources(source: Path) -> tuple[dict[str, Any], ...]:
    """Discover one skill, one pack, or a directory containing multiple packs."""
    source = source.resolve()
    if not source.is_dir() or _is_link(source):
        raise SkillMagnetError(f"登録元のフォルダーがありません: {source}")

    def pack_candidate(path: Path) -> dict[str, Any] | None:
        child_sources = {
            child.name: child
            for child in sorted(path.iterdir(), key=lambda item: item.name)
            if child.is_dir() and not _is_link(child) and (child / "SKILL.md").is_file()
        }
        if not child_sources:
            return None
        pack_id = path.name
        if not SKILL_NAME.fullmatch(pack_id):
            raise SkillMagnetError(f"Invalid pack folder name: {pack_id}")
        skill_ids = list(_index_skill_ids(path, set(child_sources)))
        entry_skill = ""
        if (path / "SKILL.md").is_file():
            entry_skill, _, _ = _source_skill_metadata(path)
            if entry_skill in child_sources:
                raise SkillMagnetError(f"Duplicate root and child skill: {entry_skill}")
            child_sources[entry_skill] = path
            skill_ids.insert(0, entry_skill)
        index_text = (
            (path / "INDEX.md").read_text(encoding="utf-8-sig")
            if (path / "INDEX.md").is_file()
            else ""
        )
        heading = re.search(r"(?m)^#\s+(.+?)\s*$", index_text)
        display_name = (
            re.sub(r"\s+[—-]\s+Skill Index\s*$", "", heading.group(1)).strip()
            if heading
            else pack_id.replace("-", " ").title()
        )
        purpose = f"Imported skill pack: {display_name}"
        if entry_skill:
            _, _, purpose = _source_skill_metadata(path)
        return {
            "id": pack_id,
            "display_name": display_name,
            "purpose": purpose,
            "skills": tuple(skill_ids),
            "skill_sources": child_sources,
            "relations": _index_relations(index_text, set(skill_ids)),
            "source_index": index_text,
            "entry_skill": entry_skill,
        }

    packs = tuple(
        candidate
        for child in sorted(source.iterdir(), key=lambda item: item.name)
        if child.is_dir() and not _is_link(child)
        if (candidate := pack_candidate(child)) is not None
    )
    if packs:
        return packs
    direct_pack = pack_candidate(source)
    if direct_pack is not None:
        return (direct_pack,)
    if (source / "SKILL.md").is_file():
        skill_id, display_name, purpose = _source_skill_metadata(source)
        return (
            {
                "id": "custom-skills",
                "display_name": "Custom skills",
                "purpose": purpose,
                "skills": (skill_id,),
                "skill_sources": {skill_id: source},
                "relations": {kind: [] for kind in RELATION_TYPES},
                "source_index": "",
                "entry_skill": "",
            },
        )
    raise SkillMagnetError(
        "選択したフォルダー内にSKILL.mdを含むスキルまたはスキルパックがありません"
    )


def _generated_acceptance(source: Path) -> dict[str, Any]:
    value: dict[str, Any] = {
        "version": 1,
        "assertions": [{"path": "result.applied", "equals": True}],
        "generated_by": "Skill Magnet Library Manager",
    }
    prompts = source / "test-prompts.json"
    if prompts.is_file() and not _is_link(prompts):
        value["source_test_prompts_sha256"] = _sha256(prompts.read_bytes())
    return value


def import_skill_source(root: Path, source: Path) -> dict[str, Any]:
    """Atomically import a skill, a complete pack, or a pack collection."""
    root = root.resolve()
    discovered = discover_skill_sources(source)
    catalog_path = root / CATALOG_FILENAME
    catalog = _read_json(catalog_path)
    existing_packs = _pack_map(catalog) if catalog.get("packs") else {}
    existing_skills = set(_catalog_skills(catalog)) if existing_packs else set()
    incoming_pack_ids = [str(pack["id"]) for pack in discovered]
    if len(incoming_pack_ids) != len(set(incoming_pack_ids)):
        raise SkillMagnetError("Duplicate discovered pack ids")
    duplicate_packs = set(incoming_pack_ids) & set(existing_packs)
    if duplicate_packs and duplicate_packs != {"custom-skills"}:
        raise SkillMagnetError("Pack already exists: " + ", ".join(sorted(duplicate_packs)))
    incoming_skills = [skill for pack in discovered for skill in pack["skills"]]
    if len(incoming_skills) != len(set(incoming_skills)):
        raise SkillMagnetError("The selected packs contain duplicate skill names")
    duplicate_skills = set(incoming_skills) & existing_skills
    if duplicate_skills:
        raise SkillMagnetError("Skill already exists: " + ", ".join(sorted(duplicate_skills)))
    for incoming in discovered:
        incoming_set = set(map(str, incoming["skills"]))
        if incoming["id"] == "custom-skills":
            continue
        same_members = [
            pack_id
            for pack_id, pack in existing_packs.items()
            if pack_id != incoming["id"]
            and set(map(str, pack.get("skills", []))) == incoming_set
        ]
        if same_members:
            raise SkillMagnetError(
                "同じスキル構成のパックが登録済みです: "
                + ", ".join(sorted(same_members))
            )

    prepared: dict[str, tuple[Path, tuple[str, ...], bool]] = {}
    metadata_by_skill: dict[str, tuple[str, str]] = {}
    for pack in discovered:
        for skill_id in pack["skills"]:
            skill_source = pack["skill_sources"][skill_id]
            actual_id, display_name, purpose = _source_skill_metadata(skill_source)
            if actual_id != skill_id:
                raise SkillMagnetError(
                    f"SKILL.md name must equal directory id: {skill_id}"
                )
            sibling_ids: tuple[str, ...] = ()
            if skill_source == source or skill_source.name == pack["id"]:
                sibling_ids = tuple(set(pack["skills"]) - {skill_id})
            acceptance_path = skill_source / "acceptance.json"
            if acceptance_path.is_file() and not _is_link(acceptance_path):
                acceptance = _read_json(acceptance_path)
                generated = False
            else:
                acceptance = _generated_acceptance(skill_source)
                generated = True
            prepared[skill_id] = (skill_source, sibling_ids, generated)
            metadata_by_skill[skill_id] = (display_name, purpose)

    def mutation(candidate: Path) -> None:
        candidate_catalog_path = candidate / CATALOG_FILENAME
        candidate_catalog = _read_json(candidate_catalog_path)
        candidate_existing_packs = (
            _pack_map(candidate_catalog) if candidate_catalog.get("packs") else {}
        )
        packs = candidate_catalog.setdefault("packs", [])
        for pack in discovered:
            if (
                pack["id"] == "custom-skills"
                and pack["id"] in candidate_existing_packs
            ):
                target_pack = candidate_existing_packs[pack["id"]]
            else:
                target_pack = {
                    "id": pack["id"],
                    "display_name": pack["display_name"],
                    "purpose": pack["purpose"],
                    "skills": [],
                    "skill_metadata": {},
                    "relations": pack["relations"],
                    "source_index": pack["source_index"],
                    "entry_skill": pack["entry_skill"],
                }
                packs.append(target_pack)
            for skill_id in pack["skills"]:
                target = candidate / skill_id
                skill_source, sibling_ids, _ = prepared[skill_id]
                _write_source_skill(
                    target, skill_source, sibling_skill_ids=sibling_ids
                )
                target_pack.setdefault("skills", []).append(skill_id)
                display_name, purpose = metadata_by_skill[skill_id]
                target_pack.setdefault("skill_metadata", {})[skill_id] = {
                    "display_name": display_name,
                    "purpose": purpose,
                }
        _atomic_json(candidate_catalog_path, candidate_catalog)

    result = _mutate_library_candidate(root, mutation).as_dict()
    result.update(
        source_kind=("collection" if len(discovered) > 1 else "pack" if len(incoming_skills) > 1 else "skill"),
        imported_pack_ids=incoming_pack_ids,
        imported_skill_ids=incoming_skills,
        generated_acceptance_count=sum(1 for _, _, generated in prepared.values() if generated),
    )
    return result


def library_inventory(root: Path) -> dict[str, Any]:
    """Return user-facing pack/skill hierarchy without exposing catalog editing."""
    root = root.resolve()
    catalog = _read_json(root / CATALOG_FILENAME)
    if not catalog.get("packs"):
        repository = catalog.get("repository", {})
        return {
            "repository": str(root),
            "repository_name": str(repository.get("name", "")),
            "pack_count": 0,
            "skill_count": 0,
            "packs": [],
        }
    validation = validate_library(root)
    memberships: dict[str, list[str]] = {}
    for pack in catalog["packs"]:
        for skill_id in map(str, pack["skills"]):
            memberships.setdefault(skill_id, []).append(str(pack["id"]))
    packs: list[dict[str, Any]] = []
    for pack in catalog["packs"]:
        metadata = pack.get("skill_metadata", {})
        packs.append(
            {
                "id": str(pack["id"]),
                "display_name": str(pack.get("display_name", pack["id"])),
                "purpose": str(pack.get("purpose", "")),
                "skills": [
                    {
                        "id": skill_id,
                        "display_name": str(
                            metadata.get(skill_id, {}).get("display_name", skill_id)
                        ),
                        "purpose": str(metadata.get(skill_id, {}).get("purpose", "")),
                        "pack_ids": memberships[skill_id],
                    }
                    for skill_id in map(str, pack["skills"])
                ],
            }
        )
    return {
        "repository": str(root),
        "repository_name": validation.repository_name,
        "pack_count": len(packs),
        "skill_count": len(validation.skill_ids),
        "packs": packs,
    }


def _mutate_library_candidate(
    root: Path, mutation: Callable[[Path], None]
) -> ValidationResult:
    """Validate a complete isolated candidate, then atomically replace the library."""
    root = root.resolve()
    if not root.is_dir():
        raise SkillMagnetError(f"Library does not exist: {root}")
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{root.name}-crud-", dir=parent))
    candidate = staging_root / root.name
    backup = parent / f".{root.name}-backup-{uuid.uuid4().hex}"
    replaced = False
    try:
        shutil.copytree(root, candidate, ignore=shutil.ignore_patterns(".git"))
        mutation(candidate)
        catalog = _read_json(candidate / CATALOG_FILENAME)
        (candidate / "INDEX.md").write_text(
            render_index(catalog), encoding="utf-8", newline="\n"
        )
        validation = validate_library(candidate)
        os.replace(root, backup)
        replaced = True
        try:
            os.replace(candidate, root)
        except Exception:
            os.replace(backup, root)
            replaced = False
            raise
        shutil.rmtree(backup, ignore_errors=True)
        replaced = False
        return validation
    finally:
        if replaced and backup.exists() and not root.exists():
            os.replace(backup, root)
        shutil.rmtree(staging_root, ignore_errors=True)
        if backup.exists() and root.exists():
            shutil.rmtree(backup, ignore_errors=True)


def recover_interrupted_library(root: Path) -> dict[str, Any]:
    """Restore a valid backup left by an abrupt stop during directory replacement."""
    root = root.resolve()
    backups = sorted(
        root.parent.glob(f".{root.name}-backup-*"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if root.exists():
        return {"recovered": False, "repository": str(root)}
    valid_backups: list[Path] = []
    for backup in backups:
        try:
            validate_library(backup)
            valid_backups.append(backup)
        except (OSError, SkillMagnetError):
            continue
    if not valid_backups:
        return {"recovered": False, "repository": str(root)}
    selected = valid_backups[0]
    os.replace(selected, root)
    validate_library(root)
    return {
        "recovered": True,
        "repository": str(root),
        "backup": str(selected),
    }


def _write_source_skill(
    target: Path, source: Path, *, sibling_skill_ids: Iterable[str] = ()
) -> tuple[str, str, str, bool]:
    skill_id, display_name, purpose = _source_skill_metadata(source)
    acceptance_path = source / "acceptance.json"
    if acceptance_path.is_file() and not _is_link(acceptance_path):
        acceptance = _read_json(acceptance_path)
        generated = False
    else:
        acceptance = _generated_acceptance(source)
        generated = True
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    for candidate in sorted(source.rglob("*")):
        relative = candidate.relative_to(source)
        if candidate.is_symlink():
            raise SkillMagnetError(f"Symbolic links are not allowed: {candidate}")
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        if candidate.is_dir():
            continue
        if candidate.name == "acceptance.json" or candidate.suffix == ".pyc":
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate, destination)

    skill_bytes = (target / "SKILL.md").read_bytes()
    if sibling_skill_ids:
        text = skill_bytes.decode("utf-8-sig")
        for sibling in sibling_skill_ids:
            text = text.replace(
                f"]({sibling}/SKILL.md)", f"](../{sibling}/SKILL.md)"
            )
        skill_bytes = text.encode("utf-8")
    (target / "SKILL.md").write_bytes(skill_bytes)
    _atomic_json(target / "acceptance.json", acceptance)
    return skill_id, display_name, purpose, generated


def update_skill_source(root: Path, skill_id: str, source: Path) -> dict[str, Any]:
    """Replace one selected skill from a same-ID source folder."""
    source = source.resolve()
    discovered = discover_skill_sources(source)
    incoming = [skill for pack in discovered for skill in pack["skills"]]
    if len(discovered) != 1 or len(incoming) != 1 or not (source / "SKILL.md").is_file():
        raise SkillMagnetError("スキルの更新にはSKILL.mdを含む1つのスキルフォルダーを選択してください")
    actual_id, _, _ = _source_skill_metadata(source)
    if actual_id != skill_id:
        raise SkillMagnetError(
            f"更新対象のスキルIDは{skill_id}ですが、選択フォルダーは{actual_id}です"
        )

    generated = False

    def mutation(candidate: Path) -> None:
        nonlocal generated
        catalog = _read_json(candidate / CATALOG_FILENAME)
        if skill_id not in set(_catalog_skills(catalog)):
            raise SkillMagnetError(f"登録されていないスキルです: {skill_id}")
        _, display_name, purpose, generated = _write_source_skill(
            candidate / skill_id, source
        )
        for pack in catalog["packs"]:
            if skill_id in map(str, pack["skills"]):
                pack.setdefault("skill_metadata", {})[skill_id] = {
                    "display_name": display_name,
                    "purpose": purpose,
                }
                if str(pack.get("id")) == "custom-skills" and list(
                    map(str, pack["skills"])
                ) == [skill_id]:
                    pack["purpose"] = purpose
        _atomic_json(candidate / CATALOG_FILENAME, catalog)

    result = _mutate_library_candidate(root, mutation).as_dict()
    result.update(operation="update_skill", skill_id=skill_id, generated_acceptance=generated)
    return result


def update_pack_source(root: Path, pack_id: str, source: Path) -> dict[str, Any]:
    """Replace one selected pack while preserving skills shared by other packs."""
    source = source.resolve()
    discovered = discover_skill_sources(source)
    if len(discovered) != 1:
        raise SkillMagnetError("パックの更新には1つのスキルパックフォルダーを選択してください")
    incoming = discovered[0]
    if str(incoming["id"]) != pack_id:
        raise SkillMagnetError(
            f"更新対象のパックIDは{pack_id}ですが、選択フォルダーは{incoming['id']}です"
        )
    generated_count = 0

    def mutation(candidate: Path) -> None:
        nonlocal generated_count
        catalog = _read_json(candidate / CATALOG_FILENAME)
        packs = catalog["packs"]
        index = next((i for i, pack in enumerate(packs) if str(pack.get("id")) == pack_id), None)
        if index is None:
            raise SkillMagnetError(f"登録されていないパックです: {pack_id}")
        old_skills = set(map(str, packs[index]["skills"]))
        other_skills = {
            skill
            for i, pack in enumerate(packs)
            if i != index
            for skill in map(str, pack["skills"])
        }
        metadata: dict[str, dict[str, str]] = {}
        for incoming_skill in incoming["skills"]:
            source_folder = incoming["skill_sources"][incoming_skill]
            siblings = (
                set(map(str, incoming["skills"])) - {str(incoming_skill)}
                if source_folder == source
                else ()
            )
            actual_id, display_name, purpose, generated = _write_source_skill(
                candidate / incoming_skill,
                source_folder,
                sibling_skill_ids=siblings,
            )
            if actual_id != incoming_skill:
                raise SkillMagnetError(
                    f"SKILL.md name must equal directory id: {incoming_skill}"
                )
            generated_count += int(generated)
            metadata[incoming_skill] = {
                "display_name": display_name,
                "purpose": purpose,
            }
        for obsolete in old_skills - set(map(str, incoming["skills"])) - other_skills:
            shutil.rmtree(candidate / obsolete, ignore_errors=True)
        packs[index] = {
            "id": pack_id,
            "display_name": incoming["display_name"],
            "purpose": incoming["purpose"],
            "skills": list(incoming["skills"]),
            "skill_metadata": metadata,
            "relations": incoming["relations"],
            "source_index": incoming["source_index"],
            "entry_skill": incoming["entry_skill"],
        }
        _atomic_json(candidate / CATALOG_FILENAME, catalog)

    result = _mutate_library_candidate(root, mutation).as_dict()
    result.update(operation="update_pack", pack_id=pack_id, generated_acceptance_count=generated_count)
    return result


def delete_skill(root: Path, skill_id: str, *, confirmed: bool) -> dict[str, Any]:
    """Delete a global skill after dependency and non-empty-library checks."""
    if not confirmed:
        raise SkillMagnetError("スキル削除には確認が必要です")

    def mutation(candidate: Path) -> None:
        catalog = _read_json(candidate / CATALOG_FILENAME)
        if skill_id not in set(_catalog_skills(catalog)):
            raise SkillMagnetError(f"登録されていないスキルです: {skill_id}")
        dependents = sorted(
            {
                str(left)
                for pack in catalog["packs"]
                for left, right in pack.get("relations", {}).get("depends-on", [])
                if str(right) == skill_id and str(left) != skill_id
            }
        )
        if dependents:
            raise SkillMagnetError(
                f"{skill_id}を必要とするスキルがあるため削除できません: "
                + ", ".join(dependents)
            )
        next_packs = []
        for pack in catalog["packs"]:
            pack["skills"] = [value for value in pack["skills"] if str(value) != skill_id]
            pack.get("skill_metadata", {}).pop(skill_id, None)
            for kind in RELATION_TYPES:
                pack.setdefault("relations", {}).setdefault(kind, [])
                pack["relations"][kind] = [
                    pair for pair in pack["relations"][kind] if skill_id not in map(str, pair)
                ]
            if pack["skills"]:
                next_packs.append(pack)
        if not next_packs:
            raise SkillMagnetError("最後のスキルは削除できません。ライブラリには1つ以上必要です")
        catalog["packs"] = next_packs
        shutil.rmtree(candidate / skill_id)
        _atomic_json(candidate / CATALOG_FILENAME, catalog)

    result = _mutate_library_candidate(root, mutation).as_dict()
    result.update(operation="delete_skill", skill_id=skill_id)
    return result


def delete_pack(root: Path, pack_id: str, *, confirmed: bool) -> dict[str, Any]:
    """Delete one pack and only its now-orphaned skill directories."""
    if not confirmed:
        raise SkillMagnetError("パック削除には確認が必要です")

    def mutation(candidate: Path) -> None:
        catalog = _read_json(candidate / CATALOG_FILENAME)
        target = next((pack for pack in catalog["packs"] if str(pack.get("id")) == pack_id), None)
        if target is None:
            raise SkillMagnetError(f"登録されていないパックです: {pack_id}")
        remaining = [pack for pack in catalog["packs"] if str(pack.get("id")) != pack_id]
        if not remaining:
            raise SkillMagnetError("最後のパックは削除できません。ライブラリには1つ以上必要です")
        remaining_skills = {skill for pack in remaining for skill in map(str, pack["skills"])}
        removed = set(map(str, target["skills"])) - remaining_skills
        blockers = sorted(
            {
                str(left)
                for pack in remaining
                for left, right in pack.get("relations", {}).get("depends-on", [])
                if str(right) in removed
            }
        )
        if blockers:
            raise SkillMagnetError(
                "削除するパックのスキルを必要とするスキルがあります: "
                + ", ".join(blockers)
            )
        catalog["packs"] = remaining
        for skill_id in removed:
            shutil.rmtree(candidate / skill_id)
        _atomic_json(candidate / CATALOG_FILENAME, catalog)

    result = _mutate_library_candidate(root, mutation).as_dict()
    result.update(operation="delete_pack", pack_id=pack_id)
    return result


@dataclass(frozen=True)
class ValidationResult:
    repository_name: str
    pack_ids: tuple[str, ...]
    skill_ids: tuple[str, ...]
    manifest: dict[str, str]
    menu_shape: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": True,
            "repository_name": self.repository_name,
            "pack_ids": list(self.pack_ids),
            "skill_ids": list(self.skill_ids),
            "manifest": dict(self.manifest),
            "menu_shape": self.menu_shape,
        }


def validate_library(
    root: Path, *, allow_uncataloged_skills: bool = False
) -> ValidationResult:
    root = root.resolve()
    files = _repository_files(root)
    if CATALOG_FILENAME not in files:
        raise SkillMagnetError(f"Missing required catalog: {CATALOG_FILENAME}")
    try:
        catalog = json.loads(files[CATALOG_FILENAME].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillMagnetError(f"Invalid {CATALOG_FILENAME}: {exc}") from exc
    if not isinstance(catalog, dict) or catalog.get("schema_version") != CATALOG_VERSION:
        raise SkillMagnetError("Unsupported catalog schema_version")
    repository = catalog.get("repository")
    if not isinstance(repository, dict):
        raise SkillMagnetError("Catalog repository must be an object")
    repository_name = str(repository.get("name", "")).strip()
    if not repository_name:
        raise SkillMagnetError("Catalog repository.name is required")
    skills = _catalog_skills(catalog)
    all_skills = set(skills)
    _validate_relations(catalog, all_skills)
    seen_directories = {
        path.parts[0]
        for relative in files
        if len((path := PurePosixPath(relative)).parts) == 2
        and path.parts[1] in {"SKILL.md", "acceptance.json"}
    }
    extras = seen_directories - all_skills
    if extras and not allow_uncataloged_skills:
        raise SkillMagnetError("Uncataloged skill directories: " + ", ".join(sorted(extras)))
    for skill in skills:
        skill_path = f"{skill}/SKILL.md"
        acceptance_path = f"{skill}/acceptance.json"
        if skill_path not in files or acceptance_path not in files:
            raise SkillMagnetError(f"Skill {skill} requires SKILL.md and acceptance.json")
        try:
            skill_text = files[skill_path].decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SkillMagnetError(f"SKILL.md must be UTF-8: {skill}") from exc
        metadata = _frontmatter(skill_text, skill_path)
        if metadata.get("name") != skill:
            raise SkillMagnetError(f"SKILL.md name must equal directory id: {skill}")
        if not metadata.get("description", "").strip():
            raise SkillMagnetError(f"SKILL.mdのdescriptionがありません: {skill}")
        try:
            acceptance = json.loads(files[acceptance_path].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillMagnetError(f"Invalid acceptance.json for {skill}: {exc}") from exc
        if not isinstance(acceptance, dict) or acceptance.get("version") != 1:
            raise SkillMagnetError(f"Skill {skill} acceptance.json requires version 1")
        assertions = acceptance.get("assertions")
        if not isinstance(assertions, list) or not assertions:
            raise SkillMagnetError(f"Skill {skill} requires acceptance assertions")
        for assertion in assertions:
            if (
                not isinstance(assertion, dict)
                or not isinstance(assertion.get("path"), str)
                or "equals" not in assertion
                or not re.fullmatch(r"result\.[A-Za-z_][A-Za-z0-9_-]*", assertion["path"])
                or isinstance(assertion["equals"], (dict, list))
            ):
                raise SkillMagnetError(f"Skill {skill} has an invalid acceptance assertion")
    expected_index = render_index(catalog).encode("utf-8")
    if "INDEX.md" in files and files["INDEX.md"].replace(b"\r\n", b"\n") != expected_index:
        raise SkillMagnetError("INDEX.md does not match catalog relations")
    manifest_paths = {CATALOG_FILENAME}
    if "INDEX.md" in files:
        manifest_paths.add("INDEX.md")
    manifest_paths.update(
        relative
        for relative in files
        if PurePosixPath(relative).parts[0] in all_skills
    )
    manifest = {path: _sha256(files[path]) for path in sorted(manifest_paths)}
    menu_value = [
        {
            "id": pack_id,
            "label": str(pack.get("display_name", pack_id)),
            "skills": list(map(str, pack["skills"])),
        }
        for pack_id, pack in _pack_map(catalog).items()
    ]
    return ValidationResult(
        repository_name=repository_name,
        pack_ids=tuple(_pack_map(catalog)),
        skill_ids=skills,
        manifest=manifest,
        menu_shape=_sha256(_canonical(menu_value)),
    )


def initialize_library(root: Path, name: str = DEFAULT_REPOSITORY_NAME) -> dict[str, Any]:
    root = root.resolve()
    if root.exists() and any(root.iterdir()):
        raise SkillMagnetError("Library initialization requires a new or empty directory")
    root.mkdir(parents=True, exist_ok=True)
    catalog = {
        "schema_version": CATALOG_VERSION,
        "repository": {"name": name or DEFAULT_REPOSITORY_NAME},
        "packs": [],
    }
    _atomic_json(root / CATALOG_FILENAME, catalog)
    return {"repository": str(root), "name": catalog["repository"]["name"]}


def add_skill(
    root: Path,
    *,
    skill_id: str,
    display_name: str,
    purpose: str,
    pack_id: str,
    pack_display_name: str | None = None,
    skill_source: Path | None = None,
) -> dict[str, Any]:
    if not SKILL_NAME.fullmatch(skill_id) or not SKILL_NAME.fullmatch(pack_id):
        raise SkillMagnetError("Invalid skill or pack id")
    root = root.resolve()
    catalog_path = root / CATALOG_FILENAME
    catalog = _read_json(catalog_path)
    existing_skills = set(_catalog_skills(catalog)) if catalog.get("packs") else set()
    if skill_id in existing_skills or (root / skill_id).exists():
        raise SkillMagnetError(f"Skill already exists: {skill_id}")
    target = root / skill_id
    target.mkdir()
    previous_catalog = catalog_path.read_bytes()
    index_path = root / "INDEX.md"
    previous_index = index_path.read_bytes() if index_path.exists() else None
    try:
        if skill_source is not None:
            source = skill_source.resolve()
            source_files = _repository_files(source)
            for required in ("SKILL.md", "acceptance.json"):
                if required not in source_files:
                    raise SkillMagnetError(f"Imported skill is missing {required}")
                (target / required).write_bytes(source_files[required])
        else:
            (target / "SKILL.md").write_text(
                "---\n"
                f"name: {skill_id}\n"
                f"description: {purpose}\n"
                "---\n\n"
                f"# {display_name}\n\n"
                "## Trigger\n\nDescribe when this skill applies.\n\n"
                "## Boundary\n\nDescribe what this skill must not do.\n",
                encoding="utf-8",
                newline="\n",
            )
            _atomic_json(
                target / "acceptance.json",
                {
                    "version": 1,
                    "assertions": [{"path": "result.applied", "equals": True}],
                },
            )
        packs = catalog.setdefault("packs", [])
        pack = next((item for item in packs if item.get("id") == pack_id), None)
        if pack is None:
            pack = {
                "id": pack_id,
                "display_name": pack_display_name or pack_id,
                "purpose": purpose,
                "skills": [],
                "skill_metadata": {},
                "relations": {kind: [] for kind in RELATION_TYPES},
            }
            packs.append(pack)
        pack.setdefault("skills", []).append(skill_id)
        pack.setdefault("skill_metadata", {})[skill_id] = {
            "display_name": display_name,
            "purpose": purpose,
        }
        _atomic_json(catalog_path, catalog)
        index_path.write_text(render_index(catalog), encoding="utf-8", newline="\n")
        result = validate_library(root)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        catalog_path.write_bytes(previous_catalog)
        if previous_index is None:
            index_path.unlink(missing_ok=True)
        else:
            index_path.write_bytes(previous_index)
        raise
    return result.as_dict()


def _run(
    args: list[str], *, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise SkillMagnetError(f"Command failed ({args[0]}): {detail}")
    return result


def _tree_digest(root: Path) -> str:
    value = hashlib.sha256()
    for relative, data in _repository_files(root).items():
        value.update(relative.encode("utf-8"))
        value.update(b"\0")
        value.update(data)
        value.update(b"\0")
    return value.hexdigest()


def _copy_library(
    source: Path, destination: Path, managed_paths: Iterable[str]
) -> None:
    """Overlay managed library files without deleting unrelated repository content."""
    source_files = _repository_files(source)
    for relative in managed_paths:
        data = source_files[relative]
        target = destination.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _deleted_git_paths(changed: Iterable[str]) -> list[str]:
    """Return deleted paths from Git porcelain output, including staged deletions."""
    deleted: list[str] = []
    for line in changed:
        if len(line) >= 3 and "D" in line[:2]:
            deleted.append(line[3:].strip())
    return deleted


class LibraryTransaction:
    def __init__(
        self,
        state_dir: Path,
        transaction_id: str | None = None,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = _run,
    ) -> None:
        self.state_dir = state_dir.resolve() / "library-transactions"
        self.transaction_id = transaction_id or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", self.transaction_id):
            raise SkillMagnetError("Invalid transaction id")
        self.root = self.state_dir / self.transaction_id
        self.journal_path = self.root / "journal.json"
        self.receipt_path = self.root / "receipt.json"
        self.workspace = self.root / "workspace"
        self.verifier = self.root / "remote-verifier"
        self.run = run

    def _journal(self) -> dict[str, Any]:
        if not self.journal_path.exists():
            return {
                "schema_version": TRANSACTION_VERSION,
                "transaction_id": self.transaction_id,
                "status": "draft",
                "created_at": _utc_now(),
            }
        return _read_json(self.journal_path)

    def _write_journal(self, journal: dict[str, Any]) -> None:
        journal["updated_at"] = _utc_now()
        _atomic_json(self.journal_path, journal)

    def prepare(
        self,
        *,
        draft: Path,
        remote: str,
        branch: str | None = None,
    ) -> dict[str, Any]:
        journal = self._journal()
        if journal["status"] != "draft":
            return journal["preview"]
        if re.search(r"://[^/\s]+@", remote) or "?" in remote or "#" in remote:
            raise SkillMagnetError(
                "Remote URL must not contain credentials, query parameters or fragments"
            )
        draft = draft.resolve()
        before = _tree_digest(draft)
        validation = validate_library(draft)
        self.root.mkdir(parents=True, exist_ok=True)
        pending = self.cleanup()
        if pending:
            raise SkillMagnetError("一時ファイルを整理できません: " + ", ".join(pending))
        journal.update(
            status="preparing",
            draft=str(draft),
            remote=remote,
            requested_branch=branch,
            draft_digest=before,
        )
        self._write_journal(journal)
        try:
            self.run(["git", "clone", "--no-hardlinks", remote, str(self.workspace)])
            default_branch = self.run(
                ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                cwd=self.workspace,
                check=False,
            ).stdout.strip()
            default_branch = default_branch.removeprefix("origin/") or "main"
            branch_name = branch or f"codex/skill-library-{self.transaction_id[:12]}"
            if branch_name == default_branch:
                self.run(["git", "switch", default_branch], cwd=self.workspace)
            else:
                self.run(["git", "switch", "-c", branch_name], cwd=self.workspace)
            previous_managed: set[str] = set()
            if (self.workspace / CATALOG_FILENAME).is_file():
                previous_managed = set(
                    validate_library(
                        self.workspace, allow_uncataloged_skills=True
                    ).manifest
                )
            approved_deletions = previous_managed - set(validation.manifest)
            for relative in approved_deletions:
                target = self.workspace.joinpath(*PurePosixPath(relative).parts)
                if target.is_file() and not _is_link(target):
                    target.unlink()
            _copy_library(draft, self.workspace, validation.manifest)
            validate_library(self.workspace, allow_uncataloged_skills=True)
            if _tree_digest(draft) != before:
                raise SkillMagnetError("Draft checkout changed during isolated preparation")
            # Preview the bytes Git will actually publish. On Windows, checkout
            # line-ending conversion can make working-tree bytes differ from blob
            # bytes, so approval and remote verification must share the Git index.
            self.run(["git", "add", "--all"], cwd=self.workspace)
            staged_manifest: dict[str, str] = {}
            for relative in validation.manifest:
                blob = subprocess.run(
                    ["git", "show", f":{relative}"],
                    cwd=self.workspace,
                    capture_output=True,
                )
                if blob.returncode:
                    raise SkillMagnetError(f"Cannot read staged Git blob: {relative}")
                staged_manifest[relative] = _sha256(blob.stdout)
            changed = [
                line
                for line in self.run(
                    ["git", "status", "--short"], cwd=self.workspace
                ).stdout.splitlines()
                if line.strip()
            ]
            deleted = _deleted_git_paths(changed)
            unexpected_deletions = sorted(set(deleted) - approved_deletions)
            if unexpected_deletions:
                raise SkillMagnetError(
                    "安全のため、既存GitHubファイルを削除する公開は拒否しました: "
                    + ", ".join(unexpected_deletions)
                )
        except Exception as exc:
            journal.update(
                status="interrupted",
                resume_status="draft",
                failed_stage="prepare",
                last_error=str(exc),
            )
            self._write_journal(journal)
            raise
        preview = {
            "transaction_id": self.transaction_id,
            "remote": remote,
            "branch": branch_name,
            "default_branch": default_branch,
            "changed_files": changed,
            "deleted_managed_files": sorted(set(deleted) & approved_deletions),
            "pack_ids": list(validation.pack_ids),
            "skill_ids": list(validation.skill_ids),
            "manifest": staged_manifest,
            "menu_shape": validation.menu_shape,
            "requires_confirmation": bool(changed),
            "no_changes": not changed,
        }
        current_commit = ""
        if not changed:
            current_commit = self.run(
                ["git", "rev-parse", "HEAD"], cwd=self.workspace
            ).stdout.strip()
        journal.update(
            status="prepared" if changed else "verified",
            draft=str(draft),
            remote=remote,
            branch=branch_name,
            default_branch=default_branch,
            preview=preview,
            draft_digest=before,
        )
        if not changed:
            journal.update(
                commit=current_commit,
                remote_manifest=staged_manifest,
                verification="remote_unchanged_manifest_verified",
            )
        self._write_journal(journal)
        if not changed:
            pending = self.cleanup()
            if pending:
                journal["cleanup_pending"] = pending
                self._write_journal(journal)
        return preview

    def _remote_manifest(self, remote: str, commit: str) -> ValidationResult:
        # A verifier may remain locked briefly by Git or antivirus on Windows.
        # Never make progress depend on deleting that old checkout: every check
        # gets a fresh, transaction-owned directory and cleanup is best effort.
        self.root.mkdir(parents=True, exist_ok=True)
        self.verifier = self.root / f"remote-verifier-{uuid.uuid4().hex[:12]}"
        self.run(["git", "clone", "--no-checkout", "--no-hardlinks", remote, str(self.verifier)])
        self.run(["git", "checkout", "--detach", commit], cwd=self.verifier)
        validation = validate_library(self.verifier, allow_uncataloged_skills=True)
        remote_manifest: dict[str, str] = {}
        for relative in validation.manifest:
            blob = subprocess.run(
                ["git", "show", f"{commit}:{relative}"],
                cwd=self.verifier,
                capture_output=True,
            )
            if blob.returncode:
                raise SkillMagnetError(f"Cannot read remote Git blob: {relative}")
            remote_manifest[relative] = _sha256(blob.stdout)
        return ValidationResult(
            repository_name=validation.repository_name,
            pack_ids=validation.pack_ids,
            skill_ids=validation.skill_ids,
            manifest=remote_manifest,
            menu_shape=validation.menu_shape,
        )

    def publish(
        self,
        *,
        confirmed: bool,
        direct: bool = False,
        create_pr: bool = True,
    ) -> dict[str, Any]:
        if not confirmed:
            raise SkillMagnetError("Publish requires explicit confirmation")
        if not create_pr and not direct:
            raise SkillMagnetError("Skipping a pull request requires explicit direct publish")
        journal = self._journal()
        if journal["status"] == "no_changes":
            return journal
        if journal["status"] in {"published_pending", "verified", "active"}:
            return journal
        if journal["status"] != "prepared":
            raise SkillMagnetError("Transaction must be prepared before publish")
        if create_pr and not direct and not re.match(
            r"https://github\.com/[^/]+/[^/]+(?:\.git)?$", str(journal["remote"])
        ):
            raise SkillMagnetError("Pull request publishing requires a GitHub repository URL")
        if direct and journal["branch"] != journal["default_branch"]:
            raise SkillMagnetError(
                "Direct publish requires explicitly preparing the default branch"
            )
        self.run(["git", "add", "--all"], cwd=self.workspace)
        staged = self.run(["git", "diff", "--cached", "--quiet"], cwd=self.workspace, check=False)
        if staged.returncode not in {0, 1}:
            raise SkillMagnetError("Cannot inspect staged library changes")
        if staged.returncode == 1:
            self.run(
                [
                    "git",
                    "-c",
                    "user.name=Skill Magnet",
                    "-c",
                    "user.email=skill-magnet@localhost",
                    "commit",
                    "-m",
                    f"Update skill library ({self.transaction_id})",
                ],
                cwd=self.workspace,
            )
        commit = self.run(["git", "rev-parse", "HEAD"], cwd=self.workspace).stdout.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise SkillMagnetError("Git did not produce a full commit SHA")
        journal.update(status="publishing", commit=commit, publish_started_at=_utc_now())
        self._write_journal(journal)
        remote_ref = self.run(
            ["git", "ls-remote", "--heads", "origin", journal["branch"]],
            cwd=self.workspace,
        ).stdout.strip()
        if not remote_ref.startswith(commit):
            self.run(
                ["git", "push", "origin", f"HEAD:refs/heads/{journal['branch']}"],
                cwd=self.workspace,
            )
        remote_validation = self._remote_manifest(journal["remote"], commit)
        if remote_validation.manifest != journal["preview"]["manifest"]:
            raise SkillMagnetError("Remote bytes do not match the approved preview manifest")
        pr_url = ""
        status = "verified"
        if create_pr and not direct:
            repository = journal["remote"].removesuffix(".git")
            existing = self.run(
                [
                    "gh", "pr", "list", "--repo", repository,
                    "--head", journal["branch"], "--state", "all",
                    "--limit", "1", "--json", "url",
                ],
                cwd=self.workspace,
                check=False,
            )
            existing_prs = json.loads(existing.stdout) if existing.returncode == 0 else []
            if existing_prs:
                pr_url = str(existing_prs[0]["url"])
            else:
                result = self.run(
                    [
                        "gh",
                        "pr",
                        "create",
                        "--repo",
                        repository,
                        "--head",
                        journal["branch"],
                        "--base",
                        journal["default_branch"],
                        "--title",
                        "Update Skill Magnet library",
                        "--body",
                        f"Transaction `{self.transaction_id}`. Remote digest verification passed.",
                    ],
                    cwd=self.workspace,
                )
                pr_url = result.stdout.strip()
            status = "published_pending"
        journal.update(
            status=status,
            commit=commit,
            remote_manifest=remote_validation.manifest,
            remote_menu_shape=remote_validation.menu_shape,
            pr_url=pr_url,
            published_at=_utc_now(),
        )
        self._write_journal(journal)
        return journal

    def mark_merged(self) -> dict[str, Any]:
        journal = self._journal()
        if journal["status"] == "verified":
            return journal
        if journal["status"] != "published_pending":
            raise SkillMagnetError("Only a published-pending transaction can be verified")
        commit = str(journal["commit"])
        if journal.get("pr_url"):
            command_cwd = self.workspace if self.workspace.is_dir() else self.root
            result = self.run(
                ["gh", "pr", "view", journal["pr_url"], "--json", "state,mergeCommit"],
                cwd=command_cwd,
            )
            pr = json.loads(result.stdout)
            pr_state = str(pr.get("state", "")).upper()
            if pr_state == "OPEN":
                journal.update(
                    wait_state="waiting_for_merge",
                    pr_state="OPEN",
                    last_merge_check_at=_utc_now(),
                )
                self._write_journal(journal)
                return journal
            if pr_state == "CLOSED":
                journal.update(
                    wait_state="closed_unmerged",
                    pr_state="CLOSED",
                    last_merge_check_at=_utc_now(),
                )
                self._write_journal(journal)
                return journal
            if pr_state != "MERGED":
                raise SkillMagnetError(f"GitHub returned an unknown pull request state: {pr_state or 'empty'}")
            merge_commit = (pr.get("mergeCommit") or {}).get("oid")
            if not isinstance(merge_commit, str) or not re.fullmatch(
                r"[0-9a-fA-F]{40}", merge_commit
            ):
                raise SkillMagnetError("GitHub reported a merged pull request without a valid merge commit")
            commit = merge_commit.lower()
        remote_validation = self._remote_manifest(journal["remote"], commit)
        if remote_validation.manifest != journal["preview"]["manifest"]:
            raise SkillMagnetError("Merged remote bytes do not match the approved preview")
        journal.update(
            status="verified",
            commit=commit,
            remote_manifest=remote_validation.manifest,
            remote_menu_shape=remote_validation.menu_shape,
            verified_at=_utc_now(),
        )
        journal.pop("wait_state", None)
        journal.pop("pr_state", None)
        self._write_journal(journal)
        return journal

    def merge_pull_request(self, *, confirmed: bool) -> dict[str, Any]:
        """Request an automatic GitHub merge and verify the resulting commit.

        A completed merge request is persisted before verification.  Re-entry
        checks the existing PR instead of issuing the merge command again.
        """
        if not confirmed:
            raise SkillMagnetError("Pull request merge requires explicit confirmation")
        journal = self._journal()
        if journal["status"] in {"verified", "active"}:
            return journal
        if journal["status"] != "published_pending":
            raise SkillMagnetError("Only a published-pending transaction can be merged")
        pr_url = str(journal.get("pr_url", ""))
        if not pr_url:
            raise SkillMagnetError("Published transaction has no pull request URL")
        if not journal.get("merge_requested_at"):
            command_cwd = self.workspace if self.workspace.is_dir() else self.root
            try:
                self.run(
                    [
                        "gh",
                        "pr",
                        "merge",
                        pr_url,
                        "--merge",
                        "--auto",
                        "--delete-branch",
                    ],
                    cwd=command_cwd,
                )
            except Exception as exc:
                # GitHub repositories may deliberately leave the repository-wide
                # auto-merge feature disabled.  That is not a failed library
                # transaction: an otherwise mergeable PR can still be merged
                # immediately by the same explicitly confirmed operation.
                if "Auto merge is not allowed for this repository" not in str(exc):
                    journal.update(
                        failed_stage="merge",
                        last_error=str(exc),
                        last_strategy="request_github_auto_merge",
                    )
                    self._write_journal(journal)
                    raise
                self.run(
                    [
                        "gh",
                        "pr",
                        "merge",
                        pr_url,
                        "--merge",
                        "--delete-branch",
                    ],
                    cwd=command_cwd,
                )
                journal["merge_strategy"] = "github_immediate_merge_fallback"
            else:
                journal["merge_strategy"] = "github_auto_merge"
            journal.update(
                merge_requested_at=_utc_now(),
            )
            journal.pop("failed_stage", None)
            journal.pop("last_error", None)
            journal.pop("last_strategy", None)
            self._write_journal(journal)
        return self.mark_merged()

    def complete_automatically(
        self,
        *,
        draft: Path,
        remote: str,
        config_path: Path,
        confirmed: bool,
        menu_update: Callable[[Path], Any] | None = None,
    ) -> dict[str, Any]:
        """Resume and run prepare, PR publish, merge, verify and activation.

        The user's register/update/delete action is the confirmation source.
        Every external transition remains journaled and can be re-entered
        without creating a duplicate commit, branch, or pull request.
        """
        if not confirmed:
            raise SkillMagnetError("Automatic library synchronization requires confirmation")
        journal = self._journal()
        status = str(journal.get("status", "draft"))
        if status in {"preparing", "interrupted", "publishing"}:
            journal = self.recover()
            status = str(journal.get("status", "draft"))
        if status == "draft":
            self.prepare(draft=draft, remote=remote)
            journal = self._journal()
            status = str(journal["status"])
        if status == "prepared":
            self.publish(confirmed=True)
            journal = self._journal()
            status = str(journal["status"])
        if status == "published_pending":
            merged = self.merge_pull_request(confirmed=True)
            if str(merged.get("status")) == "published_pending":
                return merged
            journal = merged
            status = str(journal["status"])
        if status == "verified":
            return self.activate(
                config_path=config_path,
                confirmed=True,
                menu_update=menu_update,
            )
        if status == "active":
            return _read_json(self.receipt_path)
        raise SkillMagnetError(f"Automatic synchronization cannot continue from: {status}")

    @staticmethod
    def _config_pack(pack: dict[str, Any], remote: str, commit: str) -> dict[str, Any]:
        skills = list(map(str, pack["skills"]))
        metadata = pack.get("skill_metadata", {})
        # A loose collection created by repeated single-skill registrations is
        # not one executable pack.  Expose every member as its own menu action.
        selection_kind = "skill" if str(pack["id"]) == "custom-skills" else "package"
        return {
            "id": str(pack["id"]),
            "menu_label": str(pack.get("display_name", pack["id"])),
            "selection_kind": selection_kind,
            "repo_url": remote,
            "expected_commit": commit,
            "purpose": str(pack.get("purpose", "Skill library pack")),
            "approved_by": "Skill Library Manager",
            "approved_at": _utc_now(),
            "skill_metadata": {
                skill: {
                    "display_name": str(metadata.get(skill, {}).get("display_name", skill)),
                    "purpose": str(metadata.get(skill, {}).get("purpose", pack.get("purpose", "Skill library skill"))),
                }
                for skill in skills
            },
            "skills": skills,
        }

    def activate(
        self,
        *,
        config_path: Path,
        confirmed: bool,
        menu_update: Callable[[Path], Any] | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            raise SkillMagnetError("Activation requires explicit confirmation")
        journal = self._journal()
        if journal["status"] == "active":
            return _read_json(self.receipt_path)
        if journal["status"] != "verified":
            raise SkillMagnetError("Only a remotely verified commit can be activated")
        remote_validation = self._remote_manifest(journal["remote"], journal["commit"])
        if remote_validation.manifest != journal["remote_manifest"]:
            raise SkillMagnetError("Remote verification drifted before activation")
        catalog = _read_json(self.verifier / CATALOG_FILENAME)
        config_path = config_path.resolve()
        previous = config_path.read_bytes()
        config = _read_json(config_path)
        previous_packs = list(config.get("packs", []))
        managed_ids = set(_pack_map(catalog))
        managed_remote = str(journal["remote"])
        replaced_previous = [
            pack
            for pack in previous_packs
            if str(pack.get("id")) in managed_ids
            or str(pack.get("repo_url", "")) == managed_remote
        ]
        retained = [pack for pack in previous_packs if pack not in replaced_previous]
        generated = [
            self._config_pack(pack, journal["remote"], journal["commit"])
            for pack in _pack_map(catalog).values()
        ]
        candidate = {**config, "packs": retained + generated}
        def config_menu_shape(packs: list[dict[str, Any]]) -> str:
            return _sha256(
                _canonical(
                    [
                        {
                            "id": pack.get("id"),
                            "label": pack.get("menu_label"),
                            "selection_kind": pack.get("selection_kind", "package"),
                            "purpose": pack.get("purpose", ""),
                            "skills": pack.get("skills", []),
                            "skill_metadata": pack.get("skill_metadata", {}),
                        }
                        for pack in packs
                    ]
                )
            )

        previous_shape = config_menu_shape(replaced_previous)
        generated_shape = config_menu_shape(generated)
        def deployment_shape(packs: list[dict[str, Any]]) -> str:
            return _sha256(
                _canonical(
                    [
                        {
                            "id": pack.get("id"),
                            "repo_url": pack.get("repo_url"),
                            "expected_commit": pack.get("expected_commit"),
                        }
                        for pack in packs
                    ]
                )
            )

        previous_deployment = deployment_shape(replaced_previous)
        generated_deployment = deployment_shape(generated)
        temporary = config_path.with_name(f".{config_path.name}.{self.transaction_id}.candidate")
        _atomic_json(temporary, candidate)
        try:
            Config.load(temporary)
            os.replace(temporary, config_path)
            menu_changed = (
                previous_shape != generated_shape
                or previous_deployment != generated_deployment
            )
            menu_result: Any = {"updated": False, "reason": "menu_shape_unchanged"}
            if menu_changed and menu_update is not None:
                menu_result = menu_update(config_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            config_path.write_bytes(previous)
            journal.update(status="verified", activation_error="rolled_back")
            self._write_journal(journal)
            try:
                self.cleanup()
            except OSError:
                pass
            raise
        receipt = {
            "schema_version": 1,
            "transaction_id": self.transaction_id,
            "status": "active",
            "repository": journal["remote"],
            "commit": journal["commit"],
            "changed_files": journal["preview"]["changed_files"],
            "manifest": journal["remote_manifest"],
            "pack_ids": journal["preview"]["pack_ids"],
            "skill_ids": journal["preview"]["skill_ids"],
            "config": str(config_path),
            "config_sha256": _sha256(config_path.read_bytes()),
            "menu": menu_result,
            "menu_changed": menu_changed,
            "test_result": "remote_manifest_verified",
            "completed_at": _utc_now(),
        }
        _atomic_json(self.receipt_path, receipt)
        journal.update(status="active", activated_at=_utc_now(), receipt=str(self.receipt_path))
        self._write_journal(journal)
        cleanup_pending = self.cleanup()
        if cleanup_pending:
            receipt["cleanup_pending"] = cleanup_pending
            _atomic_json(self.receipt_path, receipt)
            journal["cleanup_pending"] = cleanup_pending
            self._write_journal(journal)
        return receipt

    def status(self, config_path: Path | None = None) -> dict[str, Any]:
        journal = self._journal()
        status = str(journal["status"])
        active_commit = ""
        if config_path is not None and config_path.exists():
            config = _read_json(config_path)
            managed = set(journal.get("preview", {}).get("pack_ids", []))
            commits = {
                str(pack.get("expected_commit", ""))
                for pack in config.get("packs", [])
                if str(pack.get("id")) in managed
            }
            if len(commits) == 1:
                active_commit = commits.pop()
        published_commit = str(journal.get("commit", ""))
        remote_head = published_commit
        if journal.get("remote") and journal.get("branch"):
            remote = self.run(
                ["git", "ls-remote", "--heads", str(journal["remote"]), str(journal["branch"])],
                check=False,
            )
            if remote.returncode == 0 and remote.stdout.strip():
                remote_head = remote.stdout.split()[0].lower()
        if status == "verified" and active_commit != published_commit:
            display = "published_but_inactive"
        elif status == "published_pending":
            display = "published_pending"
        elif status == "prepared":
            display = "unpublished_edit"
        elif status in {"preparing", "interrupted", "publishing"}:
            display = "interrupted"
        else:
            display = status
        pack_ids = list(journal.get("preview", {}).get("pack_ids", []))
        skill_ids = list(journal.get("preview", {}).get("skill_ids", []))
        shared_platform_contract = {
            "commit": active_commit,
            "pack_ids": pack_ids,
            "skill_ids": skill_ids,
        }
        return {
            "transaction_id": self.transaction_id,
            "status": display,
            "resume_stage": (
                "publish" if status == "publishing" else "prepare"
                if status in {"preparing", "interrupted"} else ""
            ),
            "updated_at": str(journal.get("updated_at", journal.get("created_at", ""))),
            "remote_head": remote_head,
            "verified_commit": published_commit if status in {"verified", "active"} else "",
            "active_commit": active_commit,
            "pack_ids": pack_ids,
            "skill_ids": skill_ids,
            "platforms": {
                "windows": dict(shared_platform_contract),
                "macos": dict(shared_platform_contract),
            },
            "platform_parity": True,
            "receipt": str(self.receipt_path) if self.receipt_path.exists() else "",
        }

    def cleanup(self, *, include_workspace: bool = True) -> list[str]:
        """Remove disposable checkouts without turning completed work into failure."""
        pending: list[str] = []
        candidates = [*sorted(self.root.glob("remote-verifier*"))]
        if include_workspace:
            candidates.insert(0, self.workspace)
        root = self.root.resolve()

        def make_writable(function: Any, path: str, _: BaseException) -> None:
            os.chmod(path, stat.S_IWRITE)
            function(path)

        for path in dict.fromkeys(candidates):
            resolved = path.resolve()
            if resolved.parent != root:
                pending.append(str(path))
                continue
            for attempt in range(3):
                if not path.exists():
                    break
                try:
                    shutil.rmtree(path, onexc=make_writable)
                except OSError:
                    if attempt == 2:
                        pending.append(str(path))
                    else:
                        time.sleep(0.1 * (attempt + 1))
        return pending

    def recover(self) -> dict[str, Any]:
        """Recover an interrupted local transaction while preserving remote state."""
        journal = self._journal()
        status = str(journal.get("status", "draft"))
        pending = self.cleanup(include_workspace=status != "publishing")
        rebuilt = False
        if status in {"preparing", "interrupted"}:
            status = str(journal.get("resume_status", "draft"))
            journal["status"] = status
            self._write_journal(journal)
        if status == "publishing":
            if self.workspace.is_dir():
                journal["status"] = "prepared"
                self._write_journal(journal)
            else:
                remote_ref = self.run(
                    ["git", "ls-remote", "--heads", str(journal["remote"]), str(journal["branch"])],
                    check=False,
                ).stdout.strip()
                if remote_ref.startswith(str(journal.get("commit", ""))):
                    self.run(["git", "clone", "--no-hardlinks", str(journal["remote"]), str(self.workspace)])
                    self.run(
                        ["git", "switch", "-C", str(journal["branch"]), str(journal["commit"])],
                        cwd=self.workspace,
                    )
                    journal["status"] = "prepared"
                    self._write_journal(journal)
                else:
                    journal["status"] = "draft"
                    self._write_journal(journal)
                    status = "draft"
            journal = self._journal()
            status = str(journal["status"])
        if status in {"draft", "prepared"} and (
            status == "draft" or not self.workspace.is_dir()
        ):
            draft = Path(str(journal.get("draft", "")))
            if not draft.is_dir():
                raise SkillMagnetError(
                    "元のライブラリが見つからないため再開できません。"
                    "この作業を破棄して、登録からやり直してください"
                )
            if status != "draft":
                journal["status"] = "draft"
                self._write_journal(journal)
            self.prepare(
                draft=draft,
                remote=str(journal["remote"]),
                branch=journal.get("branch") or journal.get("requested_branch"),
            )
            journal = self._journal()
            rebuilt = True
        journal["last_recovery_at"] = _utc_now()
        journal["cleanup_pending"] = pending
        self._write_journal(journal)
        return {
            "transaction_id": self.transaction_id,
            "status": journal["status"],
            "workspace_rebuilt": rebuilt,
            "cleanup_pending": pending,
        }

    def abandon(self, *, confirmed: bool) -> dict[str, Any]:
        """Abandon only local work; never remove a remote branch or pull request."""
        if not confirmed:
            raise SkillMagnetError("作業の破棄には確認が必要です")
        journal = self._journal()
        previous = str(journal.get("status", "draft"))
        if journal.get("commit") or journal.get("pr_url") or previous in {
            "publishing",
            "published_pending",
            "verified",
            "active",
        }:
            raise SkillMagnetError(
                "GitHubへ送信済み、または送信済みの可能性があるため、"
                "ローカル作業だけを破棄できません。既存の作業を再開してください"
            )
        pending = self.cleanup()
        journal.update(
            status="abandoned",
            abandoned_from=previous,
            abandoned_at=_utc_now(),
            cleanup_pending=pending,
        )
        self._write_journal(journal)
        return {
            "transaction_id": self.transaction_id,
            "status": "abandoned",
            "remote_changes_preserved": bool(journal.get("commit") or journal.get("pr_url")),
            "cleanup_pending": pending,
        }


def list_transactions(state_dir: Path, config_path: Path | None = None) -> dict[str, Any]:
    root = state_dir.resolve() / "library-transactions"
    transactions: list[dict[str, Any]] = []
    if root.is_dir():
        for journal in sorted(root.glob("*/journal.json")):
            transaction = LibraryTransaction(state_dir, journal.parent.name)
            transactions.append(transaction.status(config_path))
    return {"transactions": transactions}


def find_resumable_transaction(
    state_dir: Path, *, draft: Path, remote: str
) -> LibraryTransaction | None:
    """Find the newest non-terminal transaction for exactly this library and remote."""
    root = state_dir.resolve() / "library-transactions"
    if not root.is_dir():
        return None
    wanted_draft = os.path.normcase(str(draft.resolve()))
    wanted_remote = remote.strip().removesuffix("/")
    matches: list[tuple[str, LibraryTransaction]] = []
    for path in root.glob("*/journal.json"):
        try:
            journal = _read_json(path)
        except SkillMagnetError:
            continue
        if str(journal.get("status")) in TERMINAL_STATES:
            continue
        saved_draft = str(journal.get("draft", ""))
        if not saved_draft:
            continue
        if os.path.normcase(str(Path(saved_draft).resolve())) != wanted_draft:
            continue
        if str(journal.get("remote", "")).strip().removesuffix("/") != wanted_remote:
            continue
        transaction = LibraryTransaction(state_dir, path.parent.name)
        matches.append((str(journal.get("updated_at", journal.get("created_at", ""))), transaction))
    return max(matches, key=lambda item: item[0])[1] if matches else None
