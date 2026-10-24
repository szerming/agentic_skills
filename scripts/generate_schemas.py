#!/usr/bin/env -S uv run python
"""Regenerate the committed JSON Schemas from the pydantic models.

The schemas under each skill's reference/ folder are the human-readable contract.
`tests/test_schemas.py` fails when they fall behind the models, so run this after
changing job_models.py:

    uv run python scripts/generate_schemas.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "match-jobs" / "scripts"))

from job_models import CandidateProfile, JobDescriptionList, MatchChunk  # noqa: E402

SCHEMAS = {
    ROOT
    / "skills"
    / "candidate-profile"
    / "reference"
    / "candidate-profile.schema.json": CandidateProfile,
    ROOT
    / "skills"
    / "job-search"
    / "reference"
    / "description-list.schema.json": JobDescriptionList,
    ROOT / "skills" / "match-jobs" / "reference" / "match-batch.schema.json": MatchChunk,
}


def main() -> None:
    for path, model in SCHEMAS.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(),
        }
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
