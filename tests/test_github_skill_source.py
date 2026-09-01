from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skill_magnet.core import Config, Engine, SafetyError


COMMIT = "a" * 40


class _ArchiveResponse(io.BytesIO):
    def __init__(self, content: bytes, url: str | None = None) -> None:
        super().__init__(content)
        self.url = url or f"https://codeload.github.com/owner/skills/tar.gz/{COMMIT}"

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> "_ArchiveResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _archive(entries: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
        if symlink is not None:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "../outside"
            bundle.addfile(info)
    return output.getvalue()


class GitHubSkillSourceTest(unittest.TestCase):
    def _engine(self, root: Path) -> tuple[Engine, object]:
        config_path = root / "skill-magnet.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "allowed_github_owners": ["owner"],
                    "state_dir": str(root / "state"),
                    "packs": [
                        {
                            "id": "pack",
                            "repo_url": "https://github.com/owner/skills.git",
                            "expected_commit": COMMIT,
                            "approved_by": "user",
                            "approved_at": "2026-08-30T00:00:00+00:00",
                            "skills": ["one-skill"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        config = Config.load(config_path)
        return Engine(config), config.packs["pack"]

    def test_pinned_github_archive_is_verified_only_in_memory(self) -> None:
        archive = _archive(
            {
                "skills-root/INDEX.md": b"# Index\n",
                "skills-root/one-skill/SKILL.md": (
                    b"---\nname: one-skill\ndescription: test\n---\n# Body\n"
                ),
                "skills-root/one-skill/acceptance.json": b'{"version":1}',
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine, pack = self._engine(root)
            with mock.patch(
                "urllib.request.urlopen", return_value=_ArchiveResponse(archive)
            ) as opened:
                commit, hashes = engine._validate_pack(pack)
            self.assertEqual(commit, COMMIT)
            self.assertEqual(set(hashes), {"one-skill"})
            self.assertEqual(
                opened.call_args.args[0].full_url,
                f"https://codeload.github.com/owner/skills/tar.gz/{COMMIT}",
            )
            self.assertFalse((root / "state").exists())
            self.assertEqual(
                sorted(path.name for path in root.iterdir()), ["skill-magnet.json"]
            )

    def test_index_is_optional(self) -> None:
        archive = _archive(
            {
                "skills-root/one-skill/SKILL.md": (
                    b"---\nname: one-skill\ndescription: test\n---\n# Body\n"
                ),
                "skills-root/one-skill/acceptance.json": b'{"version":1}',
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            engine, pack = self._engine(Path(temporary))
            with mock.patch(
                "urllib.request.urlopen", return_value=_ArchiveResponse(archive)
            ):
                commit, hashes = engine._validate_pack(pack)
            self.assertEqual(commit, COMMIT)
            self.assertEqual(set(hashes), {"one-skill"})

    def test_archive_path_traversal_is_rejected(self) -> None:
        archive = _archive({"skills-root/../outside": b"bad"})
        with tempfile.TemporaryDirectory() as temporary:
            engine, pack = self._engine(Path(temporary))
            with (
                mock.patch(
                    "urllib.request.urlopen", return_value=_ArchiveResponse(archive)
                ),
                self.assertRaisesRegex(SafetyError, "Unsafe path"),
            ):
                engine._validate_pack(pack)

    def test_archive_links_are_rejected(self) -> None:
        archive = _archive(
            {"skills-root/INDEX.md": b"# Index\n"},
            symlink="skills-root/one-skill/SKILL.md",
        )
        with tempfile.TemporaryDirectory() as temporary:
            engine, pack = self._engine(Path(temporary))
            with (
                mock.patch(
                    "urllib.request.urlopen", return_value=_ArchiveResponse(archive)
                ),
                self.assertRaisesRegex(SafetyError, "Links are not allowed"),
            ):
                engine._validate_pack(pack)

    def test_archive_redirect_is_rejected(self) -> None:
        archive = _archive({"skills-root/INDEX.md": b"# Index\n"})
        with tempfile.TemporaryDirectory() as temporary:
            engine, pack = self._engine(Path(temporary))
            with (
                mock.patch(
                    "urllib.request.urlopen",
                    return_value=_ArchiveResponse(
                        archive, "https://example.invalid/untrusted.tar.gz"
                    ),
                ),
                self.assertRaisesRegex(SafetyError, "redirected"),
            ):
                engine._validate_pack(pack)


if __name__ == "__main__":
    unittest.main()
