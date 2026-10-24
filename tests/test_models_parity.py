"""Pin the contract to the reference implementation.

`to_markdown` is the one place where a drift would silently change what the user
sees in every report, so it is compared byte for byte against
agentic_workflow's models.py. That repo is imported, never modified.
"""

from __future__ import annotations

import importlib.util

import pytest
from conftest import REFERENCE_REPO, make_job, make_result
from job_models import MatchResult


@pytest.fixture(scope="module")
def reference_models():
    path = REFERENCE_REPO / "src" / "agentic_workflows" / "models" / "models.py"
    if not path.is_file():
        pytest.skip(f"reference implementation not found at {path}")
    spec = importlib.util.spec_from_file_location("reference_job_models", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_match_result_fields_match_reference(reference_models) -> None:
    ours = set(MatchResult.model_fields)
    theirs = set(reference_models.MatchResult.model_fields)
    assert ours == theirs


def test_to_markdown_is_byte_identical(reference_models) -> None:
    for score in (95, 80, 62, 41, 12):
        payload = make_result(make_job(1), score)
        ours = MatchResult.model_validate(payload).to_markdown()
        theirs = reference_models.MatchResult.model_validate(payload).to_markdown()
        assert ours == theirs, f"to_markdown diverged at score {score}"


def test_candidate_profile_fields_match_reference(reference_models) -> None:
    from job_models import CandidateProfile

    assert set(CandidateProfile.model_fields) == set(reference_models.CandidateProfile.model_fields)


def test_openapi_is_not_needed_but_schema_is_valid() -> None:
    schema = MatchResult.model_json_schema()
    assert set(schema["required"]) >= {"title", "url", "company", "match_score", "reasoning"}
