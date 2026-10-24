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

import argparse
import sys
from pathlib import Path

from job_models import CandidateProfile
from job_workspace import (
    add_common_arguments,
    add_init_subcommand,
    emit,
    fail,
    workspace_from_args,
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
        text, pages = extract_pdf_text(pdf)
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of exception types
        fail(f"Could not read {pdf}: {exc}", code=EXIT_TASK_FAILED)

    if not text.strip():
        fail(
            f"{pdf} produced no text. It may be a scanned PDF.",
            code=EXIT_TASK_FAILED,
            fix="OCR the PDF first, or write the CV text to a .txt file by hand.",
        )

    if args.max_chars and len(text) > args.max_chars:
        text = text[: args.max_chars]

    out = Path(args.out).expanduser() if args.out else workspace.home / "cv.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")

    return {
        "ok": True,
        "pdf": str(pdf),
        "out": str(out),
        "pages": pages,
        "chars": len(text),
        "preview": text[:PREVIEW_CHARS],
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
    return {"ok": True, "schema": CandidateProfile.model_json_schema()}


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

    text = add("text", "Extract plain text from a CV PDF.")
    text.add_argument("--pdf", required=True)
    text.add_argument("--out", default=None, help="Defaults to <home>/cv.txt")
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
