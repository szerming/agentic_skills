#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.12"]
# ///
"""Prepare, validate and render job matching runs, and look up saved results.

This script contains only the deterministic parts of matching: slicing a run into
chunks, enforcing the JSON contract, merging results, rendering the Markdown
report, and remembering scores. The judgement (scores, reasoning, strengths,
gaps) belongs to the model reading reference/matching-rubric.md.

Scores are persisted in the candidate's `jobs.db`, next to the descriptions the
job-search skill caches. `lookup` answers questions about jobs that have already
been found or scored without re-reading a run or re-scoring anything.

Every command prints JSON on stdout, except `fit` which prints the rendered
Markdown for one job. Exit codes:
  0 ok
  2 configuration or usage problem
  3 the run is incomplete or does not satisfy the contract
"""

from __future__ import annotations

import sys

# The skills are installed read-only in some harnesses; importing the sibling
# modules must not drop __pycache__ inside the skill directory.
sys.dont_write_bytecode = True

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from job_models import (
    CandidateProfile,
    ChunkEnvelope,
    JobDescriptionList,
    MatchChunk,
    MatcherOutput,
    MatchResult,
)
from job_workspace import (
    EXIT_CONFIG,
    Workspace,
    add_common_arguments,
    add_init_subcommand,
    emit,
    fail,
    workspace_from_args,
)
from pydantic import ValidationError

EXIT_TASK_FAILED = 3


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# -- IO ----------------------------------------------------------------------


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_model(path: Path, model, label: str):
    """Parse a file into a model, turning every failure into one actionable message."""
    if not path.is_file():
        fail(f"{label} not found at {path}.", code=EXIT_TASK_FAILED)
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        fail(
            f"{path} does not match {model.__name__}.",
            code=EXIT_TASK_FAILED,
            errors=exc.errors(include_url=False),
        )


# -- saved results -----------------------------------------------------------

# job-search writes descriptions to `model_store`. Match scores get their own
# table in the same database, so one file answers "what have we already seen?"
# and "what did it score?". Both tables share the key/data/updated_at shape.
MATCH_TABLE = "match_results"
DESCRIPTION_TABLE = "model_store"


def connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db_path)


