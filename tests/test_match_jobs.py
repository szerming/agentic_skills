"""Chunking, contract enforcement and report rendering. All offline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_job, make_result
from jobmatch import main


def write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def run(tmp_path) -> Path:
    """A workspace with a run, three jobs and a profile, ready to prepare."""
    home = tmp_path / "resource"
    home.mkdir()
    write(
        home / "profile.json",
        {
            "headline": "Principal software architect, medical devices",
            "seniority": "principal",
            "target_titles": ["Software Architect"],
        },
    )
    run_dir = home / "runs" / "run-1"
    write(run_dir / "run.json", {"created": "2026-10-24T00:00:00Z", "source": "terms", "count": 3})
    write(run_dir / "descriptions.json", {"jobs": [make_job(i) for i in range(1, 4)]})
    return run_dir


def invoke(*argv: str) -> None:
    main(list(argv))


def test_prepare_splits_into_chunks(run: Path, capsys) -> None:
    invoke("prepare", "--run", str(run), "--home", str(run.parents[1]), "--size", "2")
    chunks = sorted((run / "chunks").glob("chunk-*.json"))
    assert [path.name for path in chunks] == ["chunk-01.json", "chunk-02.json"]

    first = json.loads(chunks[0].read_text())
    assert first["index"] == 1 and first["total"] == 2 and len(first["jobs"]) == 2
    assert len(json.loads(chunks[1].read_text())["jobs"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["pending"] == [1, 2]
    assert payload["jobs"] == 3


def test_prepare_status_does_not_write(run: Path) -> None:
    invoke("prepare", "--run", str(run), "--home", str(run.parents[1]), "--status")
    assert not (run / "chunks").exists()


def test_report_refuses_an_unscored_run(run: Path) -> None:
    invoke("prepare", "--run", str(run), "--home", str(run.parents[1]))
    with pytest.raises(SystemExit) as excinfo:
        invoke("report", "--run", str(run), "--home", str(run.parents[1]))
    assert excinfo.value.code == 3


def test_report_refuses_a_partially_scored_run(run: Path) -> None:
    invoke("prepare", "--run", str(run), "--home", str(run.parents[1]), "--size", "2")
    chunk = json.loads((run / "chunks" / "chunk-01.json").read_text())
    write(
        run / "matches" / "chunk-01.json",
        {
            "chunk": 1,
            "match_results": [make_result(job, 88) for job in chunk["jobs"]],
        },
    )

    with pytest.raises(SystemExit) as excinfo:
        invoke("report", "--run", str(run), "--home", str(run.parents[1]))
    assert excinfo.value.code == 3
    assert not (run / "report.md").exists()


def test_report_rejects_a_chunk_that_skipped_a_job(run: Path) -> None:
    invoke("prepare", "--run", str(run), "--home", str(run.parents[1]), "--size", "2")
    chunk = json.loads((run / "chunks" / "chunk-01.json").read_text())
    write(
        run / "matches" / "chunk-01.json",
        {"chunk": 1, "match_results": [make_result(chunk["jobs"][0], 88)]},
    )
    with pytest.raises(SystemExit) as excinfo:
        invoke("report", "--run", str(run), "--home", str(run.parents[1]))
    assert excinfo.value.code == 3


def test_report_writes_every_score_and_filters_the_markdown(run: Path, capsys) -> None:
    invoke("prepare", "--run", str(run), "--home", str(run.parents[1]), "--size", "3")
    chunk = json.loads((run / "chunks" / "chunk-01.json").read_text())
    scores = [91, 75, 39]
    write(
        run / "matches" / "chunk-01.json",
        {
            "chunk": 1,
            "match_results": [make_result(job, score) for job, score in zip(chunk["jobs"], scores)],
        },
    )

    capsys.readouterr()
    invoke("report", "--run", str(run), "--home", str(run.parents[1]), "--min-score", "75")
    payload = json.loads(capsys.readouterr().out)

    assert payload["scored"] == 3
    assert payload["reported"] == 2
    assert payload["bands"]["excellent_90+"] == 1
    assert payload["bands"]["poor_<40"] == 1

    all_matches = json.loads((run / "matches.all.json").read_text())
    assert [result["match_score"] for result in all_matches["match_results"]] == [91, 75, 39]

    report = (run / "report.md").read_text()
    assert "Software Architect 1" in report
    assert "Software Architect 2" in report
    assert "Software Architect 3" not in report
    assert report.index("Software Architect 1") < report.index("Software Architect 2")


def test_fit_renders_one_result(run: Path, tmp_path, capsys) -> None:
    path = write(tmp_path / "one.json", make_result(make_job(9), 83))
    invoke("fit", "--match", str(path))
    output = capsys.readouterr().out
    assert "## Software Architect 9 @ Company 9" in output
    assert "83%" in output


def test_fit_rejects_an_invalid_result(tmp_path) -> None:
    path = write(tmp_path / "bad.json", {"title": "x"})
    with pytest.raises(SystemExit) as excinfo:
        invoke("fit", "--match", str(path))
    assert excinfo.value.code == 3


def test_schema_is_available_without_a_workspace(capsys) -> None:
    invoke("schema", "--name", "match-batch")
    payload = json.loads(capsys.readouterr().out)
    assert payload["title"] == "MatchChunk"


def _score_all(run: Path, scores: list[int], home: Path) -> None:
    invoke("prepare", "--run", str(run), "--home", str(home), "--size", "100")
    chunk = json.loads((run / "chunks" / "chunk-01.json").read_text())
    write(
        run / "matches" / "chunk-01.json",
        {
            "chunk": 1,
            "match_results": [make_result(job, score) for job, score in zip(chunk["jobs"], scores)],
        },
    )


def test_report_saves_scores_to_the_database(run: Path, capsys) -> None:
    home = run.parents[1]
    _score_all(run, [91, 75, 39], home)

    capsys.readouterr()
    invoke("report", "--run", str(run), "--home", str(home), "--min-score", "75")
    payload = json.loads(capsys.readouterr().out)
    assert payload["saved"] == 3
    assert Path(payload["database"]).is_file()

    capsys.readouterr()
    invoke("lookup", "--home", str(home), "--query", "architect", "--min-score", "75")
    lookup = json.loads(capsys.readouterr().out)
    assert lookup["count"] == 2
    assert [row["match_score"] for row in lookup["matches"]] == [91, 75]
    assert lookup["total_matches"] == 2


def test_lookup_by_url_ignores_tracking_parameters(run: Path, capsys) -> None:
    home = run.parents[1]
    _score_all(run, [91, 75, 39], home)
    invoke("report", "--run", str(run), "--home", str(home))

    capsys.readouterr()
    invoke(
        "lookup",
        "--home",
        str(home),
        "--url",
        "https://www.linkedin.com/jobs/view/2/?refId=abc",
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["matches"][0]["title"] == "Software Architect 2"
    assert "description" not in payload["matches"][0]
    assert len(payload["matches"][0]["reasoning"]) > 0


def test_lookup_lists_cached_but_unscored_jobs(run: Path, capsys) -> None:
    from job_models import JobDescription
    from jobsearch import PydanticStore

    home = run.parents[1]
    _score_all(run, [91], home)
    # The fixture only writes three jobs into the run; cache a fourth description.
    store = PydanticStore(home / "jobs.db", JobDescription)
    extra = make_job(42)
    store.save(extra["url"], JobDescription(**extra))

    capsys.readouterr()
    invoke("lookup", "--home", str(home))
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_matches"] == 0
    assert [row["title"] for row in payload["unscored"]] == ["Software Architect 42"]
    assert payload["unscored"][0]["cached_at"]


def test_lookup_is_empty_not_an_error_on_a_fresh_workspace(tmp_path, capsys) -> None:
    invoke("lookup", "--home", str(tmp_path / "nothing"), "--query", "architect")
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["count"] == 0
    assert payload["matches"] == [] and payload["unscored"] == []
    assert "job-search" in payload["note"]


def test_fit_can_save_a_single_result(tmp_path, capsys) -> None:
    home = tmp_path / "resource"
    path = write(tmp_path / "one.json", make_result(make_job(9), 83))
    invoke("fit", "--match", str(path), "--home", str(home), "--save")
    capsys.readouterr()

    invoke("lookup", "--home", str(home), "--url", "https://www.linkedin.com/jobs/view/9/")
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["matches"][0]["match_score"] == 83
