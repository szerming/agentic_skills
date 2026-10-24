#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.12", "pypdf>=6.9"]
# ///
"""Turn a CV PDF into plain text, and check the Candidate Profile JSON.

This script does not call a model. The candidate-profile skill's model reads the
extracted text and writes the profile; this script only removes the PDF parsing
and the schema checking from that job.

Every command prints JSON on stdout. Exit codes: 0 ok, 2 configuration problem,
3 the file could not be read or does not satisfy the contract.
"""

from __future__ import annotations

import sys

# The skills are installed read-only in some harnesses; importing the sibling
# modules must not drop __pycache__ inside the skill directory.
sys.dont_write_bytecode = True

import argparse
import sys
from pathlib import Path

from job_models import CandidateProfile
from job_workspace import (
    EXIT_CONFIG,
    Workspace,
    WorkspaceError,
    add_common_arguments,
    add_init_subcommand,
    emit,
    fail,
    resolve_home,
    resolve_workspace,
    slugify,
    workspace_from_args,
    write_config,
)
from pydantic import ValidationError

EXIT_TASK_FAILED = 3
PREVIEW_CHARS = 400


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _tidy(text: str) -> str:
    """Strip the whitespace noise pypdf leaves behind, without losing paragraph breaks."""
    import re

    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[^\S\n]{2,}", " ", text)
    return text.strip()


def extract_pdf_text(pdf: Path) -> tuple[str, int]:
    """Return (text, page_count).

    Layout mode keeps words together on a line; the default mode turns spaced-out
    PDFs into one word per line, which reads badly and costs tokens.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    pages: list[str] = []
    for page in reader.pages:
        try:
            raw = page.extract_text(extraction_mode="layout")
        except TypeError:  # older pypdf without extraction_mode
            raw = page.extract_text()
        pages.append(_tidy(raw or ""))
    return "\n\n".join(page for page in pages if page), len(reader.pages)


def run_text(args) -> dict:
    workspace = workspace_from_args(args)
    pdf = Path(args.pdf).expanduser()
    if not pdf.is_file():
        fail(f"{pdf} does not exist.", code=EXIT_TASK_FAILED)

    try:
        stored = workspace.store_cv(pdf)
    except WorkspaceError as exc:
        fail(
            str(exc),
            code=EXIT_TASK_FAILED,
            fix=f"Drop the CV in {workspace.cv_dir} and re-run, or pass a PDF/DOCX/TXT path.",
        )

    try:
        if stored.suffix.lower() in {".txt", ".md"}:
            text, pages = stored.read_text(encoding="utf-8", errors="replace"), 0
        else:
            text, pages = extract_pdf_text(stored)
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of exception types
        fail(f"Could not read {stored}: {exc}", code=EXIT_TASK_FAILED)

    if not text.strip():
        fail(
            f"{stored} produced no text. It may be a scanned PDF.",
            code=EXIT_TASK_FAILED,
            fix="OCR the PDF first, or drop a text version in " + str(workspace.cv_dir),
        )

    if args.max_chars and len(text) > args.max_chars:
        text = text[: args.max_chars]

    out = Path(args.out).expanduser() if args.out else workspace.cv_text
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")

    return {
        "ok": True,
        "candidate": workspace.candidate,
        "candidate_dir": str(workspace.candidate_dir),
        "pdf": str(pdf),
        "stored": str(stored),
        "out": str(out),
        "pages": pages,
        "chars": len(text),
        "preview": text[:PREVIEW_CHARS],
    }


def run_candidate(args) -> dict:
    """Find or prepare the candidate folder, then say what is still missing."""
    try:
        home = resolve_home(getattr(args, "home", None))
        if args.candidate:
            workspace = Workspace(home, slugify(args.candidate)).ensure()
        else:
            workspace = resolve_workspace(home).ensure()
    except WorkspaceError as exc:
        fail(
            str(exc),
            code=EXIT_CONFIG,
            fix="Pass --candidate <name>, or run init --home <path> --candidate <name>.",
        )

    stored = None
    if args.cv:
        try:
            stored = workspace.store_cv(args.cv)
        except WorkspaceError as exc:
            fail(str(exc), code=EXIT_TASK_FAILED, fix=f"Drop the CV in {workspace.cv_dir}")

    config_path = None
    if workspace.candidate and not args.no_config:
        config_path = write_config(home, candidate=workspace.candidate)

    cvs = workspace.cvs
    if workspace.candidate is None and not cvs:
        nxt = {
            "action": "create_candidate",
            "command": f"jobprofile.py candidate --candidate <name> --home {home}",
            "say": "No candidate folder yet. Name the candidate, then supply the CV.",
        }
    elif not cvs:
        nxt = {
            "action": "drop_cv",
            "path": str(workspace.cv_dir),
            "say": f"Drop the CV in {workspace.cv_dir}, or pass --cv <path> to copy it there.",
        }
    elif not workspace.profile.is_file():
        nxt = {
            "action": "extract_profile",
            "cv": str(cvs[0]),
            "command": f"jobprofile.py text --pdf {cvs[0]} --home {home}"
            + (f" --candidate {workspace.candidate}" if workspace.candidate else ""),
        }
    else:
        nxt = {"action": "ready", "profile": str(workspace.profile)}

    return {
        "ok": True,
        "home": str(home),
        "candidate": workspace.candidate,
        "candidate_dir": str(workspace.candidate_dir),
        "cv_dir": str(workspace.cv_dir),
        "cvs": [str(path) for path in cvs],
        "stored_cv": str(stored) if stored else None,
        "profile": workspace.profile.is_file(),
        "search_terms": workspace.search_terms.is_file(),
        "database": workspace.db.is_file(),
        "config": str(config_path) if config_path else None,
        "next": nxt,
    }


def run_validate(args) -> dict:
    path = Path(args.input).expanduser()
    if not path.is_file():
        fail(f"{path} does not exist.", code=EXIT_TASK_FAILED)
    try:
        profile = CandidateProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        fail(
            f"{path} does not match CandidateProfile.",
            code=EXIT_TASK_FAILED,
            errors=exc.errors(include_url=False),
        )

    return {
        "ok": True,
        "file": str(path),
        "headline": profile.headline,
        "experience_items": len(profile.experience),
        "hard_skills": len(profile.hard_skills),
    }


def run_schema(_args) -> dict:
    return CandidateProfile.model_json_schema()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobprofile.py",
        description="Extract CV text and validate Candidate Profile JSON.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_init_subcommand(subparsers, "jobprofile.py")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        add_common_arguments(sub)
        return sub

    candidate = add("candidate", "Find or prepare a candidate folder; copy in a CV.")
    candidate.add_argument("--cv", default=None, help="CV to copy into <candidate>/cv.")
    candidate.add_argument(
        "--no-config",
        action="store_true",
        help="Do not record the candidate in .jobsearch/config.json.",
    )
    candidate.set_defaults(handler=run_candidate)

    text = add("text", "Extract plain text from a CV PDF.")
    text.add_argument("--pdf", required=True)
    text.add_argument("--out", default=None, help="Defaults to <candidate>/cv.txt")
    text.add_argument("--max-chars", type=int, default=120_000)
    text.set_defaults(handler=run_text)

    validate = add("validate", "Check a Candidate Profile JSON against the contract.")
    validate.add_argument("--in", dest="input", required=True)
    validate.set_defaults(handler=run_validate)

    schema = add("schema", "Print the CandidateProfile JSON Schema.")
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
