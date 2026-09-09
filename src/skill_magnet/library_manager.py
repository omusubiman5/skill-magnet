from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from .activation import reserved_skill_content_roots
from .core import Config, SKILL_NAME, SkillMagnetError, _is_link, _parse_github_repo
from .library_state import LibraryState, TERMINAL_STATES, TRANSACTION_STATES


CATALOG_FILENAME = "skill-magnet.catalog.json"
LOCAL_MUTATION_FILENAME = ".skill-magnet.mutation.json"
CATALOG_VERSION = 1
LOCAL_MUTATION_VERSION = 1
TRANSACTION_VERSION = 1
DEFAULT_REPOSITORY_NAME = "skill-magnet-skills"
RELATION_TYPES = ("depends-on", "composes-with", "contrasts-with")
SUPPORT_DIRECTORY_NAMES = {
    "_stage4",
    "agents",
    "assets",
    "audit",
    "candidates",
    "examples",
    "references",
    "rejected",
    "scripts",
    "templates",
    "tests",
}
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


def _selected_directory(source: Path, *, label: str = "登録元") -> Path:
    """Resolve a selected directory only after rejecting a link at its entry.

    ``Path.resolve()`` follows the selected leaf. Checking ``_is_link`` after
    that loses the evidence that the user selected a symlink or Windows
    junction and can therefore move enumeration outside the approved tree.
    """
    lexical = Path(os.path.abspath(os.fspath(source)))
    if os.path.lexists(lexical) and _is_link(lexical):
        raise SkillMagnetError(
            f"{label}にシンボリックリンクまたはジャンクションは使えません: {lexical}"
        )
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise SkillMagnetError(f"{label}のフォルダーがありません: {lexical}") from exc
    if not resolved.is_dir():
        raise SkillMagnetError(f"{label}のフォルダーがありません: {lexical}")
    return resolved


def _raise_if_cancelled(cancel_event: Any | None, operation: str) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SkillMagnetError(
            f"終了操作を受け付けたため、{operation}を保存前に中止しました。"
            "元のライブラリは変更していません"
        )


def canonical_remote_identity(remote: str) -> str:
    """Return one stable identity for equivalent remote spellings.

    GitHub HTTPS owner/repository names are case-insensitive. A trailing
    ``.git`` and slash likewise do not identify another repository. Local
    paths remain supported by the transaction engine's isolated direct mode
    and are normalized to one absolute filesystem identity.
    """
    raw = remote.strip()
    if not raw:
        raise SkillMagnetError("GitHub repository URL is required")
    if re.search(r"://[^/\s]+@", raw) or "?" in raw or "#" in raw:
        raise SkillMagnetError(
            "Remote URL must not contain credentials, query parameters or fragments"
        )
    github = re.fullmatch(
        r"https://github\.com/([^/\s]+)/([^/\s]+)/?", raw, flags=re.IGNORECASE
    )
    if github:
        owner = github.group(1).lower()
        repository = github.group(2)
        if repository.lower().endswith(".git"):
            repository = repository[:-4]
        if not repository:
            raise SkillMagnetError("GitHub repository URL is required")
        return f"https://github.com/{owner}/{repository.lower()}.git"
    if "://" in raw or raw.startswith("git@"):
        return raw.rstrip("/")
    return os.path.normcase(str(Path(raw).expanduser().resolve()))


def _draft_identity(draft: Path) -> str:
    lexical = Path(os.path.abspath(os.fspath(draft)))
    if os.path.lexists(lexical) and _is_link(lexical):
        raise SkillMagnetError(
            "保存済みライブラリがシンボリックリンクまたはジャンクションへ"
            f"置換されています: {lexical}。リンクを外して元のフォルダーを戻すか、"
            "このGitHub反映処理を復旧してから再実行してください"
        )
    return os.path.normcase(str(lexical.resolve()))


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


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(value)
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

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise SkillMagnetError(f"Cannot enumerate library directory: {directory}: {exc}") from exc
        for entry in entries:
            candidate = Path(entry.path)
            relative_path = candidate.relative_to(root)
            if ".git" in relative_path.parts:
                continue
            # pathlib.is_symlink() alone does not cover Windows directory
            # junctions.  Reject every reparse/link before deciding whether to
            # recurse, otherwise a nested junction can escape the selected tree.
            if _is_link(candidate):
                raise SkillMagnetError(
                    f"Filesystem links/junctions are not allowed: {relative_path}"
                )
            try:
                if entry.is_dir(follow_symlinks=False):
                    visit(candidate)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise SkillMagnetError(
                        f"Unsupported filesystem entry: {relative_path}"
                    )
            except OSError as exc:
                raise SkillMagnetError(f"Cannot inspect filesystem entry: {candidate}: {exc}") from exc
            relative = relative_path.as_posix()
            _safe_relative(relative)
            data = candidate.read_bytes()
            _scan_secret(relative, data)
            files[relative] = data

    visit(root)
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