def ensure_match_table(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {MATCH_TABLE} (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def save_matches(db_path: Path, results: list[MatchResult]) -> int:
    """Upsert one row per job URL. Returns how many rows were written."""
    ensure_match_table(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conn.executemany(
            f"""
            INSERT INTO {MATCH_TABLE} (key, data, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                data=excluded.data,
                updated_at=excluded.updated_at
            """,
            [(result.url, result.model_dump_json(), now) for result in results],
        )
    return len(results)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def load_saved(db_path: Path) -> tuple[list[dict], list[dict]]:
    """Read (match rows, cached-but-unscored description rows) out of jobs.db.

    Rows are dicts so `lookup` can trim a full description without re-validating
    a model. A missing database, or a database the job-search skill has not
    written to yet, simply has nothing saved.
    """
    if not db_path.is_file():
        return [], []

    matches: list[dict] = []
    descriptions: list[dict] = []
    with connect(db_path) as conn:
        if _table_exists(conn, MATCH_TABLE):
            for data, updated_at in conn.execute(f"SELECT data, updated_at FROM {MATCH_TABLE}"):
                try:
                    matches.append({**json.loads(data), "scored_at": updated_at})
                except json.JSONDecodeError:
                    continue

        scored = {row["url"] for row in matches}
        if _table_exists(conn, DESCRIPTION_TABLE):
            for data, updated_at in conn.execute(
                f"SELECT data, updated_at FROM {DESCRIPTION_TABLE}"
            ):
                try:
                    description = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if description.get("url") in scored:
                    continue
                descriptions.append({**description, "cached_at": updated_at})
    return matches, descriptions


def normalize_url(url: str) -> str:
    """Compare job URLs without LinkedIn's tracking query string or a trailing slash."""
    return url.split("?", 1)[0].split("#", 1)[0].rstrip("/").casefold()


def _matches_filters(row: dict, args) -> bool:
    if args.url:
        wanted = normalize_url(args.url)
        candidate = normalize_url(row.get("url", ""))
        if wanted != candidate and wanted not in candidate:
            return False
    if args.query:
        haystack = " ".join(
            str(row.get(field, "")) for field in ("title", "company", "location", "domain")
        ).casefold()
        if args.query.casefold() not in haystack:
            return False
    return True


def _summary(row: dict) -> dict:
    fields = (
        "title",
        "url",
        "company",
        "location",
        "domain",
        "salary_range",
        "match_score",
        "reasoning",
        "strengths",
        "gaps",
    )
    summary = {field: row.get(field) for field in fields}
    for field in ("scored_at", "cached_at"):
        if field in row:
            summary[field] = row[field]
    return summary


# -- run layout --------------------------------------------------------------


def chunk_path(run: Path, index: int) -> Path:
    return run / "chunks" / f"chunk-{index:02d}.json"


def match_path(run: Path, index: int) -> Path:
    return run / "matches" / f"chunk-{index:02d}.json"


def load_chunks(run: Path) -> list[ChunkEnvelope]:
    chunk_dir = run / "chunks"
    if not chunk_dir.is_dir():
        fail(
            f"No chunks in {chunk_dir}.",
            code=EXIT_TASK_FAILED,
            fix="Run prepare first.",
        )
    envelopes = []
    for path in sorted(chunk_dir.glob("chunk-*.json")):
        envelopes.append(load_model(path, ChunkEnvelope, "chunk"))
    if not envelopes:
        fail(f"No chunk files in {chunk_dir}.", code=EXIT_TASK_FAILED, fix="Run prepare first.")
    return envelopes


def chunk_state(run: Path, envelope: ChunkEnvelope) -> tuple[str, str | None]:
    """pending | scored | invalid. The reason is returned for invalid chunks."""
    path = match_path(run, envelope.index)
    if not path.is_file():
        return "pending", None
    try:
        match_chunk = MatchChunk.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError) as exc:
        return "invalid", f"{type(exc).__name__}: {exc}"

    if match_chunk.chunk != envelope.index:
        return "invalid", f"chunk says {match_chunk.chunk}, expected {envelope.index}"

    expected = {job.url for job in envelope.jobs}
    produced = {result.url for result in match_chunk.match_results}

    if missing := expected - produced:
        titles = {job.url: job.title for job in envelope.jobs}
        named = [f"{titles[url]!r} ({url})" for url in sorted(missing)]
        shown = "; ".join(named[:3])
        if len(named) > 3:
            shown += f"; and {len(named) - 3} more"
        return "invalid", f"{len(missing)} job(s) not scored: {shown}"
    if extra := produced - expected:
        return "invalid", f"{len(extra)} result(s) for jobs not in this chunk"
    if len(produced) != len(match_chunk.match_results):
        return "invalid", "duplicate URLs in match_results"
    return "scored", None


# -- commands ----------------------------------------------------------------


def run_prepare(args) -> dict:
    workspace: Workspace = workspace_from_args(args)
    run = workspace.run_dir(args.run)
    if not run.is_dir():
        fail(
            f"Run directory {run} does not exist.",
            code=EXIT_TASK_FAILED,
            fix="Run the job-search skill first.",
        )

    descriptions_path = (
        Path(args.descriptions).expanduser() if args.descriptions else run / "descriptions.json"
    )
    profile_path = Path(args.profile).expanduser() if args.profile else workspace.profile

    descriptions = load_model(descriptions_path, JobDescriptionList, "descriptions")
    load_model(profile_path, CandidateProfile, "profile")  # validate only; scoring uses it live

    if not descriptions.jobs:
        fail(f"{descriptions_path} contains no jobs.", code=EXIT_TASK_FAILED)

    size = max(1, args.size)
    groups = [descriptions.jobs[i : i + size] for i in range(0, len(descriptions.jobs), size)]

    chunks = []
    for index, group in enumerate(groups, start=1):
        path = chunk_path(run, index)
        if not args.status:
            envelope = ChunkEnvelope(index=index, total=len(groups), jobs=group)
            write_json(path, envelope.model_dump(mode="json"))
        state, reason = chunk_state(run, ChunkEnvelope(index=index, total=len(groups), jobs=group))
        entry = {"index": index, "path": str(path), "jobs": len(group), "status": state}
        if reason:
            entry["reason"] = reason
        chunks.append(entry)

    pending = [chunk["index"] for chunk in chunks if chunk["status"] != "scored"]
    return {
        "ok": True,
        "run": str(run),
        "descriptions": str(descriptions_path),
        "profile": str(profile_path),
        "jobs": len(descriptions.jobs),
        "chunk_size": size,
        "chunks": chunks,
        "pending": pending,
    }


def run_report(args) -> dict:
    workspace: Workspace = workspace_from_args(args)
    run = workspace.run_dir(args.run)
    if not run.is_dir():
        fail(f"Run directory {run} does not exist.", code=EXIT_TASK_FAILED)

    envelopes = load_chunks(run)

    pending: list[dict] = []
    scored: list[MatchResult] = []
    for envelope in envelopes:
        state, reason = chunk_state(run, envelope)
        if state != "scored":
            pending.append({"chunk": envelope.index, "status": state, "reason": reason})
            continue
        match_chunk = load_model(match_path(run, envelope.index), MatchChunk, "match chunk")
        scored.extend(match_chunk.match_results)

    if pending:
        fail(
            f"{len(pending)} of {len(envelopes)} chunk(s) are not ready to render.",
            code=EXIT_TASK_FAILED,
            pending=pending,
            run=str(run),
            fix="Score the pending chunks, write matches/chunk-NN.json, then re-run report.",
        )

    urls = [result.url for result in scored]
    if len(urls) != len(set(urls)):
        fail(
            "The same job URL appears in more than one chunk result.",
            code=EXIT_TASK_FAILED,
            run=str(run),
        )

    ordered = sorted(scored, key=lambda result: result.match_score, reverse=True)
    bands = _bands(ordered)
    output = MatcherOutput(match_results=ordered, bands=bands)
    write_json(run / "matches.all.json", output.model_dump(mode="json"))
    saved = save_matches(workspace.db, ordered)

    reported = [result for result in ordered if result.match_score >= args.min_score]

    metadata = read_json(run / "run.json") if (run / "run.json").is_file() else {}
    bullets = [
        f"- **Date**: {metadata.get('created', 'unknown')}",
        f"- **Source**: {metadata.get('source', 'unknown')}",
        f"- **Jobs found**: {metadata.get('count', len(ordered))}",
        f"- **Jobs scored**: {len(ordered)}",
        f"- **Reported (score >= {args.min_score})**: {len(reported)}",
    ]
    if metadata.get("terms"):
        bullets.append(f"- **Search terms**: {'; '.join(metadata['terms'])}")

    report = MatcherOutput(match_results=reported).to_markdown(extra="\n".join(bullets))
    report_path = Path(args.out).expanduser() if args.out else run / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    return {
        "ok": True,
        "run": str(run),
        "report": str(report_path),
        "all_matches": str(run / "matches.all.json"),
        "database": str(workspace.db),
        "saved": saved,
        "scored": len(ordered),
        "reported": len(reported),
        "min_score": args.min_score,
        "bands": bands,
    }


def _bands(results: list[MatchResult]) -> dict:
    bands = {
        "excellent_90+": 0,
        "strong_75-89": 0,
        "moderate_60-74": 0,
        "weak_40-59": 0,
        "poor_<40": 0,
    }
    for result in results:
        score = result.match_score
        if score >= 90:
            bands["excellent_90+"] += 1
        elif score >= 75:
            bands["strong_75-89"] += 1
        elif score >= 60:
            bands["moderate_60-74"] += 1
        elif score >= 40:
            bands["weak_40-59"] += 1
        else:
            bands["poor_<40"] += 1
    return bands


def run_fit(args) -> dict:
    """Validate one Match Result and render it for the conversation."""
    path = Path(args.match).expanduser()
    if not path.is_file():
        fail(f"{path} does not exist.", code=EXIT_TASK_FAILED)

    raw = path.read_text(encoding="utf-8")
    result: MatchResult | None = None
    errors = None
    for model in (MatchResult, MatchChunk, MatcherOutput):
        try:
            parsed = model.model_validate_json(raw)
        except ValidationError as exc:
            errors = exc.errors(include_url=False)
            continue
        if isinstance(parsed, (MatchChunk, MatcherOutput)):
            if len(parsed.match_results) != 1:
                fail(
                    f"{path} contains {len(parsed.match_results)} results; "
                    "fit expects exactly one. Use report for a full run, or "
                    "extract a single Match Result into its own file.",
                    code=EXIT_TASK_FAILED,
                )
            result = parsed.match_results[0]
        else:
            result = parsed
        break

    if result is None:
        fail(f"{path} is not a MatchResult.", code=EXIT_TASK_FAILED, errors=errors)

    if args.out:
        write_json(Path(args.out).expanduser(), result.model_dump(mode="json"))

    saved_to = None
    if args.save:
        workspace: Workspace = workspace_from_args(args)
        save_matches(workspace.db, [result])
        saved_to = str(workspace.db)

    if args.json:
        payload = {"ok": True, "match": result.model_dump(mode="json")}
        if saved_to:
            payload["database"] = saved_to
        return payload

    print(result.to_markdown())
    log(f"[jobmatch] {result.title} @ {result.company}: {result.match_score}/100")
    return None


def run_lookup(args) -> dict:
    """Answer questions about jobs that have already been seen or scored.

    Reads the candidate's jobs.db only: no run directory, no profile, no model.
    """
    workspace: Workspace = workspace_from_args(args)
    matches, descriptions = load_saved(workspace.db)

    filtered_matches = [
        row
        for row in matches
        if _matches_filters(row, args)
        and (args.min_score is None or row.get("match_score", 0) >= args.min_score)
    ]
    filtered_descriptions = [row for row in descriptions if _matches_filters(row, args)]

    filtered_matches.sort(
        key=lambda row: (row.get("match_score", 0), row.get("scored_at") or ""), reverse=True
    )
    filtered_descriptions.sort(key=lambda row: row.get("cached_at") or "", reverse=True)

    total_matches = len(filtered_matches)
    total_descriptions = len(filtered_descriptions)
    limit = max(0, args.limit)
    if limit:
        filtered_matches = filtered_matches[:limit]
        filtered_descriptions = filtered_descriptions[:limit]

    shown_matches = [row if args.include_description else _summary(row) for row in filtered_matches]
    shown_descriptions = [
        row if args.include_description else _summary(row) for row in filtered_descriptions
    ]

    payload = {
        "ok": True,
        "database": str(workspace.db),
        "count": len(shown_matches),
        "total_matches": total_matches,
        "total_descriptions": total_descriptions,
        "matches": shown_matches,
        "unscored": shown_descriptions,
    }
    if not shown_matches and not shown_descriptions:
        payload["note"] = (
            "Nothing saved matches that query. Collect jobs with the job-search skill "
            "and score them with this skill; both save to jobs.db."
        )
    return payload


def run_validate(args) -> dict:
    path = Path(args.input).expanduser()
    models = {
        "match-batch": MatchChunk,
        "match": MatchResult,
        "profile": CandidateProfile,
        "descriptions": JobDescriptionList,
    }
    model = models[args.schema]
    parsed = load_model(path, model, args.schema)
    payload = {"ok": True, "schema": args.schema, "file": str(path)}
    if isinstance(parsed, MatchChunk):
        payload["results"] = len(parsed.match_results)
    elif isinstance(parsed, JobDescriptionList):
        payload["jobs"] = len(parsed.jobs)
    return payload


def run_schema(args) -> dict:
    schemas = {
        "match-batch": MatchChunk.model_json_schema(),
        "match": MatchResult.model_json_schema(),
        "profile": CandidateProfile.model_json_schema(),
        "descriptions": JobDescriptionList.model_json_schema(),
        "chunk": ChunkEnvelope.model_json_schema(),
    }
    if args.name:
        if args.name not in schemas:
            fail(f"Unknown schema {args.name!r}; choose from {sorted(schemas)}", code=EXIT_CONFIG)
        return schemas[args.name]
    return {"ok": True, "schemas": schemas}


# -- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobmatch.py",
        description=(
            "Prepare chunks, validate scores, render the Match Report and look up saved results."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_init_subcommand(subparsers, "jobmatch.py")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        add_common_arguments(sub)
        return sub

    prepare = add("prepare", "Validate a run and split it into chunk files for scoring.")
    prepare.add_argument("--run", required=True)
    prepare.add_argument("--descriptions", default=None)
    prepare.add_argument("--profile", default=None)
    prepare.add_argument("--size", type=int, default=10, help="Jobs per chunk. Default 10.")
    prepare.add_argument(
        "--status", action="store_true", help="Report chunk status without writing."
    )
    prepare.set_defaults(handler=run_prepare)

    report = add("report", "Merge scored chunks and render report.md.")
    report.add_argument("--run", required=True)
    report.add_argument("--min-score", type=int, default=75)
    report.add_argument("--out", default=None)
    report.set_defaults(handler=run_report)

    fit = add("fit", "Render one Match Result as Markdown.")
    fit.add_argument(
        "--match", required=True, help="A MatchResult or single-result MatchChunk JSON."
    )
    fit.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    fit.add_argument("--out", default=None)
    fit.add_argument(
        "--save",
        action="store_true",
        help="Also save the result to the candidate's jobs.db for later lookup.",
    )
    fit.set_defaults(handler=run_fit)

    lookup = add("lookup", "Search saved match results and cached descriptions in jobs.db.")
    lookup.add_argument("--url", default=None, help="A job URL, with or without tracking params.")
    lookup.add_argument(
        "--query", default=None, help="Case-insensitive text over title, company, location, domain."
    )
    lookup.add_argument(
        "--min-score", type=int, default=None, help="Only results at or above this."
    )
    lookup.add_argument(
        "--limit", type=int, default=10, help="Rows per list. Default 10; 0 means no limit."
    )
    lookup.add_argument(
        "--include-description",
        action="store_true",
        help="Include the full posting text. Long; only when the user needs it.",
    )
    lookup.set_defaults(handler=run_lookup)

    validate = add("validate", "Check a JSON file against the contract.")
    validate.add_argument("--in", dest="input", required=True)
    validate.add_argument(
        "--schema", choices=["match-batch", "match", "profile", "descriptions"], required=True
    )
    validate.set_defaults(handler=run_validate)

    schema = add("schema", "Print the JSON Schema to aim at.")
    schema.add_argument(
        "--name",
        choices=["match-batch", "match", "profile", "descriptions", "chunk"],
        default=None,
    )
    schema.set_defaults(handler=run_schema)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        fail("Interrupted.", code=EXIT_TASK_FAILED)

    if result is not None:
        emit(result)


if __name__ == "__main__":
    main()
