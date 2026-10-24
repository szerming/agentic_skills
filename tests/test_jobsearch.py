"""Deterministic parts of the job-search skill need no browser and no network."""

from __future__ import annotations

import pytest
from job_models import JobDescription, JobEntry
from jobsearch import PydanticStore, combine_terms, dedupe_entries, load_search_terms
from linkedin import build_search_url, with_page


def test_build_search_url_encodes_filters() -> None:
    url = build_search_url("Software Architect", past_days=7)
    assert url.startswith("https://www.linkedin.com/jobs/search/?")
    assert "keywords=Software+Architect" in url
    assert "geoId=102057244" in url
    assert "f_TPR=r604800" in url
    assert "f_E=4,5,6" in url
    assert "f_WT=1,2,3" in url
    assert "sortBy=DD" in url


def test_build_search_url_accepts_boolean_expressions() -> None:
    url = build_search_url('("Solution Architect") OR ("System Architect")')
    assert "(" in url and ")" in url
    assert "%22Solution+Architect%22" in url


def test_past_days_is_floored_at_one_day() -> None:
    assert "f_TPR=r86400" in build_search_url("x", past_days=0)


def test_with_page_handles_every_url_shape() -> None:
    assert with_page("https://example.com/search?keywords=x", 2).endswith("&start=50")
    assert with_page("https://example.com/collections/recommended/?", 1).endswith("start=25")
    assert with_page("https://example.com/search/", 0).endswith("?start=0")


def test_combine_terms_ors_each_term() -> None:
    assert combine_terms(["a", "b"]) == "(a) or (b)"


def test_dedupe_entries_keeps_first_occurrence() -> None:
    first = JobEntry(title="A", url="u1", company="c", location="l")
    dup = JobEntry(title="A (again)", url="u1", company="c", location="l")
    second = JobEntry(title="B", url="u2", company="c", location="l")
    assert [entry.url for entry in dedupe_entries([first, dup, second])] == ["u1", "u2"]
    assert dedupe_entries([first, dup, second])[0].title == "A"


def test_load_search_terms_rejects_non_arrays(tmp_path) -> None:
    path = tmp_path / "terms.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        load_search_terms(path)
    assert excinfo.value.code == 3


def test_load_search_terms_reads_a_bare_array(tmp_path) -> None:
    path = tmp_path / "terms.json"
    path.write_text('["Software Architect", "Head of Software"]', encoding="utf-8")
    assert load_search_terms(path) == ["Software Architect", "Head of Software"]


def test_store_round_trip_and_prune(tmp_path) -> None:
    store = PydanticStore(tmp_path / "jobs.db", JobDescription)
    job = JobDescription(
        title="Architect",
        url="https://www.linkedin.com/jobs/view/1/",
        company="Acme",
        location="Cambridge",
        description="Build things.",
    )
    assert store.get(job.url) is None
    store.save(job.url, job)
    assert store.exists(job.url)
    assert store.get(job.url) == job
    assert store.count() == 1
    assert store.prune(older_than_days=0) == 1
    assert store.count() == 0


# -- candidate layout --------------------------------------------------------


def test_candidate_layout_scopes_candidate_state(tmp_path) -> None:
    from job_workspace import Workspace

    home = tmp_path / "resource"
    workspace = Workspace(home, "alice").ensure()

    assert workspace.candidate_dir == home / "candidates" / "alice"
    assert workspace.cv_dir == home / "candidates" / "alice" / "cv"
    assert workspace.profile == home / "candidates" / "alice" / "profile.json"
    assert workspace.search_terms == home / "candidates" / "alice" / "search_terms.json"
    assert workspace.db == home / "candidates" / "alice" / "jobs.db"
    assert workspace.runs == home / "candidates" / "alice" / "runs"
    # The LinkedIn session is account-level, not candidate-level.
    assert workspace.session == home / "session"


def test_store_cv_copies_into_the_candidate_without_duplicating(tmp_path) -> None:
    from job_workspace import Workspace

    workspace = Workspace(tmp_path / "resource", "alice").ensure()
    source = tmp_path / "cv.pdf"
    source.write_bytes(b"%PDF-1.4 candidate")

    stored = workspace.store_cv(source)
    assert stored == workspace.cv_dir / "cv.pdf"
    assert stored.read_bytes() == b"%PDF-1.4 candidate"
    assert workspace.store_cv(source) == stored, "a re-run must not create cv-2.pdf"
    assert [path.name for path in workspace.cvs] == ["cv.pdf"]


def test_single_candidate_is_discovered(tmp_path) -> None:
    from job_workspace import Workspace, resolve_workspace

    home = tmp_path / "resource"
    Workspace(home, "bob").ensure()
    assert resolve_workspace(home).candidate == "bob"


def test_two_candidates_require_an_explicit_choice(tmp_path) -> None:
    from job_workspace import Workspace, WorkspaceError, resolve_workspace

    home = tmp_path / "resource"
    Workspace(home, "alice").ensure()
    Workspace(home, "bob").ensure()
    with pytest.raises(WorkspaceError):
        resolve_workspace(home)
    assert resolve_workspace(home, "bob").candidate == "bob"


def test_flat_layout_is_still_honoured(tmp_path) -> None:
    from job_workspace import resolve_workspace

    home = tmp_path / "resource"
    home.mkdir()
    (home / "profile.json").write_text("{}", encoding="utf-8")
    workspace = resolve_workspace(home)
    assert workspace.candidate is None
    assert workspace.profile == home / "profile.json"
    assert workspace.db == home / "jobs.db"


def test_candidate_name_is_slugified(tmp_path) -> None:
    from job_workspace import Workspace, slugify

    assert slugify("Alex Mercer") == "alex-mercer"
    assert Workspace(tmp_path, "Alex Mercer").candidate_dir.name == "alex-mercer"