def discover_skill_sources(
    source: Path, *, cancel_event: Any | None = None
) -> tuple[dict[str, Any], ...]:
    """Discover and account for one skill, packs, or a mixed collection.

    Every immediate directory is classified as an accepted skill/pack,
    recognized support material, or an ambiguity.  Ambiguities fail with their
    concrete relative paths; silently dropping a sibling is never acceptable.
    """
    _raise_if_cancelled(cancel_event, "登録元の検査")
    source = _selected_directory(source)

    def relative(path: Path) -> str:
        value = path.relative_to(source).as_posix()
        return value or "."

    def immediate_directories(path: Path) -> list[Path]:
        result: list[Path] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            _raise_if_cancelled(cancel_event, "登録元の検査")
            if _is_link(child):
                raise SkillMagnetError(
                    f"Filesystem links/junctions are not allowed: {relative(child)}"
                )
            if child.is_dir():
                result.append(child)
        return result

    def child_skill_directories(path: Path) -> list[Path]:
        return [
            child
            for child in immediate_directories(path)
            if (child / "SKILL.md").is_file() and not _is_link(child / "SKILL.md")
        ]

    def nested_skill_directories(path: Path) -> list[Path]:
        nested: list[Path] = []
        for candidate in sorted(path.rglob("SKILL.md")):
            _raise_if_cancelled(cancel_event, "登録元の検査")
            if _is_link(candidate) or any(_is_link(parent) for parent in candidate.parents if parent != source.parent):
                raise SkillMagnetError(
                    f"Filesystem links/junctions are not allowed: {relative(candidate)}"
                )
            if candidate.parent != path:
                nested.append(candidate.parent)
        return nested

    accepted: list[str] = []
    support: list[str] = [
        relative(child)
        for child in sorted(source.iterdir(), key=lambda item: item.name)
        if child.is_file() and child.name not in {"SKILL.md", "INDEX.md"}
    ]
    ambiguous: list[str] = []

    def pack_candidate(path: Path) -> dict[str, Any] | None:
        _raise_if_cancelled(cancel_event, "スキルパックの検査")
        children = immediate_directories(path)
        child_skills = child_skill_directories(path)
        child_sources = {child.name: child for child in child_skills}
        if not child_sources:
            return None
        for child in child_skills:
            _raise_if_cancelled(cancel_event, "スキルパックの検査")
            deeper = nested_skill_directories(child)
            if deeper:
                ambiguous.extend(relative(item) for item in deeper)
        root_entry = (path / "SKILL.md").is_file()
        for child in children:
            _raise_if_cancelled(cancel_event, "スキルパックの検査")
            if child in child_skills:
                continue
            descendants = nested_skill_directories(child)
            if descendants:
                ambiguous.extend(relative(item) for item in descendants)
            elif root_entry or child.name in SUPPORT_DIRECTORY_NAMES or child.name.startswith((".", "_")):
                support.append(relative(child))
            else:
                ambiguous.append(relative(child))
        if ambiguous:
            raise SkillMagnetError(
                "分類できないフォルダーがあります。各スキルにはSKILL.mdを置くか、"
                "明示的なsupportフォルダーへ移動してください: "
                + ", ".join(sorted(set(ambiguous)))
            )
        pack_id = path.name
        if not SKILL_NAME.fullmatch(pack_id):
            raise SkillMagnetError(f"Invalid pack folder name: {pack_id}")
        skill_ids = list(_index_skill_ids(path, set(child_sources)))
        entry_skill = ""
        if root_entry:
            entry_skill, _, _ = _source_skill_metadata(path)
            if entry_skill in child_sources:
                raise SkillMagnetError(f"Duplicate root and child skill: {entry_skill}")
            child_sources[entry_skill] = path
            skill_ids.insert(0, entry_skill)
            accepted.append(relative(path))
        accepted.extend(relative(child) for child in child_skills)
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
            "candidate_accounting": {},
        }

    children = immediate_directories(source)
    pack_children = [child for child in children if child_skill_directories(child)]
    if pack_children:
        if (source / "SKILL.md").is_file():
            raise SkillMagnetError(
                "ルートSKILL.mdと子パックが混在しており構造を一意に分類できません: "
                + relative(source / "SKILL.md")
            )
        standalone = [
            child
            for child in children
            if child not in pack_children and (child / "SKILL.md").is_file()
        ]
        for child in children:
            _raise_if_cancelled(cancel_event, "登録候補の検査")
            if child in pack_children or child in standalone:
                continue
            descendants = nested_skill_directories(child)
            if descendants:
                ambiguous.extend(relative(item) for item in descendants)
            elif child.name in SUPPORT_DIRECTORY_NAMES or child.name.startswith((".", "_")):
                support.append(relative(child))
            else:
                ambiguous.append(relative(child))
        if ambiguous:
            raise SkillMagnetError(
                "分類できないフォルダーがあります。SKILL.mdを追加するかsupport用途を明示してください: "
                + ", ".join(sorted(set(ambiguous)))
            )
        packs = [pack_candidate(child) for child in pack_children]
        accepted.extend(relative(child) for child in pack_children)
        if standalone:
            skill_sources: dict[str, Path] = {}
            skill_ids: list[str] = []
            metadata: list[tuple[str, str, str]] = []
            for child in standalone:
                _raise_if_cancelled(cancel_event, "登録候補の検査")
                value = _source_skill_metadata(child)
                if value[0] in skill_sources:
                    raise SkillMagnetError(f"Duplicate discovered skill id: {value[0]}")
                skill_sources[value[0]] = child
                skill_ids.append(value[0])
                metadata.append(value)
                accepted.append(relative(child))
            packs.append(
                {
                    "id": "custom-skills",
                    "display_name": "Custom skills",
                    "purpose": "Imported standalone skills",
                    "skills": tuple(skill_ids),
                    "skill_sources": skill_sources,
                    "relations": {kind: [] for kind in RELATION_TYPES},
                    "source_index": "",
                    "entry_skill": "",
                    "candidate_accounting": {},
                }
            )
        accounting = {
            "accepted": sorted(set(accepted)),
            "support": sorted(set(support)),
            "ambiguous": [],
        }
        result = tuple(pack for pack in packs if pack is not None)
        for pack in result:
            pack["candidate_accounting"] = dict(accounting)
        return result
    direct_pack = pack_candidate(source)
    if direct_pack is not None:
        direct_pack["candidate_accounting"] = {
            "accepted": sorted(set(accepted)),
            "support": sorted(set(support)),
            "ambiguous": [],
        }
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
                "candidate_accounting": {
                    "accepted": ["."],
                    "support": sorted(set(support + [relative(child) for child in children])),
                    "ambiguous": [],
                },
            },
        )
    for child in children:
        descendants = nested_skill_directories(child)
        if descendants:
            ambiguous.extend(relative(item) for item in descendants)
        elif child.name not in SUPPORT_DIRECTORY_NAMES and not child.name.startswith((".", "_")):
            ambiguous.append(relative(child))
        else:
            support.append(relative(child))
    if ambiguous:
        raise SkillMagnetError(
            "分類できないフォルダーがあります: " + ", ".join(sorted(set(ambiguous)))
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


def import_skill_source(
    root: Path, source: Path, *, cancel_event: Any | None = None
) -> dict[str, Any]:
    """Atomically import a skill, a complete pack, or a pack collection."""
    root = root.resolve()
    discovered = discover_skill_sources(source, cancel_event=cancel_event)
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

    result = _mutate_library_candidate(
        root,
        mutation,
        operation="import_skill",
        target_id=",".join(incoming_pack_ids),
        source=source,
        cancel_event=cancel_event,
    ).as_dict()
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


def _managed_manifest_snapshot(
    root: Path, *, allow_uncataloged_skills: bool = False
) -> dict[str, str]:
    """Return a checkout-stable managed baseline.

    Git can materialize UTF-8 text with CRLF on Windows while the remote blob
    remains LF.  CRUD concurrency/deletion authorization compares logical text
    bytes so a clean clone is not mistaken for an external edit.  Binary bytes
    remain exact.
    """
    catalog = _read_json(root / CATALOG_FILENAME)
    if catalog.get("packs") == []:
        return {}
    validation = validate_library(
        root, allow_uncataloged_skills=allow_uncataloged_skills
    )
    files = _repository_files(root)

    def logical_digest(data: bytes) -> str:
        if b"\0" not in data:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                data = data.replace(b"\r\n", b"\n")
        return _sha256(data)

    return {
        relative: logical_digest(files[relative])
        for relative in validation.manifest
    }


def _read_local_mutation(root: Path) -> dict[str, Any] | None:
    path = root / LOCAL_MUTATION_FILENAME
    if not path.exists():
        return None
    value = _read_json(path)
    if value.get("schema_version") != LOCAL_MUTATION_VERSION:
        raise SkillMagnetError(
            "ローカル変更記録の形式を確認できません。GitHubから復旧して操作をやり直してください"
        )
    for key in ("base_manifest", "result_manifest", "explicit_deletions"):
        if not isinstance(value.get(key), dict) or not all(
            isinstance(path, str) and isinstance(digest, (str, dict))
            for path, digest in value[key].items()
        ):
            raise SkillMagnetError(
                "ローカル変更記録が壊れています。GitHubから復旧して操作をやり直してください"
            )
    if not isinstance(value.get("operations"), list):
        raise SkillMagnetError(
            "ローカル変更記録に操作履歴がありません。GitHubから復旧して操作をやり直してください"
        )
    return value


def _mark_local_mutation_synchronized(
    root: Path,
    remote_manifest: dict[str, str],
    *,
    expected_revision: int | None = None,
    expected_mutation_id: str | None = None,
    lock_held: bool = False,
) -> None:
    """Rebase a still-current local journal after remote byte verification."""
    root = root.resolve()
    if not root.is_dir():
        return

    def synchronize() -> None:
        state = _read_local_mutation(root)
        if state is None:
            return
        if (
            expected_mutation_id is not None
            and str(state.get("mutation_id", "")) != expected_mutation_id
        ):
            return
        if expected_revision is not None and int(state.get("revision", 0)) != expected_revision:
            return
        current = _managed_manifest_snapshot(root)
        # The user may start a new CRUD operation after prepare.  Never clear
        # that newer intent using completion of the older transaction.
        if state.get("result_manifest") != current or remote_manifest != current:
            return
        state.update(
            # Remote verification established that the current logical
            # checkout corresponds to the published blobs.
            base_manifest=dict(current),
            result_manifest=dict(current),
            explicit_deletions={},
            operations=[],
            pending=False,
            synchronized_at=_utc_now(),
        )
        _atomic_json(root / LOCAL_MUTATION_FILENAME, state)

    if lock_held:
        synchronize()
    else:
        with _library_mutation_lock(root):
            synchronize()


@contextmanager
def _library_mutation_lock(root: Path) -> Iterable[None]:
    """Take a crash-releasing cross-process lock for one managed library."""
    lock_path = root.parent / f".{root.name}.crud.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise SkillMagnetError(
                "同じライブラリで別のCRUD操作が進行中です。完了表示後に再実行してください"
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


@contextmanager
def library_mutation_lock(root: Path) -> Iterable[None]:
    """Share the library CRUD exclusion boundary with lifecycle cleanup.

    Cleanup, hydration, and recovery must not inspect and then remove a library
    while a CRUD candidate is being swapped into place.  Keep the implementation
    private, but expose this deliberately narrow context manager so those
    lifecycle operations use the exact same OS lock byte as CRUD.
    """

    with _library_mutation_lock(root.resolve()):
        yield


def _mutate_library_candidate(
    root: Path,
    mutation: Callable[[Path], None],
    *,
    operation: str = "crud",
    target_id: str = "",
    source: Path | None = None,
    cancel_event: Any | None = None,
) -> ValidationResult:
    """Validate a complete isolated candidate, then atomically replace the library."""
    root = root.resolve()
    if not root.is_dir():
        raise SkillMagnetError(f"Library does not exist: {root}")
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True)
    with _library_mutation_lock(root):
        _raise_if_cancelled(cancel_event, "ライブラリ操作")
        baseline_digest = _tree_digest(root)
        before_manifest = _managed_manifest_snapshot(root)
        mutation_state = _read_local_mutation(root)
        if mutation_state is None:
            mutation_state = {
                "schema_version": LOCAL_MUTATION_VERSION,
                "mutation_id": uuid.uuid4().hex,
                "base_manifest": dict(before_manifest),
                "result_manifest": dict(before_manifest),
                "explicit_deletions": {},
                "operations": [],
                "pending": False,
                "revision": 0,
            }
        elif mutation_state["result_manifest"] != before_manifest:
            raise SkillMagnetError(
                "ローカルライブラリが前回操作後に外部変更されています。"
                "GitHubから復旧して変更をやり直してください"
            )
        staging_root = Path(tempfile.mkdtemp(prefix=f".{root.name}-crud-", dir=parent))
        candidate = staging_root / root.name
        backup = parent / f".{root.name}-backup-{uuid.uuid4().hex}"
        replaced = False
        try:
            # Preserve an existing local checkout so CLI callers do not lose
            # their recovery/history metadata when using the same CRUD API.
            # Validation and publishing still exclude .git content.
            # Git metadata belongs to the live checkout, not to the managed
            # library candidate.  Copying it here would let a concurrent git
            # operation update the original metadata after this snapshot and
            # then have that update overwritten by the stale candidate.
            def copy_with_cancel(source_name: str, destination_name: str) -> str:
                _raise_if_cancelled(cancel_event, "ライブラリ候補のコピー")
                return shutil.copy2(source_name, destination_name)

            shutil.copytree(
                root,
                candidate,
                ignore=shutil.ignore_patterns(".git"),
                copy_function=copy_with_cancel,
            )
            _raise_if_cancelled(cancel_event, "ライブラリ候補のコピー")
            mutation(candidate)
            _raise_if_cancelled(cancel_event, "ライブラリ候補の生成")
            catalog = _read_json(candidate / CATALOG_FILENAME)
            (candidate / "INDEX.md").write_text(
                render_index(catalog), encoding="utf-8", newline="\n"
            )
            validation = validate_library(candidate)
            after_manifest = _managed_manifest_snapshot(candidate)
            _raise_if_cancelled(cancel_event, "ライブラリ候補の検証")
            if after_manifest == before_manifest:
                # A repeated registration/update is a true no-op. Do not
                # manufacture a pending mutation revision or swap a byte-
                # identical candidate over a live checkout.
                return validation
            deleted_paths = sorted(set(before_manifest) - set(after_manifest))
            if deleted_paths and operation in {"add_skill", "import_skill"}:
                raise SkillMagnetError(
                    "追加操作が既存ファイルを削除しようとしたため停止しました: "
                    + ", ".join(deleted_paths)
                )
            explicit = mutation_state["explicit_deletions"]
            for relative in deleted_paths:
                if relative in before_manifest:
                    explicit[relative] = {
                        "sha256": before_manifest[relative],
                        "operation": operation,
                        "target_id": target_id,
                    }
            mutation_state.update(
                result_manifest=dict(after_manifest),
                pending=True,
                revision=int(mutation_state.get("revision", 0)) + 1,
                updated_at=_utc_now(),
            )
            mutation_state["operations"].append(
                {
                    "operation": operation,
                    "target_id": target_id,
                    "source": str(source.resolve()) if source is not None else None,
                    "deleted_paths": deleted_paths,
                    "before_manifest": dict(before_manifest),
                    "result_manifest": dict(after_manifest),
                    "at": _utc_now(),
                }
            )
            _atomic_json(candidate / LOCAL_MUTATION_FILENAME, mutation_state)
            if _tree_digest(root) != baseline_digest:
                raise SkillMagnetError(
                    "CRUD処理中にライブラリが変更されました。変更は反映せず、最新状態から再実行してください"
                )
            # This is the last cancellation point before the atomic commit.
            # Before it, only the isolated candidate was changed.  After it,
            # the candidate already contains the durable mutation journal, so
            # an abrupt close remains recoverable.
            _raise_if_cancelled(cancel_event, "ライブラリ操作")
            os.replace(root, backup)
            replaced = True
            try:
                os.replace(candidate, root)
                # Preserve the original checkout metadata byte-for-byte.  It
                # is never copied into, validated as, or replaced by the CRUD
                # candidate.  A crash before this move is recoverable from the
                # retained backup in recover_interrupted_library().
                backup_git = backup / ".git"
                if os.path.lexists(backup_git):
                    os.replace(backup_git, root / ".git")
            except Exception:
                # Restore the old directory when possible.  If an OS-level
                # handle prevents rollback, retain the backup so startup
                # recovery can reattach/restore it; never delete it here.
                failed_candidate = staging_root / "failed-candidate"
                if root.exists():
                    try:
                        os.replace(root, failed_candidate)
                    except OSError:
                        pass
                if backup.exists() and not root.exists():
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
            # A backup which still exists after an unsuccessful replacement is
            # recovery evidence.  Do not erase it merely because a partial new
            # root also exists.


