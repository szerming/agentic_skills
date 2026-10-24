#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.12", "playwright>=1.58"]
# ///
"""Collect Job Descriptions from LinkedIn into a run directory.

This is the mechanical half of the job-search skills: search, paginate, dedupe,
cache, fetch. It makes no judgments. Matching is the match-jobs skill's job.

Every command prints JSON on stdout. Progress goes to stderr. Exit codes:
  0 ok
  1 doctor found a failed check
  2 configuration or usage problem
  3 the requested work could not be completed
  4 the LinkedIn session is missing or expired
"""

from __future__ import annotations

import sys

# The skills are installed read-only in some harnesses; importing the sibling
# modules must not drop __pycache__ inside the skill directory.
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generic, Sequence, TypeVar

from job_models import JobDescription, JobDescriptionList, JobEntry
from job_workspace import (
    EXIT_CONFIG,
    Workspace,
    add_common_arguments,
    add_init_subcommand,
    emit,
    fail,
    workspace_from_args,
)
from pydantic import BaseModel, ValidationError

EXIT_CHECK_FAILED = 1
EXIT_TASK_FAILED = 3
EXIT_SESSION = 4

T = TypeVar("T", bound=BaseModel)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    """Write atomically so a crash never leaves a half-written run behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


class PydanticStore(Generic[T]):
    """A tiny SQLite key/value cache keyed by job URL.

    Ported from agentic_workflow's utilities/model_store.py, plus stats() and
    prune() so the skill can report and trim the cache.
    """

    def __init__(self, db_path: Path, model_class: type[T], table_name: str = "model_store"):
        self.db_path = db_path
        self.model_class = model_class
        self.table_name = table_name
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self):
        import sqlite3

        return sqlite3.connect(self.db_path)

    def save(self, key: str, model: T) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table_name} (key, data, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    data=excluded.data,
                    updated_at=excluded.updated_at
                """,
                (key, model.model_dump_json(), now),
            )

    def get(self, key: str) -> T | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT data FROM {self.table_name} WHERE key = ?", (key,)
            ).fetchone()
        return self.model_class.model_validate_json(row[0]) if row else None

    def exists(self, key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(f"SELECT 1 FROM {self.table_name} WHERE key = ?", (key,)).fetchone()
        return row is not None

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {self.table_name}").fetchone()[0]

    def prune(self, older_than_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(f"DELETE FROM {self.table_name} WHERE updated_at < ?", (cutoff,))
            return cursor.rowcount


# -- terms -------------------------------------------------------------------


def load_search_terms(path: Path) -> list[str]:
    """Read the bare JSON array the job-search-terms skill writes."""
    if not path.is_file():
        fail(
            f"No search terms at {path}.",
            code=EXIT_TASK_FAILED,
            fix=(
                "Run the job-search-terms skill, or write a bare JSON array of search "
                'terms to that path, for example ["Software Architect", "Head of Software"].'
            ),
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}", code=EXIT_TASK_FAILED)

    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        fail(f"{path} must be a JSON array of strings.", code=EXIT_TASK_FAILED)
    if not raw:
        fail(f"{path} is empty; nothing to search for.", code=EXIT_TASK_FAILED)
    return raw


def combine_terms(terms: Sequence[str]) -> str:
    """LinkedIn accepts boolean keywords; OR the terms into one query.

    Pure and testable. This is the one place that decides how a term list becomes
    a query, so the wording never drifts into a prompt.
    """
    return " or ".join(f"({term})" for term in terms)


def dedupe_entries(entries: Sequence[JobEntry]) -> list[JobEntry]:
    """Drop repeat postings by URL, keeping the first occurrence. Pure."""
    seen: set[str] = set()
    unique: list[JobEntry] = []
    for entry in entries:
        if entry.url in seen:
            continue
        seen.add(entry.url)
        unique.append(entry)
    return unique


# -- browser wiring ----------------------------------------------------------


def browser_config(args, workspace: Workspace):
    from linkedin import DEFAULT_GEO_ID, BrowserConfig

    return BrowserConfig(
        session_dir=workspace.session,
        headless=args.headless,
        max_pages=args.max_pages,
        geo_id=args.geo_id or DEFAULT_GEO_ID,
        seniority_levels=tuple(args.seniority) if args.seniority else (4, 5, 6),
        workplace_types=tuple(args.workplace) if args.workplace else (1, 2, 3),
        delay=args.delay,
        channel=getattr(args, "channel", None),
        executable_path=getattr(args, "executable_path", None),
    )


def add_browser_arguments(parser) -> None:
    """Browser identity flags, for hosts where Google blocks Playwright Chromium."""
    parser.add_argument(
        "--channel",
        default=None,
        help="Browser channel, e.g. chrome. Use when Google refuses sign-in in Chromium.",
    )
    parser.add_argument(
        "--executable-path",
        default=None,
        help="Path to a Chrome/Chromium binary installed outside Playwright's cache.",
    )


def chromium_installed(
    executable_path: str | None = None, channel: str | None = None
) -> tuple[bool, str]:
    """Check the browser Playwright will actually launch.

    A chromium-* directory is not enough: the installed build must match the
    Playwright version the script resolves, or launch fails with "Executable
    doesn't exist".
    """
    if executable_path:
        exe = Path(executable_path).expanduser()
        return (True, str(exe)) if exe.is_file() else (False, f"{exe} does not exist")

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        return False, f"playwright is not importable: {exc}"

    try:
        with sync_playwright() as playwright:
            resolved = Path(playwright.chromium.executable_path)
    except Exception as exc:  # noqa: BLE001
        return False, f"could not resolve the browser: {exc}"

    if channel:
        # Non-default channels are resolved at launch; report the intent plainly.
        return True, f"{resolved} (channel={channel})"
    if resolved.is_file():
        return True, str(resolved)
    return False, f"missing {resolved}"


# -- commands ----------------------------------------------------------------


async def run_collect(args) -> dict:
    from linkedin import LinkedInBrowser, SessionExpired

    workspace = workspace_from_args(args)
    config = browser_config(args, workspace)

    terms: list[str] = []
    listing_url: str | None = None

    if args.source == "terms":
        terms_path = (
            Path(args.terms_file).expanduser() if args.terms_file else workspace.search_terms
        )
        terms = load_search_terms(terms_path)[: args.max_terms]
        query = combine_terms(terms)
    elif args.source == "url":
        if not args.url:
            fail("--source url requires --url <listing url>", code=EXIT_CONFIG)
        listing_url = args.url
        query = args.url
    else:  # recommended
        query = "recommended"

    run_dir = workspace.run_dir(args.run) if args.run else workspace.new_run()
    created_run = args.run is None
    run_dir.mkdir(parents=True, exist_ok=True)
    descriptions_path = Path(args.out).expanduser() if args.out else run_dir / "descriptions.json"

    log(f"[jobsearch] workspace: {workspace.home}")
    log(f"[jobsearch] run:       {run_dir}")
    log(f"[jobsearch] source:    {args.source} ({query[:120]}...)")

    entries: list[JobEntry] = []
    try:
        async with LinkedInBrowser(config) as browser:
            await browser.require_session()

            if args.source == "terms":
                for index, term in enumerate(terms, 1):
                    log(f"[jobsearch] searching {index}/{len(terms)}: {term}")
                    entries.extend(await browser.fetch_search(term, past_days=args.past_days))
                    log(f"[jobsearch]   running total: {len(entries)} entries")
            elif args.source == "url":
                entries.extend(await browser.fetch_listing(listing_url or ""))
            else:
                entries.extend(await browser.fetch_recommended())

            entries = dedupe_entries(entries)
            log(f"[jobsearch] {len(entries)} unique entries; fetching descriptions")

            store = PydanticStore(workspace.db, JobDescription)
            jobs: list[JobDescription] = []
            cached = scraped = failed = 0

            def flush_descriptions() -> None:
                """Persist progress atomically, so a crash costs at most one fetch."""
                payload = JobDescriptionList(jobs=jobs)
                write_json(descriptions_path, payload.model_dump(mode="json"))

            for index, entry in enumerate(entries, 1):
                known = store.get(entry.url)
                if known is not None:
                    jobs.append(known)
                    cached += 1
                    continue

                log(
                    f"[jobsearch] describing {index}/{len(entries)}: "
                    f"{entry.title} ({entry.company})"
                )
                html_dump = (
                    Path(args.save_html).expanduser() / f"{index:03d}.html"
                    if args.save_html
                    else None
                )
                description, error = await browser.get_description(entry, save_html=html_dump)
                if error:
                    failed += 1
                    log(f"[jobsearch]   failed: {error}")
                else:
                    store.save(entry.url, description)
                    scraped += 1
                jobs.append(description)
                flush_descriptions()
                await _sleep(args.delay)

    except SessionExpired as exc:
        _discard_empty_run(run_dir, created_run)
        extra = {"run": str(run_dir), "partial": True} if descriptions_path.is_file() else {}
        fail(str(exc), code=EXIT_SESSION, fix="jobsearch.py login", **extra)
    except Exception as exc:  # noqa: BLE001 - report, do not dump a traceback
        _discard_empty_run(run_dir, created_run)
        extra = {"run": str(run_dir), "partial": True} if descriptions_path.is_file() else {}
        text = str(exc).lower()
        if "executable" in text or "playwright install" in text or "channel" in text:
            fail(
                f"Could not launch a browser: {exc}",
                code=EXIT_CONFIG,
                fix="Run: uv run --with playwright playwright install chromium, or pass "
                "--channel chrome / --executable-path <chrome>.",
                **extra,
            )
        fail(
            f"Run failed: {type(exc).__name__}: {exc}",
            code=EXIT_TASK_FAILED,
            fix="Run doctor to check the workspace, browser and session, then retry.",
            **extra,
        )

    payload = JobDescriptionList(jobs=jobs)
    write_json(descriptions_path, payload.model_dump(mode="json"))

    metadata = {
        "created": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "query": query,
        "terms": terms,
        "listing_url": listing_url,
        "past_days": args.past_days,
        "count": len(jobs),
        "cached": cached,
        "scraped": scraped,
        "failed": failed,
        "max_pages": config.max_pages,
        "geo_id": config.geo_id,
        "descriptions_sha256": hashlib.sha256(descriptions_path.read_bytes()).hexdigest(),
    }
    write_json(run_dir / "run.json", metadata)

    if args.strict and failed:
        fail(
            f"{failed} of {len(jobs)} descriptions could not be fetched.",
            code=EXIT_TASK_FAILED,
            run=str(run_dir),
        )

    return {
        "ok": True,
        "run": str(run_dir),
        "descriptions": str(descriptions_path),
        "count": len(jobs),
        "cached": cached,
        "scraped": scraped,
        "failed": failed,
        "terms": len(terms),
    }


def _discard_empty_run(run_dir: Path, created: bool) -> None:
    """A run that never produced output should not leave a timestamped empty dir."""
    if not created:
        return
    try:
        if run_dir.is_dir() and not any(run_dir.iterdir()):
            run_dir.rmdir()
    except OSError:
        pass


async def _sleep(seconds: float) -> None:
    import asyncio

    if seconds > 0:
        await asyncio.sleep(seconds)


async def run_describe(args) -> dict:
    """Single posting, for debugging selectors. Deliberately does not touch the cache."""
    from linkedin import LinkedInBrowser, SessionExpired

    workspace = workspace_from_args(args)
    config = browser_config(args, workspace)
    entry = JobEntry(title="", url=args.url, company="", location="")

    html_path = Path(args.save_html).expanduser() if args.save_html else None
    try:
        async with LinkedInBrowser(config) as browser:
            await browser.require_session()
            description, error = await browser.get_description(entry, save_html=html_path)
    except SessionExpired as exc:
        fail(str(exc), code=EXIT_SESSION, fix="jobsearch.py login")
    except Exception as exc:  # noqa: BLE001 - report, do not dump a traceback
        fail(
            f"Could not start the browser: {type(exc).__name__}: {exc}",
            code=EXIT_CONFIG,
            fix="Run doctor, or pass --channel chrome / --executable-path <chrome>.",
        )

    if error:
        fail(
            f"Could not read the description: {error}",
            code=EXIT_TASK_FAILED,
            url=args.url,
            html=str(html_path) if html_path else None,
            fix="Inspect the saved HTML, then update the selectors in linkedin.py.",
        )

    if args.out:
        out = Path(args.out).expanduser()
        write_json(out, description.model_dump(mode="json"))
        return {"ok": True, "description": str(out), "chars": len(description.description)}

    return {"ok": True, "job": description.model_dump(mode="json")}


def run_login(args) -> dict:
    import asyncio

    from linkedin import LinkedInBrowser

    workspace = workspace_from_args(args)
    config = browser_config(args, workspace)

    async def login() -> bool:
        async with LinkedInBrowser(config) as browser:
            return await browser.login(timeout_s=args.timeout)

    log(
        "[jobsearch] A browser window is opening. Log in to LinkedIn there; "
        f"this command waits up to {args.timeout}s, then checks for the auth cookie."
    )
    try:
        success = asyncio.run(login())
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "executable" in text or "playwright install" in text or "channel" in text:
            fail(
                f"Could not launch a browser: {exc}",
                code=EXIT_CONFIG,
                fix="Run: uv run --with playwright playwright install chromium, or pass "
                "--channel chrome / --executable-path <chrome>.",
            )
        if "closed" in text:
            fail(
                "The browser window closed before sign-in completed.",
                code=EXIT_SESSION,
                fix="Re-run login and leave the window open until it closes itself.",
            )
        fail(f"Login failed to start: {exc}", code=EXIT_CONFIG, fix="Re-run login.")

    if not success:
        fail(
            "Timed out before a LinkedIn session appeared.",
            code=EXIT_SESSION,
            fix="Re-run login and finish the sign-in (and any 2FA) in the browser window. "
            "If Google says the browser is not secure, re-run with --channel chrome.",
        )
    return {"ok": True, "session": str(workspace.session)}


def run_doctor(args) -> dict:
    import asyncio

    from job_workspace import WorkspaceError, resolve_home, resolve_workspace

    checks: dict[str, dict] = {}

    def record(name: str, ok: bool, detail: str, fix: str | None = None, critical: bool = True):
        entry = {"ok": ok, "detail": detail, "critical": critical}
        if fix and not ok:
            entry["fix"] = fix
        checks[name] = entry

    workspace: Workspace | None = None
    try:
        home = resolve_home(args.home)
        workspace = resolve_workspace(home, getattr(args, "candidate", None)).ensure()
        record("workspace", True, str(home))
        probe = workspace.home / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        record("workspace_writable", True, "writable")
    except WorkspaceError as exc:
        record("workspace", False, str(exc), "init --home <path>")
    except OSError as exc:
        record("workspace_writable", False, str(exc), "Choose a writable --home")

    installed, where = chromium_installed(
        getattr(args, "executable_path", None), getattr(args, "channel", None)
    )
    record(
        "chromium",
        installed,
        where,
        "uv run --with playwright playwright install chromium, or pass "
        "--channel chrome / --executable-path <chrome>",
    )

    if workspace is not None:
        record(
            "candidate",
            workspace.candidate is not None or workspace.profile.is_file(),
            workspace.candidate
            if workspace.candidate
            else f"flat layout at {workspace.home}",
            "jobprofile.py candidate --candidate <name> --cv <path>",
            critical=False,
        )
        session_files = len(list(workspace.session.rglob("*"))) if workspace.session.is_dir() else 0
        record(
            "session_dir",
            session_files > 0,
            f"{workspace.session} ({session_files} files)",
            "jobsearch.py login",
        )

        if args.deep and session_files and installed:
            from linkedin import LinkedInBrowser

            async def probe_session() -> bool:
                async with LinkedInBrowser(browser_config(args, workspace)) as browser:
                    return await browser.has_session()

            try:
                has_cookie = asyncio.run(probe_session())
            except Exception as exc:  # noqa: BLE001
                record("session_cookie", False, f"could not check: {exc}", "jobsearch.py login")
            else:
                record(
                    "session_cookie",
                    has_cookie,
                    "li_at cookie present" if has_cookie else "li_at cookie missing",
                    "jobsearch.py login",
                )

        record(
            "search_terms",
            workspace.search_terms.is_file(),
            str(workspace.search_terms),
            "Run the job-search-terms skill, or write a bare JSON array of terms.",
            critical=False,
        )
        record(
            "profile",
            workspace.profile.is_file(),
            str(workspace.profile),
            "Run the candidate-profile skill.",
            critical=False,
        )

        store = PydanticStore(workspace.db, JobDescription)
        record("cache", True, f"{store.count()} cached descriptions", critical=False)
        record("stats", True, json.dumps(workspace.stats()), critical=False)

    failed = [name for name, check in checks.items() if check["critical"] and not check["ok"]]
    return {"ok": not failed, "failed": failed, **checks}


def run_cache(args) -> dict:
    workspace = workspace_from_args(args)
    store = PydanticStore(workspace.db, JobDescription)
    if args.cache_command == "stats":
        size = workspace.db.stat().st_size if workspace.db.is_file() else 0
        return {"ok": True, "descriptions": store.count(), "bytes": size, "db": str(workspace.db)}
    removed = store.prune(args.older_than_days)
    return {
        "ok": True,
        "removed": removed,
        "remaining": store.count(),
        "older_than_days": args.older_than_days,
    }


def run_validate(args) -> dict:
    path = Path(args.input).expanduser()
    try:
        parsed = JobDescriptionList.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"{path} does not exist.", code=EXIT_TASK_FAILED)
    except ValidationError as exc:
        fail(
            f"{path} does not match JobDescriptionList.",
            code=EXIT_TASK_FAILED,
            errors=exc.errors(include_url=False),
        )
    return {"ok": True, "jobs": len(parsed.jobs), "file": str(path)}


def run_schema(args) -> dict:
    schemas = {
        "description": JobDescription.model_json_schema(),
        "description-list": JobDescriptionList.model_json_schema(),
    }
    if args.name:
        if args.name not in schemas:
            fail(f"Unknown schema {args.name!r}; choose from {sorted(schemas)}", code=EXIT_CONFIG)
        return schemas[args.name]
    return {"ok": True, "schemas": schemas}


# -- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobsearch.py",
        description="Collect LinkedIn Job Descriptions into a run directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_init_subcommand(subparsers, "jobsearch.py")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        add_common_arguments(sub)
        return sub

    collect = add("collect", "Search LinkedIn and store the descriptions of every job found.")
    add_browser_arguments(collect)
    collect.add_argument("--source", choices=["terms", "url", "recommended"], default="terms")
    collect.add_argument("--terms-file", default=None, help="Defaults to <home>/search_terms.json")
    collect.add_argument("--url", default=None, help="Listing URL, for --source url")
    collect.add_argument("--past-days", type=int, default=7)
    collect.add_argument(
        "--run", default=None, help="Run directory. Default: a new timestamped run."
    )
    collect.add_argument("--out", default=None, help="Defaults to <run>/descriptions.json")
    collect.add_argument("--max-terms", type=int, default=12, help="Cap terms per run. Default 12.")
    collect.add_argument("--max-pages", type=int, default=5, help="Cap pages per term. Default 5.")
    collect.add_argument(
        "--geo-id", default=None, help="LinkedIn geoId. Defaults to Cambridgeshire."
    )
    collect.add_argument(
        "--seniority", type=int, nargs="*", default=None, help="4=mid-senior 5=director 6=exec"
    )
    collect.add_argument(
        "--workplace", type=int, nargs="*", default=None, help="1=onsite 2=remote 3=hybrid"
    )
    collect.add_argument(
        "--delay", type=float, default=1.0, help="Seconds between requests. Default 1.0."
    )
    collect.add_argument("--headless", action="store_true", help="Run Chromium without a window.")
    collect.add_argument(
        "--save-html", default=None, help="Directory for HTML dumps of failed postings."
    )
    collect.add_argument("--strict", action="store_true", help="Exit 3 if any description failed.")
    collect.set_defaults(handler=_async(run_collect))

    describe = add("describe", "Fetch one posting. For debugging selectors.")
    add_browser_arguments(describe)
    describe.add_argument("--url", required=True)
    describe.add_argument("--out", default=None)
    describe.add_argument("--save-html", default=None)
    describe.add_argument("--delay", type=float, default=0.0)
    describe.add_argument("--max-pages", type=int, default=1)
    describe.add_argument("--geo-id", default=None)
    describe.add_argument("--seniority", type=int, nargs="*", default=None)
    describe.add_argument("--workplace", type=int, nargs="*", default=None)
    describe.add_argument("--headless", action="store_true")
    describe.set_defaults(handler=_async(run_describe))

    login = add("login", "Open a browser and wait for the user to sign in to LinkedIn.")
    add_browser_arguments(login)
    login.add_argument("--timeout", type=int, default=600)
    login.add_argument("--headless", action="store_true")
    login.add_argument("--delay", type=float, default=1.0)
    login.add_argument("--max-pages", type=int, default=1)
    login.add_argument("--geo-id", default=None)
    login.add_argument("--seniority", type=int, nargs="*", default=None)
    login.add_argument("--workplace", type=int, nargs="*", default=None)
    login.set_defaults(handler=run_login)

    doctor = add("doctor", "Check workspace, chromium, session, terms, profile and cache.")
    add_browser_arguments(doctor)
    doctor.add_argument(
        "--deep", action="store_true", help="Launch a headless browser to verify the cookie."
    )
    doctor.add_argument("--headless", action="store_true")
    doctor.add_argument("--delay", type=float, default=0.0)
    doctor.add_argument("--max-pages", type=int, default=1)
    doctor.add_argument("--geo-id", default=None)
    doctor.add_argument("--seniority", type=int, nargs="*", default=None)
    doctor.add_argument("--workplace", type=int, nargs="*", default=None)
    doctor.set_defaults(handler=run_doctor)

    cache = add("cache", "Inspect or trim the description cache.")
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    for name in ("stats", "prune"):
        child = cache_sub.add_parser(name)
        add_common_arguments(child)
        child.set_defaults(handler=run_cache, cache_command=name)
    cache_sub.choices["prune"].add_argument("--older-than-days", type=int, default=90)

    validate = add("validate", "Check a descriptions.json against the contract.")
    validate.add_argument("--in", dest="input", required=True)
    validate.set_defaults(handler=run_validate)

    schema = add("schema", "Print the JSON Schema a run must conform to.")
    schema.add_argument("--name", choices=["description", "description-list"], default=None)
    schema.set_defaults(handler=run_schema)

    return parser


def _async(coroutine_function):
    """Wrap an async handler so argparse stays synchronous."""

    def handler(args):
        import asyncio

        return asyncio.run(coroutine_function(args))

    return handler


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
        if isinstance(result, dict) and result.get("ok") is False:
            raise SystemExit(EXIT_CHECK_FAILED)


if __name__ == "__main__":
    main()
