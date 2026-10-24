"""The committed schemas are documentation; they must not drift from the models."""

from __future__ import annotations

import json

import pytest
from conftest import ROOT
from job_models import CandidateProfile, JobDescriptionList, MatchChunk

SCHEMAS = {
    "skills/candidate-profile/reference/candidate-profile.schema.json": CandidateProfile,
    "skills/job-search/reference/description-list.schema.json": JobDescriptionList,
    "skills/match-jobs/reference/match-batch.schema.json": MatchChunk,
}


@pytest.mark.parametrize("relative_path,model", SCHEMAS.items())
def test_committed_schema_is_current(relative_path: str, model) -> None:
    path = ROOT / relative_path
    assert path.is_file(), f"{relative_path} is missing"
    committed = json.loads(path.read_text(encoding="utf-8"))
    committed.pop("$schema", None)
    assert committed == model.model_json_schema(), (
        f"{relative_path} is stale. Run: uv run python scripts/generate_schemas.py"
    )
