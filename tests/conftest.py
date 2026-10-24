"""Shared fixtures.

The reference implementation is read-only: these tests may import it to prove
parity, but nothing here writes to it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
REFERENCE_REPO = Path(
    os.environ.get("JOBSEARCH_REFERENCE", ".")
)


@pytest.fixture(scope="session")
def skill_dirs() -> list[Path]:
    return sorted(path for path in SKILLS.iterdir() if (path / "SKILL.md").is_file())


def make_job(index: int, *, description: str | None = None) -> dict:
    return {
        "title": f"Software Architect {index}",
        "url": f"https://www.linkedin.com/jobs/view/{index}/",
        "company": f"Company {index}",
        "location": "Cambridge, UK",
        "description": description
        or f"Job {index}. Build safety-critical medical device software in Python.",
    }


def make_result(job: dict, score: int) -> dict:
    return {
        **job,
        "domain": "MedTech",
        "salary_range": "GBP 90k-110k",
        "match_score": score,
        "reasoning": "Relevant architecture and regulated-industry experience.",
        "strengths": "Safety-critical delivery, team leadership.",
        "gaps": "No direct MRI experience.",
    }
