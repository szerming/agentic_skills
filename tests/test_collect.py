"""End-to-end collect wiring, with the browser faked.

No network, no Playwright. This pins the behaviour the whole design exists for:
dedupe across search terms, and a second run that refetches nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import linkedin
from job_models import JobDescription, JobEntry
from jobsearch import main

ENTRIES = {
    "Software Architect": [
        JobEntry(
            title="Architect",
            url="https://www.linkedin.com/jobs/view/1/",
            company="A",
            location="Cambridge",
        ),
        JobEntry(
            title="Engineer",
            url="https://www.linkedin.com/jobs/view/2/",
            company="B",
            location="Cambridge",
        ),
    ],
    "Head of Software": [
        # Deliberate duplicate of the first term's result: dedupe is the point.
        JobEntry(
            title="Architect (again)",
            url="https://www.linkedin.com/jobs/view/1/",
            company="A",
            location="Cambridge",
        ),
        JobEntry(
            title="Director",
            url="https://www.linkedin.com/jobs/view/3/",
            company="C",
            location="London",
        ),
    ],
}


class FakeBrowser:
    """Stands in for LinkedInBrowser. Counts description fetches."""

    description_calls = 0

    def __init__(self, config):
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def require_session(self):
        return None

    async def fetch_search(self, keyword, past_days=7):
        return list(ENTRIES.get(keyword, []))

    async def fetch_recommended(self):
        return []

    async def fetch_listing(self, url):
        return []

    async def get_description(self, entry, save_html=None):
        type(self).description_calls += 1
        return (
            JobDescription(description=f"Full text for {entry.url}", **entry.model_dump()),
            None,
        )


class CrashingBrowser(FakeBrowser):
    """Fails on the Nth description fetch, after the earlier ones were saved."""

    description_calls = 0
    crash_on = 3

    async def get_description(self, entry, save_html=None):
        type(self).description_calls += 1
        if type(self).description_calls == type(self).crash_on:
            raise RuntimeError("page crashed")
        return (
            JobDescription(description=f"Full text for {entry.url}", **entry.model_dump()),
            None,
        )


def make_workspace(tmp_path: Path, terms: list[str]) -> Path:
    home = tmp_path / "resource"
    home.mkdir()
    (home / "search_terms.json").write_text(json.dumps(terms), encoding="utf-8")
    return home


def collect(monkeypatch, home: Path, *extra) -> dict:
    monkeypatch.setattr(linkedin, "LinkedInBrowser", FakeBrowser)
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        main(["collect", "--home", str(home), "--source", "terms", "--delay", "0", *extra])
    return json.loads(buffer.getvalue())


def test_collect_dedupes_terms_and_caches_between_runs(tmp_path, monkeypatch):
    home = make_workspace(tmp_path, ["Software Architect", "Head of Software"])
    FakeBrowser.description_calls = 0

    first = collect(monkeypatch, home)

    assert first["count"] == 3, "the duplicate URL across two terms was not deduped"
    assert first["scraped"] == 3
    assert first["cached"] == 0
    assert FakeBrowser.description_calls == 3

    descriptions = json.loads((Path(first["descriptions"])).read_text())
    assert [job["url"] for job in descriptions["jobs"]] == [
        "https://www.linkedin.com/jobs/view/1/",
        "https://www.linkedin.com/jobs/view/2/",
        "https://www.linkedin.com/jobs/view/3/",
    ]
    assert descriptions["jobs"][0]["title"] == "Architect", "the first occurrence should win"

    second = collect(monkeypatch, home)

    assert second["count"] == 3
    assert second["cached"] == 3, "the cache was not used on the second run"
    assert second["scraped"] == 0
    assert FakeBrowser.description_calls == 3, "a cached description was fetched again"
    assert second["run"] != first["run"], "each collect must get its own run directory"


def test_collect_records_provenance(tmp_path, monkeypatch):
    home = make_workspace(tmp_path, ["Software Architect"])

    payload = collect(monkeypatch, home, "--past-days", "30")

    metadata = json.loads((Path(payload["run"]) / "run.json").read_text())
    assert metadata["source"] == "terms"
    assert metadata["past_days"] == 30
    assert metadata["terms"] == ["Software Architect"]
    assert metadata["count"] == 2
    assert metadata["descriptions_sha256"]


def test_collect_persists_progress_when_a_later_fetch_crashes(tmp_path, monkeypatch):
    import pytest

    home = make_workspace(tmp_path, ["Software Architect", "Head of Software"])
    monkeypatch.setattr(linkedin, "LinkedInBrowser", CrashingBrowser)
    CrashingBrowser.description_calls = 0

    with pytest.raises(SystemExit) as excinfo:
        main(["collect", "--home", str(home), "--source", "terms", "--delay", "0"])
    assert excinfo.value.code == 3

    runs = sorted((home / "runs").iterdir())
    assert len(runs) == 1, "a crash after a fetch must keep the partial run directory"
    partial = json.loads((runs[0] / "descriptions.json").read_text())
    assert [job["url"] for job in partial["jobs"]] == [
        "https://www.linkedin.com/jobs/view/1/",
        "https://www.linkedin.com/jobs/view/2/",
    ], "the two descriptions fetched before the crash were not persisted"

    # The partial output and the cache mean a retry only fetches the missing job.
    monkeypatch.setattr(linkedin, "LinkedInBrowser", FakeBrowser)
    FakeBrowser.description_calls = 0

    second = collect(monkeypatch, home)

    assert second["cached"] == 2
    assert second["scraped"] == 1
    assert FakeBrowser.description_calls == 1, "a job saved before the crash was fetched again"


def test_collect_fails_without_search_terms(tmp_path, monkeypatch):
    home = tmp_path / "resource"
    home.mkdir()
    monkeypatch.setattr(linkedin, "LinkedInBrowser", FakeBrowser)

    import pytest

    with pytest.raises(SystemExit) as excinfo:
        main(["collect", "--home", str(home), "--source", "terms"])
    assert excinfo.value.code == 3
