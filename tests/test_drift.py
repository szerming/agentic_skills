"""The shared modules must stay byte-identical across the standalone skills.

Each skill folder has to work when copied alone, so job_models.py and
job_workspace.py are duplicated rather than packaged. Duplication is only safe if
divergence is loud; that is this test's entire job.
"""

from __future__ import annotations

import hashlib

from conftest import SKILLS

SHARED_MODULES = ("job_models.py", "job_workspace.py")


def test_shared_modules_are_identical() -> None:
    for name in SHARED_MODULES:
        copies = sorted(SKILLS.glob(f"*/scripts/{name}"))
        assert len(copies) == 3, f"expected 3 copies of {name}, found {len(copies)}"
        digests = {hashlib.sha256(path.read_bytes()).hexdigest() for path in copies}
        assert len(digests) == 1, (
            f"{name} has diverged between skills:\n"
            + "\n".join(str(path) for path in copies)
            + "\nRe-copy the canonical file from skills/match-jobs/scripts/."
        )


def test_every_skill_has_frontmatter() -> None:
    for skill_dir in sorted(path for path in SKILLS.iterdir() if path.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.is_file(), f"{skill_dir.name} has no SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{skill_md} does not start with YAML frontmatter"
        frontmatter = text.split("---", 2)[1]
        assert "name:" in frontmatter
        assert "description:" in frontmatter
        assert f"name: {skill_dir.name}" in frontmatter, (
            f"{skill_md} declares a name that does not match its folder"
        )