def recover_interrupted_library(root: Path) -> dict[str, Any]:
    """Restore a valid CRUD swap checkpoint without discarding the last copy.

    Recovery takes the *same* process-wide lock as CRUD.  It never treats a
    concurrently absent root as proof of a crash and never deletes a valid
    backup as part of deciding which copy is authoritative.
    """
    root = Path(os.path.abspath(os.fspath(root)))
    with _library_mutation_lock(root):
        backups = sorted(
            root.parent.glob(f".{root.name}-backup-*"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        valid_backups: list[Path] = []
        for backup in backups:
            if _is_link(backup):
                continue
            try:
                validate_library(backup)
                valid_backups.append(backup)
            except (OSError, SkillMagnetError):
                continue

        if root.exists():
            try:
                validate_library(root)
                root_valid = True
            except (OSError, SkillMagnetError):
                root_valid = False
            if root_valid:
                # The candidate can become visible just before preserved Git
                # metadata is reattached. Move only the metadata; retaining the
                # remaining valid backup is intentional recovery evidence.
                root_git = root / ".git"
                if not os.path.lexists(root_git):
                    for backup in backups:
                        if _is_link(backup):
                            continue
                        backup_git = backup / ".git"
                        if not os.path.lexists(backup_git):
                            continue
                        os.replace(backup_git, root_git)
                        # Remove only an empty shell. rmdir deliberately fails
                        # when the backup still contains managed/user bytes.
                        try:
                            backup.rmdir()
                        except OSError:
                            pass
                        return {
                            "recovered": True,
                            "repository": str(root),
                            "backup": str(backup),
                            "recovery": "git_metadata_reattached",
                            "backup_preserved": backup.exists(),
                        }
                return {"recovered": False, "repository": str(root)}
            if not valid_backups:
                return {
                    "recovered": False,
                    "repository": str(root),
                    "recovery_error": "current_and_backups_invalid",
                }
            # Preserve the invalid/partial candidate for diagnosis before
            # restoring the newest independently validated backup.
            invalid = root.parent / f".{root.name}-recovery-invalid-{uuid.uuid4().hex}"
            os.replace(root, invalid)
            selected = valid_backups[0]
            try:
                os.replace(selected, root)
                validate_library(root)
            except Exception:
                if not root.exists() and invalid.exists():
                    os.replace(invalid, root)
                raise
            return {
                "recovered": True,
                "repository": str(root),
                "backup": str(selected),
                "invalid_candidate_preserved": str(invalid),
                "recovery": "valid_backup_restored",
            }

        if not valid_backups:
            return {"recovered": False, "repository": str(root)}
        selected = valid_backups[0]
        os.replace(selected, root)
        validate_library(root)
        return {
            "recovered": True,
            "repository": str(root),
            "backup": str(selected),
            "recovery": "missing_root_restored",
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
    sibling_set = set(map(str, sibling_skill_ids))
    for relative_text, data in _repository_files(source).items():
        relative = PurePosixPath(relative_text)
        # A root-entry pack owns its support files, but each child skill is
        # imported separately at library root.  Copying those child trees into
        # the entry skill creates two independently mutable copies.
        if relative.parts and relative.parts[0] in sibling_set:
            continue
        if "__pycache__" in relative.parts:
            continue
        if relative.name == "acceptance.json" or relative.suffix == ".pyc":
            continue
        destination = target.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    skill_bytes = (target / "SKILL.md").read_bytes()
    if sibling_set:
        text = skill_bytes.decode("utf-8-sig")
        for sibling in sibling_set:
            text = text.replace(
                f"]({sibling}/SKILL.md)", f"](../{sibling}/SKILL.md)"
            )
        skill_bytes = text.encode("utf-8")
    (target / "SKILL.md").write_bytes(skill_bytes)
    _atomic_json(target / "acceptance.json", acceptance)
    return skill_id, display_name, purpose, generated


def update_skill_source(
    root: Path,
    skill_id: str,
    source: Path,
    *,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    """Replace one selected skill from a same-ID source folder."""
    source = source.resolve()
    discovered = discover_skill_sources(source, cancel_event=cancel_event)
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

    result = _mutate_library_candidate(
        root,
        mutation,
        operation="update_skill",
        target_id=skill_id,
        source=source,
        cancel_event=cancel_event,
    ).as_dict()
    result.update(operation="update_skill", skill_id=skill_id, generated_acceptance=generated)
    return result


def update_pack_source(
    root: Path,
    pack_id: str,
    source: Path,
    *,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    """Replace one selected pack while preserving skills shared by other packs."""
    source = source.resolve()
    discovered = discover_skill_sources(source, cancel_event=cancel_event)
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
        incoming_skills = set(map(str, incoming["skills"]))
        implicit_removals = old_skills - incoming_skills
        if implicit_removals:
            raise SkillMagnetError(
                "パック更新によるスキル削除・renameは自動判定できません。"
                "先に対象スキルを明示的に削除してください: "
                + ", ".join(sorted(implicit_removals))
            )
        metadata: dict[str, dict[str, str]] = {}
        for incoming_skill in incoming["skills"]:
            source_folder = incoming["skill_sources"][incoming_skill]
            siblings = (
                set(map(str, incoming["skills"])) - {str(incoming_skill)}
                if source_folder == source
                else ()
            )
            if incoming_skill in other_skills:
                comparison_root = Path(
                    tempfile.mkdtemp(prefix=f".shared-{incoming_skill}-", dir=candidate.parent)
                )
                comparison = comparison_root / incoming_skill
                try:
                    actual_id, display_name, purpose, generated = _write_source_skill(
                        comparison,
                        source_folder,
                        sibling_skill_ids=siblings,
                    )
                    if _repository_files(comparison) != _repository_files(
                        candidate / incoming_skill
                    ):
                        raise SkillMagnetError(
                            f"共有スキル{incoming_skill}の内容が他パックと異なるため更新を拒否しました"
                        )
                finally:
                    shutil.rmtree(comparison_root, ignore_errors=True)
            else:
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
        for obsolete in old_skills - incoming_skills - other_skills:
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

    result = _mutate_library_candidate(
        root,
        mutation,
        operation="update_pack",
        target_id=pack_id,
        source=source,
        cancel_event=cancel_event,
    ).as_dict()
    result.update(operation="update_pack", pack_id=pack_id, generated_acceptance_count=generated_count)
    return result


def delete_skill(
    root: Path,
    skill_id: str,
    *,
    confirmed: bool,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
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

    result = _mutate_library_candidate(
        root,
        mutation,
        operation="delete_skill",
        target_id=skill_id,
        cancel_event=cancel_event,
    ).as_dict()
    result.update(operation="delete_skill", skill_id=skill_id)
    return result


def delete_pack(
    root: Path,
    pack_id: str,
    *,
    confirmed: bool,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
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

    result = _mutate_library_candidate(
        root,
        mutation,
        operation="delete_pack",
        target_id=pack_id,
        cancel_event=cancel_event,
    ).as_dict()
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

    def mutation(candidate: Path) -> None:
        catalog_path = candidate / CATALOG_FILENAME
        catalog = _read_json(catalog_path)
        existing_skills = set(_catalog_skills(catalog)) if catalog.get("packs") else set()
        target = candidate / skill_id
        if skill_id in existing_skills or target.exists():
            raise SkillMagnetError(f"Skill already exists: {skill_id}")
        target.mkdir()
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

    return _mutate_library_candidate(
        root,
        mutation,
        operation="add_skill",
        target_id=skill_id,
        source=skill_source,
    ).as_dict()


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    cancel_event: Any | None = None,
) -> subprocess.CompletedProcess[str]:
    timeout = _external_command_timeout()
    environment = os.environ.copy()
    # Git Credential Manager, Git itself, and gh can otherwise open a hidden
    # prompt while the Library Manager appears to have frozen.  Authentication
    # must already be configured; a missing credential is a recoverable error.
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "SSH_ASKPASS_REQUIRE": "never",
        }
    )
    process = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        **_process_group_options(),
    )
    started = time.monotonic()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _terminate_process(process)
            raise ExternalCommandCancelled(args[0])
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            _terminate_process(process)
            raise ExternalCommandTimeout(args[0], timeout)
        try:
            stdout, stderr = process.communicate(timeout=min(0.1, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise SkillMagnetError(f"Command failed ({args[0]}): {detail}")
    return result


def upsert_skill_source(
    root: Path, source: Path, *, cancel_event: Any | None = None
) -> dict[str, Any]:
    """Atomically add or refresh every pack discovered under ``source``.

    Existing packs may add members but may not implicitly remove/rename them.
    A skill referenced by another pack is shared only when every generated byte
    is identical; otherwise the whole collection update is rolled back.
    """
    root = root.resolve()
    source = source.resolve()
    discovered = discover_skill_sources(source, cancel_event=cancel_event)
    pack_ids = [str(pack["id"]) for pack in discovered]
    if len(pack_ids) != len(set(pack_ids)):
        raise SkillMagnetError("Duplicate discovered pack ids")
    imported_packs: list[str] = []
    updated_packs: list[str] = []
    imported_skills: list[str] = []
    updated_skills: list[str] = []
    library_changed = False

    def mutation(candidate: Path) -> None:
        nonlocal library_changed
        catalog = _read_json(candidate / CATALOG_FILENAME)
        catalog_before = _canonical(catalog)
        packs = catalog.setdefault("packs", [])
        by_id = {str(pack.get("id")): pack for pack in packs}
        for incoming in discovered:
            pack_id = str(incoming["id"])
            incoming_ids = list(map(str, incoming["skills"]))
            target_pack = by_id.get(pack_id)
            if target_pack is None:
                same_members = [
                    str(pack.get("id"))
                    for pack in packs
                    if set(map(str, pack.get("skills", []))) == set(incoming_ids)
                ]
                if same_members:
                    raise SkillMagnetError(
                        "同じスキル構成のパックが登録済みです: "
                        + ", ".join(sorted(same_members))
                    )
                target_pack = {
                    "id": pack_id,
                    "skills": [],
                    "skill_metadata": {},
                    "relations": {kind: [] for kind in RELATION_TYPES},
                }
                packs.append(target_pack)
                by_id[pack_id] = target_pack
                imported_packs.append(pack_id)
            else:
                removed = set(map(str, target_pack.get("skills", []))) - set(incoming_ids)
                if pack_id != "custom-skills" and removed:
                    raise SkillMagnetError(
                        "パック更新によるスキル削除・renameは拒否しました。"
                        "先に明示的な削除を実行してください: "
                        + ", ".join(sorted(removed))
                    )
                updated_packs.append(pack_id)

            metadata = (
                dict(target_pack.get("skill_metadata", {}))
                if pack_id == "custom-skills"
                else {}
            )
            final_ids = (
                list(map(str, target_pack.get("skills", [])))
                if pack_id == "custom-skills"
                else []
            )
            for skill_id in incoming_ids:
                source_folder = incoming["skill_sources"][skill_id]
                siblings = (
                    set(incoming_ids) - {skill_id}
                    if source_folder == source or source_folder.name == pack_id
                    else ()
                )
                temporary_root = Path(
                    tempfile.mkdtemp(prefix=f".upsert-{skill_id}-", dir=candidate.parent)
                )
                prepared = temporary_root / skill_id
                try:
                    actual_id, display_name, purpose, _ = _write_source_skill(
                        prepared, source_folder, sibling_skill_ids=siblings
                    )
                    if actual_id != skill_id:
                        raise SkillMagnetError(
                            f"SKILL.md name must equal directory id: {skill_id}"
                        )
                    destination = candidate / skill_id
                    if destination.exists():
                        if _repository_files(destination) != _repository_files(prepared):
                            memberships = {
                                str(pack.get("id"))
                                for pack in packs
                                if skill_id in set(map(str, pack.get("skills", [])))
                            }
                            if memberships - {pack_id}:
                                raise SkillMagnetError(
                                    f"共有スキル{skill_id}の内容が他パックと異なるため更新を拒否しました"
                                )
                            shutil.rmtree(destination)
                            os.replace(prepared, destination)
                            updated_skills.append(skill_id)
                            library_changed = True
                    else:
                        os.replace(prepared, destination)
                        imported_skills.append(skill_id)
                        library_changed = True
                    if skill_id not in final_ids:
                        final_ids.append(skill_id)
                    metadata[skill_id] = {
                        "display_name": display_name,
                        "purpose": purpose,
                    }
                finally:
                    shutil.rmtree(temporary_root, ignore_errors=True)
            target_pack.update(
                display_name=incoming["display_name"],
                purpose=incoming["purpose"],
                skills=final_ids,
                skill_metadata=metadata,
                relations=incoming["relations"],
                source_index=incoming["source_index"],
                entry_skill=incoming["entry_skill"],
            )
        _atomic_json(candidate / CATALOG_FILENAME, catalog)
        library_changed = library_changed or _canonical(catalog) != catalog_before

    result = _mutate_library_candidate(
        root,
        mutation,
        operation="upsert_source",
        target_id=",".join(pack_ids),
        source=source,
        cancel_event=cancel_event,
    ).as_dict()
    result.update(
        operation="upsert_source",
        imported_pack_ids=imported_packs,
        updated_pack_ids=updated_packs,
        imported_skill_ids=sorted(set(imported_skills)),
        updated_skill_ids=sorted(set(updated_skills)),
        changed=library_changed,
        no_changes=not library_changed,
    )
    return result


def local_mutation_status(root: Path) -> dict[str, Any]:
    """Expose a recoverable pre-publish CRUD checkpoint to the UI."""
    root = root.resolve()
    recovered = recover_interrupted_library(root)
    state = _read_local_mutation(root) if root.is_dir() else None
    if state is None:
        return {"pending": False, "repository": str(root), **recovered}
    current = _managed_manifest_snapshot(root)
    matches = state.get("result_manifest") == current
    return {
        "pending": bool(state.get("pending")),
        "repository": str(root),
        "mutation_id": str(state.get("mutation_id", "")),
        "operations": list(state.get("operations", [])),
        "current_matches_checkpoint": matches,
        "recovered_directory_swap": bool(recovered.get("recovered")),
        "recovery_action": (
            "同じGitHub反映処理を再実行してください"
            if matches
            else "GitHubから復旧し、CRUD操作をやり直してください"
        ),
    }


def checkpoint_legacy_mutation(
    root: Path,
    *,
    remote_baseline_manifest: dict[str, str],
    local_result_manifest: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Adopt a verified legacy edit without inventing deletion intent.

    The caller must supply the logical manifest read from its isolated remote
    comparison.  A legacy tree missing any remote-managed path is refused;
    deletion must go through an explicit CRUD action instead.
    """
    root = root.resolve()
    with _library_mutation_lock(root):
        current = _managed_manifest_snapshot(root)
        if local_result_manifest is not None and current != local_result_manifest:
            raise SkillMagnetError(
                "旧libraryの比較後にローカル内容が変更されました。比較からやり直してください"
            )
        missing = sorted(set(remote_baseline_manifest) - set(current))
        if missing:
            raise SkillMagnetError(
                "旧library移行ではGitHubファイルの削除を承認できません。"
                "GitHubから復旧後、明示的なCRUD削除を実行してください: "
                + ", ".join(missing)
            )
        state = {
            "schema_version": LOCAL_MUTATION_VERSION,
            "mutation_id": uuid.uuid4().hex,
            "base_manifest": dict(remote_baseline_manifest),
            "result_manifest": dict(current),
            "explicit_deletions": {},
            "operations": [
                {
                    "operation": "legacy_migration",
                    "target_id": "",
                    "source": str(root),
                    "deleted_paths": [],
                    "before_manifest": dict(remote_baseline_manifest),
                    "result_manifest": dict(current),
                    "at": _utc_now(),
                }
            ],
            "pending": True,
            "updated_at": _utc_now(),
        }
        _atomic_json(root / LOCAL_MUTATION_FILENAME, state)
    return local_mutation_status(root)


class ExternalCommandTimeout(SkillMagnetError):
    """A bounded git/gh invocation exceeded the configured deadline."""

    def __init__(self, program: str, timeout_seconds: float) -> None:
        self.program = program
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"{program} の応答が {timeout_seconds:g} 秒以内に返りませんでした。"
            "処理状態は保存されています。ネットワークとGitHub認証を確認し、"
            "同じ作業を再試行してください"
        )


class ExternalCommandCancelled(SkillMagnetError):
    """The user closed the UI while an external command was running."""

    def __init__(self, program: str) -> None:
        self.program = program
        super().__init__(
            f"{program} の待機を中止しました。処理状態は保存されています。"
            "Library Managerを開き直すと同じ作業を再開できます"
        )


def _external_command_timeout() -> float:
    raw = os.environ.get("SKILL_MAGNET_EXTERNAL_COMMAND_TIMEOUT_SECONDS", "120").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise SkillMagnetError(
            "SKILL_MAGNET_EXTERNAL_COMMAND_TIMEOUT_SECONDS must be a number"
        ) from exc
    if not 0.01 <= value <= 900:
        raise SkillMagnetError(
            "SKILL_MAGNET_EXTERNAL_COMMAND_TIMEOUT_SECONDS must be between 0.01 and 900"
        )
    return value


def _process_group_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    """Bound termination of the command and its descendant process tree."""
    if os.name == "nt":
        try:
            # /T is essential: git/gh and credential helpers may have spawned
            # descendants which keep locks or inherited pipes after the direct
            # process is stopped.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            if process.poll() is None:
                process.terminate()
    try:
        process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=3,
                )
            except (OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                if process.poll() is None:
                    process.kill()
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            # A misbehaving grandchild can keep inherited pipes open even after
            # the direct process dies.  Closing our handles keeps this cleanup
            # path finite; the transaction journal remains the recovery source.
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _run_bytes(
    args: list[str], *, cwd: Path | None = None, cancel_event: Any | None = None
) -> subprocess.CompletedProcess[bytes]:
    timeout = _external_command_timeout()
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "SSH_ASKPASS_REQUIRE": "never",
        }
    )
    process = subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        **_process_group_options(),
    )
    started = time.monotonic()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _terminate_process(process)
            raise ExternalCommandCancelled(args[0])
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            _terminate_process(process)
            raise ExternalCommandTimeout(args[0], timeout)
        try:
            stdout, stderr = process.communicate(timeout=min(0.1, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


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


def _serialized_transaction(method: Callable[..., Any]) -> Callable[..., Any]:
    """Hold one transaction lock across journal reads and remote side effects."""

    @wraps(method)
    def guarded(self: "LibraryTransaction", *args: Any, **kwargs: Any) -> Any:
        with self._transaction_lock():
            return method(self, *args, **kwargs)

    return guarded


def _serialized_draft_snapshot(method: Callable[..., Any]) -> Callable[..., Any]:
    """Keep CRUD, recovery, hydration, and purge outside a prepare snapshot."""

    @wraps(method)
    def guarded(self: "LibraryTransaction", *args: Any, **kwargs: Any) -> Any:
        if "draft" not in kwargs:
            raise SkillMagnetError("Library draft is required")
        lexical = Path(os.path.abspath(os.fspath(kwargs["draft"])))
        selected = _selected_directory(lexical, label="ライブラリ")
        with _library_mutation_lock(selected):
            # Recheck after acquiring the sibling lock; a directory must not be
            # swapped to a junction between approval and snapshot.
            current = _selected_directory(lexical, label="ライブラリ")
            if _draft_identity(current) != _draft_identity(selected):
                raise SkillMagnetError(
                    "ライブラリが処理開始時に差し替えられたため停止しました。"
                    "現在のフォルダーを確認して再実行してください"
                )
            call_kwargs = dict(kwargs)
            call_kwargs["draft"] = current
            return method(self, *args, **call_kwargs)

    return guarded


class LibraryTransaction:
    def __init__(
        self,
        state_dir: Path,
        transaction_id: str | None = None,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = _run,
        cancel_event: Any | None = None,
    ) -> None:
        resolved_state = state_dir.resolve()
        for reserved in reserved_skill_content_roots():
            try:
                resolved_state.relative_to(reserved)
            except ValueError:
                continue
            raise SkillMagnetError(
                "スキル実体の保存先をLibrary Managerの作業領域にはできません: "
                f"{resolved_state}。状態保存先を ~/.skill-magnet などの専用領域へ変更して、"
                "同じ操作を再実行してください"
            )
        self.state_dir = resolved_state / "library-transactions"
        self.transaction_id = transaction_id or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", self.transaction_id):
            raise SkillMagnetError("Invalid transaction id")
        self.root = self.state_dir / self.transaction_id
        self.journal_path = self.root / "journal.json"
        self.receipt_path = self.root / "receipt.json"
        self.workspace = self.root / "workspace"
        self.verifier = self.root / "remote-verifier"
        self.run = run
        self.cancel_event = cancel_event
        self._uses_default_runner = run is _run
        self._operation_thread_lock = threading.RLock()
        self._operation_lock_state = threading.local()

    @contextmanager
    def _transaction_lock(self) -> Iterable[None]:
        """Crash-releasing, same-instance-reentrant lock for one transaction.

        The in-process RLock queues calls made through the same instance. The
        OS byte lock rejects another instance/process before either can issue
        a duplicate push, PR, merge, or activation side effect.
        """
        with self._operation_thread_lock:
            depth = int(getattr(self._operation_lock_state, "depth", 0))
            if depth:
                self._operation_lock_state.depth = depth + 1
                try:
                    yield
                finally:
                    self._operation_lock_state.depth = depth
                return

            self.state_dir.mkdir(parents=True, exist_ok=True)
            lock_path = self.state_dir / f".{self.transaction_id}.operation.lock"
            handle = lock_path.open("a+b")
            try:
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, BlockingIOError) as exc:
                    raise SkillMagnetError(
                        "同じGitHub反映処理が別のプロセスで進行中です。"
                        "完了または中断表示後に、同じ作業を再開してください"
                    ) from exc
                self._operation_lock_state.depth = 1
                try:
                    yield
                finally:
                    self._operation_lock_state.depth = 0
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _journal(self) -> dict[str, Any]:
        if not self.journal_path.exists():
            return {
                "schema_version": TRANSACTION_VERSION,
                "transaction_id": self.transaction_id,
                "status": "draft",
                "created_at": _utc_now(),
            }
        value = _read_json(self.journal_path)
        self._validate_journal(value)
        return value

    def _validate_journal(self, journal: dict[str, Any]) -> None:
        if journal.get("schema_version") != TRANSACTION_VERSION:
            raise SkillMagnetError(
                f"GitHub反映の作業記録の形式を確認できません: {self.journal_path}"
            )
        if str(journal.get("transaction_id", "")) != self.transaction_id:
            raise SkillMagnetError(
                f"GitHub反映の作業IDが保存先と一致しません: {self.journal_path}"
            )
        try:
            if "status" not in journal:
                raise SkillMagnetError("作業状態がありません")
            LibraryState.from_journal(journal)
        except SkillMagnetError as exc:
            raise SkillMagnetError(
                f"GitHub反映の作業状態を確認できません: {self.journal_path}"
            ) from exc

    @_serialized_transaction
    def _write_journal(self, journal: dict[str, Any]) -> None:
        journal.setdefault("schema_version", TRANSACTION_VERSION)
        journal.setdefault("transaction_id", self.transaction_id)
        self._validate_journal(journal)
        journal["updated_at"] = _utc_now()
        _atomic_json(self.journal_path, journal)

    def _assert_transaction_identity(
        self, journal: dict[str, Any], *, draft: Path, remote: str
    ) -> tuple[Path, str]:
        draft = _selected_directory(draft, label="ライブラリ")
        remote = canonical_remote_identity(remote)
        saved_draft = str(journal.get("draft", ""))
        saved_remote = str(journal.get("remote", ""))
        if saved_draft and _draft_identity(Path(saved_draft)) != _draft_identity(draft):
            raise SkillMagnetError(
                "この処理は別のライブラリ用です。"
                f"保存済み: {saved_draft} / 選択中: {draft}。"
                "保存済みの作業を再開するか、新しい処理を開始してください"
            )
        if saved_remote and canonical_remote_identity(saved_remote) != remote:
            raise SkillMagnetError(
                "この処理は別のGitHub公開先用です。"
                f"保存済み: {saved_remote} / 入力中: {remote}。"
                "保存済みの公開先に戻すか、新しい処理を開始してください"
            )
        return draft, remote

    def _command_stage(self) -> str:
        status = str(self._journal().get("status", "draft"))
        return {
            "draft": "prepare",
            "preparing": "prepare",
            "prepared": "publish",
            "publishing": "publish",
            "published_pending": "merge_or_verify",
            "verified": "activate",
            "activating": "activate",
            "menu_pending": "activate",
        }.get(status, "external_command")

    def _record_external_interruption(
        self, error: ExternalCommandTimeout | ExternalCommandCancelled, args: list[str]
    ) -> None:
        journal = self._journal()
        reason = "timeout" if isinstance(error, ExternalCommandTimeout) else "cancelled"
        journal.update(
            failed_stage=f"{self._command_stage()}_command_{reason}",
            last_error=str(error),
            interrupted_command=str(args[0]) if args else "unknown",
            recovery_action="retry_same_transaction",
        )
        if isinstance(error, ExternalCommandTimeout):
            journal["command_timeout_seconds"] = error.timeout_seconds
        journal["command_interrupted_at"] = _utc_now()
        self._write_journal(journal)

    def _exec(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        kwargs: dict[str, Any] = {"cwd": cwd, "check": check}
        if self._uses_default_runner:
            kwargs["cancel_event"] = self.cancel_event
        try:
            return self.run(args, **kwargs)
        except (ExternalCommandTimeout, ExternalCommandCancelled) as exc:
            self._record_external_interruption(exc, args)
            raise
        except subprocess.TimeoutExpired as exc:
            # Test/future injected runners may expose TimeoutExpired directly.
            timeout = float(exc.timeout) if exc.timeout is not None else _external_command_timeout()
            wrapped = ExternalCommandTimeout(args[0], timeout)
            self._record_external_interruption(wrapped, args)
            raise wrapped from exc

    def _exec_bytes(
        self, args: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        try:
            return _run_bytes(args, cwd=cwd, cancel_event=self.cancel_event)
        except (ExternalCommandTimeout, ExternalCommandCancelled) as exc:
            self._record_external_interruption(exc, args)
            raise

    @_serialized_transaction
    @_serialized_draft_snapshot
    def prepare(
        self,
        *,
        draft: Path,
        remote: str,
        branch: str | None = None,
    ) -> dict[str, Any]:
        journal = self._journal()
        draft, remote = self._assert_transaction_identity(
            journal, draft=draft, remote=remote
        )
        if journal.get("draft_unavailable"):
            raise SkillMagnetError(
                "一時作業領域が失われたため、未送信の編集を再現できません。"
                "この作業を破棄し、登録元を選び直して編集をやり直してください。"
                "GitHubの公開済みデータは保持されています。"
            )
        if journal["status"] != "draft":
            preview = journal.get("preview")
            if not isinstance(preview, dict):
                raise SkillMagnetError(
                    "保存済みの処理状態に確認内容がありません。"
                    "作業記録を保存したまま、この処理の復旧を選んでください"
                )
            return preview
        before = _tree_digest(draft)
        validation = validate_library(draft)
        local_baseline_manifest = _managed_manifest_snapshot(draft)
        mutation_state = _read_local_mutation(draft)
        if mutation_state is not None and mutation_state["result_manifest"] != local_baseline_manifest:
            raise SkillMagnetError(
                "ローカル変更記録と現在のライブラリが一致しません。"
                "GitHubから復旧してCRUD操作をやり直してください"
            )
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
            self._exec(["git", "clone", "--no-hardlinks", remote, str(self.workspace)])
            default_branch = self._exec(
                ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                cwd=self.workspace,
                check=False,
            ).stdout.strip()
            default_branch = default_branch.removeprefix("origin/") or "main"
            branch_name = branch or f"codex/skill-library-{self.transaction_id[:12]}"
            if branch_name == default_branch:
                self._exec(["git", "switch", default_branch], cwd=self.workspace)
            else:
                self._exec(["git", "switch", "-c", branch_name], cwd=self.workspace)
            previous_manifest: dict[str, str] = {}
            if (self.workspace / CATALOG_FILENAME).is_file():
                validate_library(self.workspace, allow_uncataloged_skills=True)
                previous_manifest = _managed_manifest_snapshot(
                    self.workspace, allow_uncataloged_skills=True
                )
            remote_differs = previous_manifest != local_baseline_manifest
            if remote_differs:
                if mutation_state is None or not mutation_state.get("pending"):
                    raise SkillMagnetError(
                        "公開先GitHubのbaselineに対応する明示的なCRUD記録がありません。"
                        "GitHubからライブラリを復旧し、登録・更新・削除をやり直してください"
                    )
                if mutation_state["base_manifest"] != previous_manifest:
                    # A caller may have explicitly committed/pushed an earlier
                    # CRUD checkpoint before starting the next one.  Accept
                    # only an exact recorded checkpoint; arbitrary stale or
                    # independently changed remote bytes still fail closed.
                    recorded_checkpoints = [
                        operation.get("result_manifest")
                        for operation in mutation_state.get("operations", [])
                        if isinstance(operation, dict)
                        and isinstance(operation.get("result_manifest"), dict)
                    ]
                    if previous_manifest not in recorded_checkpoints:
                        changed_remote = sorted(
                            set(mutation_state["base_manifest"]) ^ set(previous_manifest)
                            | {
                                path
                                for path in set(mutation_state["base_manifest"])
                                & set(previous_manifest)
                                if mutation_state["base_manifest"][path]
                                != previous_manifest[path]
                            }
                        )
                        raise SkillMagnetError(
                            "公開先GitHubがローカルCRUDのbaselineから更新されています。"
                            "最新内容を復旧して操作をやり直してください: "
                            + ", ".join(changed_remote[:20])
                        )
            approved_deletions = set(previous_manifest) - set(local_baseline_manifest)
            explicit_deletions = (
                mutation_state.get("explicit_deletions", {}) if mutation_state else {}
            )
            missing_intent = sorted(approved_deletions - set(explicit_deletions))
            changed_since_intent = sorted(
                path
                for path in approved_deletions & set(explicit_deletions)
                if not isinstance(explicit_deletions[path], dict)
                or explicit_deletions[path].get("sha256") != previous_manifest[path]
            )
            if missing_intent or changed_since_intent:
                raise SkillMagnetError(
                    "明示的なCRUD削除意図とGitHub baselineが一致しないため削除を拒否しました: "
                    + ", ".join(missing_intent + changed_since_intent)
                )
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
            self._exec(["git", "add", "--all"], cwd=self.workspace)
            staged_manifest: dict[str, str] = {}
            for relative in validation.manifest:
                blob = self._exec_bytes(
                    ["git", "show", f":{relative}"],
                    cwd=self.workspace,
                )
                if blob.returncode:
                    raise SkillMagnetError(f"Cannot read staged Git blob: {relative}")
                staged_manifest[relative] = _sha256(blob.stdout)
            changed = [
                line
                for line in self._exec(
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
            if isinstance(exc, (ExternalCommandTimeout, ExternalCommandCancelled)):
                journal = self._journal()
            journal.update(status="interrupted", resume_status="draft")
            if not isinstance(exc, (ExternalCommandTimeout, ExternalCommandCancelled)):
                journal.update(failed_stage="prepare", last_error=str(exc))
            self._write_journal(journal)
            raise
        preview = {
            "transaction_id": self.transaction_id,
            "remote": remote,
            "branch": branch_name,
            "default_branch": default_branch,
            "changed_files": changed,
            "deleted_managed_files": sorted(set(deleted) & approved_deletions),
            "mutation_id": (
                str(mutation_state.get("mutation_id", "")) if mutation_state else ""
            ),
            "mutation_revision": (
                int(mutation_state.get("revision", 0)) if mutation_state else 0
            ),
            "remote_baseline_manifest": previous_manifest,
            "pack_ids": list(validation.pack_ids),
            "skill_ids": list(validation.skill_ids),
            "manifest": staged_manifest,
            "menu_shape": validation.menu_shape,
            "requires_confirmation": bool(changed),
            "no_changes": not changed,
        }
        current_commit = ""
        if not changed:
            current_commit = self._exec(
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
            _mark_local_mutation_synchronized(
                draft,
                staged_manifest,
                expected_revision=int(preview.get("mutation_revision", 0)),
                expected_mutation_id=str(preview.get("mutation_id", "")),
                lock_held=True,
            )
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
        self._exec(["git", "clone", "--no-checkout", "--no-hardlinks", remote, str(self.verifier)])
        self._exec(["git", "checkout", "--detach", commit], cwd=self.verifier)
        validation = validate_library(self.verifier, allow_uncataloged_skills=True)
        remote_manifest: dict[str, str] = {}
        for relative in validation.manifest:
            blob = self._exec_bytes(
                ["git", "show", f"{commit}:{relative}"],
                cwd=self.verifier,
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

    @_serialized_transaction
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
        if journal.get("draft_unavailable") and not self.workspace.is_dir():
            raise SkillMagnetError(
                "一時作業領域が失われ、未送信の編集を再現できません。"
                "作業を復旧するか、未送信の作業を破棄して登録元を選び直してください。"
            )
        if create_pr and not direct and not re.match(
            r"https://github\.com/[^/]+/[^/]+(?:\.git)?$", str(journal["remote"])
        ):
            raise SkillMagnetError("Pull request publishing requires a GitHub repository URL")
        if direct and journal["branch"] != journal["default_branch"]:
            raise SkillMagnetError(
                "Direct publish requires explicitly preparing the default branch"
            )
        self._exec(["git", "add", "--all"], cwd=self.workspace)
        staged = self._exec(["git", "diff", "--cached", "--quiet"], cwd=self.workspace, check=False)
        if staged.returncode not in {0, 1}:
            raise SkillMagnetError("Cannot inspect staged library changes")
        if staged.returncode == 1:
            self._exec(
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
        commit = self._exec(["git", "rev-parse", "HEAD"], cwd=self.workspace).stdout.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise SkillMagnetError("Git did not produce a full commit SHA")
        journal.update(status="publishing", commit=commit, publish_started_at=_utc_now())
        self._write_journal(journal)
        remote_ref = self._exec(
            ["git", "ls-remote", "--heads", "origin", journal["branch"]],
            cwd=self.workspace,
        ).stdout.strip()
        if not remote_ref.startswith(commit):
            self._exec(
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
            existing = self._exec(
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
                result = self._exec(
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
        if status == "verified" and journal.get("draft"):
            _mark_local_mutation_synchronized(
                Path(str(journal["draft"])),
                remote_validation.manifest,
                expected_revision=int(journal.get("preview", {}).get("mutation_revision", 0)),
                expected_mutation_id=str(journal.get("preview", {}).get("mutation_id", "")),
            )
        return journal

    def _read_pull_request(self, journal: dict[str, Any]) -> dict[str, Any]:
        command_cwd = self.workspace if self.workspace.is_dir() else self.root
        result = self._exec(
            [
                "gh",
                "pr",
                "view",
                str(journal["pr_url"]),
                "--json",
                "state,mergeCommit,autoMergeRequest",
            ],
            cwd=command_cwd,
        )
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SkillMagnetError(
                "GitHubのPR状態を読み取れませんでした。処理状態は保存されています。"
                "GitHub認証と通信を確認し、同じ作業を再試行してください"
            ) from exc
        if not isinstance(value, dict):
            raise SkillMagnetError("GitHub returned an invalid pull request response")
        return value

    def _verify_merged_pull_request(
        self, journal: dict[str, Any], pull_request: dict[str, Any]
    ) -> dict[str, Any]:
        merge_commit = (pull_request.get("mergeCommit") or {}).get("oid")
        if not isinstance(merge_commit, str) or not re.fullmatch(
            r"[0-9a-fA-F]{40}", merge_commit
        ):
            raise SkillMagnetError(
                "GitHub reported a merged pull request without a valid merge commit"
            )
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
        journal.pop("merge_attempt_started_at", None)
        journal.pop("merge_attempt_strategy", None)
        self._write_journal(journal)
        draft = str(journal.get("draft", ""))
        if draft and Path(draft).is_dir():
            _mark_local_mutation_synchronized(
                Path(draft),
                remote_validation.manifest,
                expected_revision=int(journal.get("preview", {}).get("mutation_revision", 0)),
                expected_mutation_id=str(journal.get("preview", {}).get("mutation_id", "")),
            )
        return journal

    @_serialized_transaction
    def mark_merged(self) -> dict[str, Any]:
        journal = self._journal()
        if journal["status"] == "verified":
            return journal
        if journal["status"] != "published_pending":
            raise SkillMagnetError("Only a published-pending transaction can be verified")
        commit = str(journal["commit"])
        if journal.get("pr_url"):
            pr = self._read_pull_request(journal)
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
            return self._verify_merged_pull_request(journal, pr)
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
        draft = str(journal.get("draft", ""))
        if draft and Path(draft).is_dir():
            _mark_local_mutation_synchronized(
                Path(draft),
                remote_validation.manifest,
                expected_revision=int(journal.get("preview", {}).get("mutation_revision", 0)),
                expected_mutation_id=str(journal.get("preview", {}).get("mutation_id", "")),
            )
        return journal

    @_serialized_transaction
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
            # A crash can happen after GitHub accepted/finished the merge but
            # before the local completion checkpoint.  A durable intent marker
            # distinguishes that re-entry from the first attempt.  Always ask
            # GitHub first on that path; if it is already MERGED, verify bytes
            # and never issue a second merge mutation.
            if journal.get("merge_attempt_started_at"):
                pull_request = self._read_pull_request(journal)
                state = str(pull_request.get("state", "")).upper()
                if state == "MERGED":
                    return self._verify_merged_pull_request(journal, pull_request)
                if state == "CLOSED":
                    journal.update(
                        wait_state="closed_unmerged",
                        pr_state="CLOSED",
                        last_merge_check_at=_utc_now(),
                    )
                    self._write_journal(journal)
                    return journal
                if state != "OPEN":
                    raise SkillMagnetError(
                        f"GitHub returned an unknown pull request state: {state or 'empty'}"
                    )
                if (
                    str(journal.get("merge_attempt_strategy", ""))
                    == "github_auto_merge"
                    and pull_request.get("autoMergeRequest")
                ):
                    # GitHub accepted the auto-merge reservation before the
                    # process died.  Persist that observed side effect and only
                    # poll; issuing another mutation is unnecessary.
                    journal.update(
                        merge_requested_at=_utc_now(),
                        merge_strategy="github_auto_merge_recovered",
                    )
                    journal.pop("merge_attempt_started_at", None)
                    journal.pop("merge_attempt_strategy", None)
                    self._write_journal(journal)
                    return self.mark_merged()
            strategy = str(journal.get("merge_attempt_strategy", "github_auto_merge"))
            journal.update(
                merge_attempt_started_at=_utc_now(),
                merge_attempt_strategy=strategy,
            )
            self._write_journal(journal)
            try:
                if strategy == "github_immediate_merge_fallback":
                    self._exec(
                        ["gh", "pr", "merge", pr_url, "--merge", "--delete-branch"],
                        cwd=command_cwd,
                    )
                    journal["merge_strategy"] = "github_immediate_merge_fallback"
                else:
                    self._exec(
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
                    if isinstance(exc, (ExternalCommandTimeout, ExternalCommandCancelled)):
                        journal = self._journal()
                    else:
                        journal.update(
                            failed_stage="merge",
                            last_error=str(exc),
                            last_strategy="request_github_auto_merge",
                        )
                    self._write_journal(journal)
                    raise
                journal.update(
                    merge_attempt_strategy="github_immediate_merge_fallback",
                    merge_attempt_started_at=_utc_now(),
                )
                self._write_journal(journal)
                self._exec(
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
                if strategy != "github_immediate_merge_fallback":
                    journal["merge_strategy"] = "github_auto_merge"
            journal.update(
                merge_requested_at=_utc_now(),
            )
            journal.pop("merge_attempt_started_at", None)
            journal.pop("merge_attempt_strategy", None)
            journal.pop("failed_stage", None)
            journal.pop("last_error", None)
            journal.pop("last_strategy", None)
            self._write_journal(journal)
        return self.mark_merged()

    @_serialized_transaction
    def reopen_pull_request(self, *, confirmed: bool) -> dict[str, Any]:
        """Reopen exactly the closed, unmerged PR recorded by this transaction.

        The PR is read before mutation, so a crash after GitHub reopens it but
        before the local journal checkpoint is safely recoverable on re-entry.
        """
        if not confirmed:
            raise SkillMagnetError("Pull request reopen requires explicit confirmation")
        journal = self._journal()
        if str(journal.get("status")) != "published_pending":
            raise SkillMagnetError("Only a published-pending transaction can reopen its PR")
        pr_url = str(journal.get("pr_url", ""))
        if not pr_url:
            raise SkillMagnetError("Published transaction has no pull request URL")
        if str(journal.get("wait_state", "")) != "closed_unmerged":
            raise SkillMagnetError("Only a closed, unmerged pull request can be reopened")

        pull_request = self._read_pull_request(journal)
        state = str(pull_request.get("state", "")).upper()
        if state == "MERGED":
            return self._verify_merged_pull_request(journal, pull_request)
        if state == "OPEN":
            journal.update(
                wait_state="waiting_for_merge",
                pr_state="OPEN",
                reopen_recovered_at=_utc_now(),
            )
            journal.pop("reopen_attempt_started_at", None)
            self._write_journal(journal)
            return journal
        if state != "CLOSED":
            raise SkillMagnetError(
                f"GitHub returned an unknown pull request state: {state or 'empty'}"
            )

        journal["reopen_attempt_started_at"] = _utc_now()
        self._write_journal(journal)
        command_cwd = self.workspace if self.workspace.is_dir() else self.root
        self._exec(["gh", "pr", "reopen", pr_url], cwd=command_cwd)
        journal.update(
            wait_state="waiting_for_merge",
            pr_state="OPEN",
            reopened_at=_utc_now(),
        )
        journal.pop("reopen_attempt_started_at", None)
        journal.pop("failed_stage", None)
        journal.pop("last_error", None)
        self._write_journal(journal)
        return journal

    @_serialized_transaction
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
        draft, remote = self._assert_transaction_identity(
            journal, draft=draft, remote=remote
        )
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
        if status in {"verified", "activating", "menu_pending"}:
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

    @_serialized_transaction
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
        if journal["status"] not in {"verified", "activating", "menu_pending"}:
            raise SkillMagnetError("Only a remotely verified commit can be activated")
        entry_status = str(journal["status"])
        config_path = config_path.resolve()
        activation_candidate = self.root / "activation-candidate.json"
        activation_previous = self.root / "activation-previous.bin"

        if entry_status == "verified":
            remote_validation = self._remote_manifest(
                journal["remote"], journal["commit"]
            )
            if remote_validation.manifest != journal["remote_manifest"]:
                raise SkillMagnetError("Remote verification drifted before activation")
            catalog = _read_json(self.verifier / CATALOG_FILENAME)
            previous_exists = config_path.is_file()
            if config_path.exists() and not previous_exists:
                raise SkillMagnetError(
                    "Configuration path exists but is not a regular file. "
                    f"Move or rename it before retrying activation: {config_path}"
                )
            previous = config_path.read_bytes() if previous_exists else b""
            config_repaired = False
            try:
                remote_owner, _ = _parse_github_repo(str(journal["remote"]))
            except SkillMagnetError:
                # Local bare repositories are supported by the deterministic test
                # harness; product GitHub URLs take the validated branch above.
                remote_owner = "local"
            try:
                config = _read_json(config_path)
                Config.load(config_path)
            except (OSError, SkillMagnetError):
                config = {
                    "version": 1,
                    "allowed_github_owners": [remote_owner],
                    "state_dir": "~/.skill-magnet",
                    "packs": [],
                }
                config_repaired = True
            allowed_owners = config.get("allowed_github_owners")
            if not isinstance(allowed_owners, list):
                allowed_owners = []
            if remote_owner.casefold() not in {
                str(item).casefold() for item in allowed_owners
            }:
                allowed_owners = [*allowed_owners, remote_owner]
            config["allowed_github_owners"] = allowed_owners
            previous_packs = list(config.get("packs", []))
            managed_ids = set(_pack_map(catalog))
            managed_remote = str(journal["remote"])
            replaced_previous = [
                pack
                for pack in previous_packs
                if str(pack.get("id")) in managed_ids
                or (
                    bool(pack.get("repo_url"))
                    and canonical_remote_identity(str(pack.get("repo_url")))
                    == canonical_remote_identity(managed_remote)
                )
            ]
            retained = [pack for pack in previous_packs if pack not in replaced_previous]
            generated = [
                self._config_pack(pack, journal["remote"], journal["commit"])
                for pack in _pack_map(catalog).values()
            ]
            candidate = {**config, "packs": retained + generated}
            # The native direct-root shell command receives only the config
            # path. Pack, skill, and commit changes alter config bytes, not the
            # installed Explorer/Finder menu contract. Reinstall/repair the
            # native menu only through an explicit repair checkpoint.
            menu_changed = bool(journal.get("force_menu_update"))
            if menu_changed and menu_update is None:
                raise SkillMagnetError(
                    "Context-menu update is required for this activation"
                )
            _atomic_json(activation_candidate, candidate)
            Config.load(activation_candidate)
            if previous_exists:
                _atomic_bytes(activation_previous, previous)
            else:
                activation_previous.unlink(missing_ok=True)
            repair_backup = config_path.with_name(
                f"{config_path.name}.pre-repair-{self.transaction_id}.bak"
            )
            if config_repaired and previous_exists and repair_backup.exists():
                raise SkillMagnetError(
                    f"設定復旧バックアップが既に存在します: {repair_backup}"
                )
            activation = {
                "config": str(config_path),
                "previous_exists": previous_exists,
                "previous_sha256": _sha256(previous),
                "candidate_sha256": _sha256(activation_candidate.read_bytes()),
                "config_repaired": config_repaired,
                "repair_backup": str(repair_backup),
                "repair_backup_created": False,
                "menu_changed": menu_changed,
            }
            journal.update(status="activating", activation=activation)
            self._write_journal(journal)
        else:
            activation = journal.get("activation")
            if not isinstance(activation, dict):
                raise SkillMagnetError("Activation recovery metadata is missing")
            if str(config_path) != activation.get("config"):
                raise SkillMagnetError(
                    "Activation must resume with the original configuration path"
                )
            if not activation_candidate.is_file() or _sha256(
                activation_candidate.read_bytes()
            ) != activation.get("candidate_sha256"):
                raise SkillMagnetError("Activation recovery candidate is missing or changed")
            Config.load(activation_candidate)
            previous_exists = bool(activation.get("previous_exists"))
            previous = (
                activation_previous.read_bytes() if previous_exists else b""
            )
            if _sha256(previous) != activation.get("previous_sha256"):
                raise SkillMagnetError("Activation recovery baseline is missing or changed")
            config_repaired = bool(activation.get("config_repaired"))
            repair_backup = Path(str(activation.get("repair_backup")))
            menu_changed = bool(activation.get("menu_changed"))

        candidate_bytes = activation_candidate.read_bytes()
        repair_backup_created = bool(activation.get("repair_backup_created"))
        current_exists = config_path.is_file()
        if config_path.exists() and not current_exists:
            raise SkillMagnetError(
                "Configuration path changed to a non-file during activation. "
                f"Move or rename it before retrying: {config_path}"
            )
        current = config_path.read_bytes() if current_exists else b""
        current_sha256 = _sha256(current)
        previous_sha256 = str(activation["previous_sha256"])
        candidate_sha256 = str(activation["candidate_sha256"])
        allowed_current = (
            {previous_sha256}
            if entry_status == "verified"
            else {candidate_sha256}
            if entry_status == "menu_pending"
            else {previous_sha256, candidate_sha256}
        )
        current_state_allowed = (
            current_sha256 in allowed_current
            if current_exists
            else not previous_exists and previous_sha256 == _sha256(b"")
        )
        if not current_state_allowed:
            conflict_backup = config_path.with_name(
                f"{config_path.name}.activation-conflict-{self.transaction_id}.bak"
            )
            if conflict_backup.exists():
                if not conflict_backup.is_file() or _sha256(
                    conflict_backup.read_bytes()
                ) != current_sha256:
                    raise SkillMagnetError(
                        "Configuration changed after activation was interrupted, and "
                        f"the conflict backup path is already occupied: {conflict_backup}"
                    )
            else:
                _atomic_bytes(conflict_backup, current)
            previous_backup: Path | None = None
            if previous_exists:
                previous_backup = config_path.with_name(
                    f"{config_path.name}.activation-before-{self.transaction_id}.bak"
                )
                if previous_backup.exists():
                    if not previous_backup.is_file() or _sha256(
                        previous_backup.read_bytes()
                    ) != previous_sha256:
                        raise SkillMagnetError(
                            "Configuration changed after activation was interrupted, and "
                            f"the original backup path is already occupied: {previous_backup}"
                        )
                else:
                    _atomic_bytes(previous_backup, previous)
            journal.pop("activation", None)
            journal.update(
                status="verified",
                failed_stage="activation_config_conflict",
                last_error="configuration_changed_after_activation_started",
                conflict_backup=str(conflict_backup),
                previous_backup=str(previous_backup) if previous_backup else "",
                conflicted_config_was_missing=not current_exists,
                force_menu_update=True,
            )
            self._write_journal(journal)
            activation_candidate.unlink(missing_ok=True)
            activation_previous.unlink(missing_ok=True)
            raise SkillMagnetError(
                "Configuration changed after activation was interrupted; no file was "
                "overwritten. The current bytes were preserved at "
                f"{conflict_backup}. Activation was reset to the remotely verified "
                "state; retry to rebuild safely from the current configuration. "
                + (
                    f"The pre-activation configuration remains at {previous_backup}."
                    if previous_backup
                    else "The configuration did not exist before activation."
                )
            )
        try:
            if config_repaired and previous_exists:
                if repair_backup.exists():
                    if _sha256(repair_backup.read_bytes()) != _sha256(previous):
                        raise SkillMagnetError(
                            f"設定復旧バックアップが既に存在します: {repair_backup}"
                        )
                    repair_backup_created = True
                else:
                    _atomic_bytes(repair_backup, previous)
                    repair_backup_created = True
                activation["repair_backup_created"] = repair_backup_created
                journal["activation"] = activation
                self._write_journal(journal)
            _atomic_bytes(config_path, candidate_bytes)
            journal.update(
                status="menu_pending",
                activation={
                    **activation,
                    "config_applied_sha256": _sha256(config_path.read_bytes()),
                },
            )
            self._write_journal(journal)
            menu_result: Any = {"updated": False, "reason": "menu_shape_unchanged"}
            if menu_changed and menu_update is not None:
                menu_result = menu_update(config_path)
        except Exception as activation_error:
            try:
                if previous_exists:
                    _atomic_bytes(config_path, previous)
                else:
                    config_path.unlink(missing_ok=True)
            except Exception as rollback_error:
                recovery_backup = (
                    repair_backup
                    if repair_backup_created and repair_backup.is_file()
                    else activation_previous
                    if previous_exists and activation_previous.is_file()
                    else activation_candidate
                )
                journal.update(
                    status="activating",
                    failed_stage="activation_rollback",
                    last_error=str(activation_error),
                    rollback_error=str(rollback_error),
                    recovery_backup=str(recovery_backup),
                    activation=activation,
                )
                self._write_journal(journal)
                raise SkillMagnetError(
                    "Activation failed and automatic config recovery also failed. "
                    f"Recovery evidence is preserved at {recovery_backup}. "
                    f"Correct the configuration path problem, then retry: {rollback_error}"
                ) from activation_error
            if repair_backup_created:
                repair_backup.unlink(missing_ok=True)
            activation_candidate.unlink(missing_ok=True)
            activation_previous.unlink(missing_ok=True)
            journal.pop("activation", None)
            journal.update(status="verified", activation_error="rolled_back")
            self._write_journal(journal)
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
            "config_repaired": config_repaired,
            "config_repair_backup": (
                str(repair_backup) if config_repaired and previous_exists else None
            ),
            "menu": menu_result,
            "menu_changed": menu_changed,
            "test_result": "remote_manifest_verified",
            "completed_at": _utc_now(),
        }
        _atomic_json(self.receipt_path, receipt)
        journal.pop("force_menu_update", None)
        journal.update(status="active", activated_at=_utc_now(), receipt=str(self.receipt_path))
        self._write_journal(journal)
        activation_candidate.unlink(missing_ok=True)
        activation_previous.unlink(missing_ok=True)
        cleanup_pending = self.cleanup()
        if cleanup_pending:
            receipt["cleanup_pending"] = cleanup_pending
            _atomic_json(self.receipt_path, receipt)
            journal["cleanup_pending"] = cleanup_pending
            self._write_journal(journal)
        return receipt

    def status(
        self, config_path: Path | None = None, *, check_remote: bool = True
    ) -> dict[str, Any]:
        journal = self._journal()
        status = str(journal["status"])
        active_commit = ""
        config_error = ""
        if config_path is not None and config_path.exists():
            try:
                config = _read_json(config_path)
                if not isinstance(config, dict):
                    raise SkillMagnetError("Configuration root must be an object")
                managed = set(journal.get("preview", {}).get("pack_ids", []))
                commits = {
                    str(pack.get("expected_commit", ""))
                    for pack in config.get("packs", [])
                    if isinstance(pack, dict) and str(pack.get("id")) in managed
                }
                if len(commits) == 1:
                    active_commit = commits.pop()
            except (OSError, SkillMagnetError, TypeError) as exc:
                # Status is also the recovery entry point. A damaged config must
                # not hide an otherwise durable interrupted transaction.
                config_error = str(exc)
        published_commit = str(journal.get("commit", ""))
        remote_head = published_commit
        if check_remote and journal.get("remote") and journal.get("branch"):
            remote = self._exec(
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
        elif status in {
            "preparing",
            "interrupted",
            "publishing",
            "activating",
            "menu_pending",
        }:
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
                if status in {"preparing", "interrupted"} else "activate"
                if status in {"activating", "menu_pending"} else ""
            ),
            "updated_at": str(journal.get("updated_at", journal.get("created_at", ""))),
            "remote_head": remote_head,
            "verified_commit": published_commit
            if status in {"verified", "activating", "menu_pending", "active"}
            else "",
            "active_commit": active_commit,
            "config_error": config_error,
            "pack_ids": pack_ids,
            "skill_ids": skill_ids,
            "platforms": {
                "windows": dict(shared_platform_contract),
                "macos": dict(shared_platform_contract),
            },
            "platform_parity": True,
            "receipt": str(self.receipt_path) if self.receipt_path.exists() else "",
        }

    @_serialized_transaction
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

    @_serialized_transaction
    def mark_draft_unavailable(self) -> None:
        """Keep loss metadata before recreating disposable content from GitHub."""
        journal = self._journal()
        if journal["status"] not in TERMINAL_STATES:
            journal["draft_unavailable"] = True
            self._write_journal(journal)

    @_serialized_transaction
    def recover(self) -> dict[str, Any]:
        """Recover an interrupted local transaction while preserving remote state."""
        journal = self._journal()
        status = str(journal.get("status", "draft"))
        if journal.get("draft") and not Path(str(journal["draft"])).is_dir():
            journal["draft_unavailable"] = True
            self._write_journal(journal)
        resume_status = LibraryState.from_journal(journal).resume_status
        if journal.get("draft_unavailable") and journal.get("commit") and resume_status == "prepared":
            # Recheck the recorded remote commit rather than rebuilding from
            # the replacement baseline after another interrupted recovery.
            status = "publishing"
            resume_status = status
            journal["status"] = status
            self._write_journal(journal)
        if journal.get("draft_unavailable") and resume_status in {"draft", "prepared"}:
            raise SkillMagnetError(
                "一時作業領域が失われ、未送信の編集を再現できません。"
                "この作業を破棄して登録元を選び直してください。"
                "GitHubへ送信済みの可能性がある場合は、作業記録を保持してGitHubを確認してください。"
            )
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
                remote_ref = self._exec(
                    ["git", "ls-remote", "--heads", str(journal["remote"]), str(journal["branch"])],
                    check=False,
                ).stdout.strip()
                saved_commit = str(journal.get("commit", ""))
                if re.fullmatch(r"[0-9a-f]{40}", saved_commit) and remote_ref.split()[:1] == [saved_commit]:
                    self._exec(["git", "clone", "--no-hardlinks", str(journal["remote"]), str(self.workspace)])
                    self._exec(
                        ["git", "switch", "-C", str(journal["branch"]), str(journal["commit"])],
                        cwd=self.workspace,
                    )
                    journal["status"] = "prepared"
                    self._write_journal(journal)
                else:
                    if journal.get("draft_unavailable"):
                        raise SkillMagnetError(
                            "一時作業領域が失われ、送信途中のcommitをGitHubで確認できません。"
                            "公開結果を確認するため作業記録を保持しました。"
                        )
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

    @_serialized_transaction
    def abandon(self, *, confirmed: bool) -> dict[str, Any]:
        """Abandon only local work; never remove a remote branch or pull request."""
        if not confirmed:
            raise SkillMagnetError("作業の破棄には確認が必要です")
        journal = self._journal()
        previous = str(journal.get("status", "draft"))
        if not LibraryState.from_journal(journal).can_abandon:
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


def list_transactions(
    state_dir: Path,
    config_path: Path | None = None,
    *,
    check_remote: bool = True,
) -> dict[str, Any]:
    root = state_dir.resolve() / "library-transactions"
    transactions: list[dict[str, Any]] = []
    if root.is_dir():
        for journal in sorted(root.glob("*/journal.json")):
            transaction = LibraryTransaction(state_dir, journal.parent.name)
            transactions.append(transaction.status(config_path, check_remote=check_remote))
    return {"transactions": transactions}


def find_resumable_transaction(
    state_dir: Path, *, draft: Path, remote: str
) -> LibraryTransaction | None:
    """Find the newest non-terminal transaction for exactly this library and remote."""
    root = state_dir.resolve() / "library-transactions"
    if not root.is_dir():
        return None
    # A disposable draft can be absent while its durable journal remains.
    lexical_draft = Path(os.path.abspath(os.fspath(draft)))
    if _is_link(lexical_draft):
        raise SkillMagnetError("一時作業領域にリンクは使えません")
    wanted_draft = _draft_identity(lexical_draft)
    wanted_remote = canonical_remote_identity(remote)
    matches: list[tuple[str, LibraryTransaction]] = []
    for path in root.glob("*/journal.json"):
        try:
            journal = _read_json(path)
            transaction = LibraryTransaction(state_dir, path.parent.name)
            transaction._validate_journal(journal)
        except SkillMagnetError as exc:
            # A corrupt journal may belong to this exact repository. Skipping
            # it would authorize a duplicate push/PR while its remote effect is
            # unknown, so startup and new work must fail closed.
            raise SkillMagnetError(
                "GitHub反映の作業記録が壊れているため新しい処理を開始できません。"
                f"記録を別の場所に退避してから再試行できるよう、次のファイルを確認してください: {path}"
            ) from exc
        if str(journal.get("status")) in TERMINAL_STATES:
            continue
        saved_draft = str(journal.get("draft", ""))
        if not saved_draft:
            continue
        if _draft_identity(Path(saved_draft)) != wanted_draft:
            continue
        try:
            saved_remote = canonical_remote_identity(str(journal.get("remote", "")))
        except SkillMagnetError as exc:
            raise SkillMagnetError(
                f"GitHub反映の作業記録に公開先がありません: {path}"
            ) from exc
        if saved_remote != wanted_remote:
            continue
        matches.append((str(journal.get("updated_at", journal.get("created_at", ""))), transaction))
    return max(matches, key=lambda item: item[0])[1] if matches else None
