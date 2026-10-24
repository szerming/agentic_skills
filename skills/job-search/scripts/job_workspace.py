"""Workspace resolution shared by the job-search skills.

Identical copies of this file live in candidate-profile/, job-search/ and
match-jobs/. tests/test_drift.py fails if the copies diverge.

Layout:

    <home>/
      session/                  LinkedIn session, shared by every candidate
      candidates/<slug>/
        cv/                     the original CV, exactly as supplied
        cv.txt                  text extracted from that CV
        profile.json            the Candidate Profile
        search_terms.json       the Search Terms
        jobs.db                 this candidate's description cache
        runs/                   this candidate's run directories

One directory per candidate keeps the cache and the runs with the person they
describe, so a workspace can hold more than one candidate without mixing them.
A flat, pre-candidate layout (profile.json, search_terms.json, jobs.db and runs/
directly under <home>) is still honoured, so older workspaces keep working.

Resolution order:
  1. an explicit --candidate argument
  2. $JOBSEARCH_CANDIDATE
  3. the "candidate" key in .jobsearch/config.json
  4. the only directory under <home>/candidates, when there is exactly one
  5. flat layout: <home> itself

Nothing is ever written inside the skill's own directory: skills get copied or
symlinked into a read-only harness install, and a skill that writes next to
__file__ corrupts that install.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR_NAME = ".jobsearch"
CONFIG_FILE_NAME = "config.json"
ENV_VAR = "JOBSEARCH_HOME"
ENV_VAR_CANDIDATE = "JOBSEARCH_CANDIDATE"
DEFAULT_HOME = "resource"
CANDIDATES_DIR_NAME = "candidates"
CV_DIR_NAME = "cv"
CV_TEXT_NAME = "cv.txt"
CV_SUFFIXES = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md"}

EXIT_CONFIG = 2


class WorkspaceError(RuntimeError):
    """No usable workspace could be resolved."""


def emit(payload: dict) -> None:
    """Print machine-readable JSON on stdout. Every command's happy path uses this."""
    print(json.dumps(payload, indent=2, default=str))


def fail(message: str, code: int = EXIT_CONFIG, **extra) -> None:
    """Print a JSON error and exit. Agents read the `error` and `fix` keys."""
    emit({"ok": False, "error": message, **extra})
    raise SystemExit(code)


def slugify(name: str) -> str:
    """A directory-safe candidate name: "Alex Mercer" -> "alex-mercer"."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise WorkspaceError(f"candidate name {name!r} has no letters or digits")
    return slug


def find_config(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for .jobsearch/config.json."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        config = candidate / CONFIG_DIR_NAME / CONFIG_FILE_NAME
        if config.is_file():
            return config
    return None


def read_config(config_path: Path) -> dict:
    """Read a config file as a dict, or raise WorkspaceError."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"{config_path} is not readable JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkspaceError(f"{config_path} must contain a JSON object.")
    return raw


def home_from_config(config_path: Path) -> Path:
    """Read a config file. Relative `home` values resolve against the config's directory."""
    raw = read_config(config_path)
    home = raw.get("home")
    if not isinstance(home, str) or not home.strip():
        raise WorkspaceError(f'{config_path} has no "home" string.')

    path = Path(home).expanduser()
    if not path.is_absolute():
        path = (config_path.parent.parent / path).resolve()
    return path


def configured_home() -> Path | None:
    """The workspace named by the environment or a config file, if any."""
    if explicit := os.environ.get(ENV_VAR):
        return Path(explicit).expanduser().resolve()
    config = find_config()
    if config is None:
        return None
    return home_from_config(config)


def configured_candidate() -> str | None:
    """The candidate named by the environment or a config file, if any."""
    if explicit := os.environ.get(ENV_VAR_CANDIDATE):
        return explicit
    config = find_config()
    if config is None:
        return None
    value = read_config(config).get("candidate")
    return value.strip() if isinstance(value, str) and value.strip() else None


def resolve_home(explicit: str | Path | None = None) -> Path:
    """Resolve the workspace or raise WorkspaceError with actionable guidance."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    home = configured_home()
    if home is not None:
        return home
    raise WorkspaceError(
        "No job-search workspace is configured. Ask the user where state should live "
        f"(default: ./{DEFAULT_HOME} in the current directory), then run: "
        "init --home <path>. This writes "
        f"{CONFIG_DIR_NAME}/{CONFIG_FILE_NAME}; set ${ENV_VAR} instead to override per shell."
    )


def write_config(home: Path, config_dir: Path | None = None, candidate: str | None = None) -> Path:
    """Write .jobsearch/config.json under `config_dir` (default: cwd)."""
    target_dir = (config_dir or Path.cwd()).resolve() / CONFIG_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / CONFIG_FILE_NAME
    payload: dict[str, str] = {"home": str(home)}
    if candidate:
        payload["candidate"] = candidate
    config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return config_path


def candidate_dirs(home: Path) -> list[Path]:
    """Candidate directories that already exist under <home>/candidates."""
    root = home / CANDIDATES_DIR_NAME
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def has_flat_layout(home: Path) -> bool:
    """True for a pre-candidate workspace with state directly under <home>."""
    return any(
        (home / name).exists() for name in ("profile.json", "search_terms.json", "jobs.db", "runs")
    )


def resolve_candidate(home: Path, explicit: str | None = None) -> str | None:
    """Pick the candidate for this workspace, or None for the flat layout."""
    chosen = explicit or configured_candidate()
    if chosen:
        return slugify(chosen)
    found = candidate_dirs(home)
    if len(found) > 1:
        names = ", ".join(path.name for path in found)
        raise WorkspaceError(
            f"{home / CANDIDATES_DIR_NAME} holds more than one candidate ({names}). "
            "Pass --candidate <name>, or set it in "
            f"{CONFIG_DIR_NAME}/{CONFIG_FILE_NAME}."
        )
    if len(found) == 1:
        return found[0].name
    return None  # flat layout, or a brand-new workspace


def discover_cvs(candidate_dir: Path) -> list[Path]:
    """CVs sitting in <candidate>/cv, newest name order."""
    cv_dir = candidate_dir / CV_DIR_NAME
    if not cv_dir.is_dir():
        return []
    return sorted(
        path for path in cv_dir.iterdir() if path.is_file() and path.suffix.lower() in CV_SUFFIXES
    )


@dataclass(frozen=True)
class Workspace:
    """Every path a skill may write to.

    `candidate` is a slug under <home>/candidates. When it is None the workspace
    is in the flat, single-candidate layout and the candidate paths ARE <home>.
    """

    home: Path
    candidate: str | None = None

    def __post_init__(self) -> None:
        if self.candidate:
            object.__setattr__(self, "candidate", slugify(self.candidate))

    @property
    def candidate_dir(self) -> Path:
        if self.candidate:
            return self.home / CANDIDATES_DIR_NAME / self.candidate
        return self.home

    @property
    def cv_dir(self) -> Path:
        return self.candidate_dir / CV_DIR_NAME

    @property
    def cv_text(self) -> Path:
        return self.candidate_dir / CV_TEXT_NAME

    @property
    def cvs(self) -> list[Path]:
        return discover_cvs(self.candidate_dir)

    @property
    def profile(self) -> Path:
        return self.candidate_dir / "profile.json"

    @property
    def search_terms(self) -> Path:
        return self.candidate_dir / "search_terms.json"

    @property
    def db(self) -> Path:
        return self.candidate_dir / "jobs.db"

    @property
    def runs(self) -> Path:
        return self.candidate_dir / "runs"

    @property
    def session(self) -> Path:
        """Account-level, so it stays with the workspace rather than a candidate."""
        return self.home / "session"

    def ensure(self) -> "Workspace":
        self.home.mkdir(parents=True, exist_ok=True)
        self.candidate_dir.mkdir(parents=True, exist_ok=True)
        self.cv_dir.mkdir(parents=True, exist_ok=True)
        self.runs.mkdir(parents=True, exist_ok=True)
        return self

    def store_cv(self, source: str | Path) -> Path:
        """Copy a supplied CV into <candidate>/cv and return the stored path."""
        src = Path(source).expanduser()
        if not src.is_file():
            raise WorkspaceError(f"CV not found: {src}")
        if src.suffix.lower() not in CV_SUFFIXES:
            raise WorkspaceError(
                f"{src.name} is not a CV file type; expected one of {sorted(CV_SUFFIXES)}"
            )
        self.cv_dir.mkdir(parents=True, exist_ok=True)
        dest = self.cv_dir / src.name
        if dest.exists():
            if dest.resolve() == src.resolve() or dest.stat().st_size == src.stat().st_size:
                return dest
            stem, suffix = dest.stem, dest.suffix
            index = 2
            while (self.cv_dir / f"{stem}-{index}{suffix}").exists():
                index += 1
            dest = self.cv_dir / f"{stem}-{index}{suffix}"
        shutil.copy2(src, dest)
        return dest

    def new_run(self, name: str | None = None) -> Path:
        stamp = name or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        run = self.runs / stamp
        suffix = 1
        while run.exists():
            suffix += 1
            run = self.runs / f"{stamp}-{suffix}"
        run.mkdir(parents=True)
        return run

    def run_dir(self, path: str | Path) -> Path:
        """Accept an absolute run dir, a path relative to cwd, or a bare run name."""
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate
        if candidate.exists():
            return candidate.resolve()
        if len(candidate.parts) > 1:
            # An explicit relative path (runs/abc, ../x) means what it says.
            return (Path.cwd() / candidate).resolve()
        return (self.runs / candidate).resolve()

    def stats(self) -> dict:
        return {
            "home": str(self.home),
            "candidate": self.candidate,
            "candidate_dir": str(self.candidate_dir),
            "cv_dir": str(self.cv_dir),
            "cvs": [path.name for path in self.cvs],
            "profile": self.profile.is_file(),
            "search_terms": self.search_terms.is_file(),
            "database": self.db.is_file(),
            "session": self.session.is_dir(),
            "runs": len([p for p in self.runs.iterdir() if p.is_dir()])
            if self.runs.is_dir()
            else 0,
        }


def resolve_workspace(home: Path, explicit_candidate: str | None = None) -> Workspace:
    """Build a Workspace for an already-resolved home."""
    return Workspace(home, resolve_candidate(home, explicit_candidate))


def workspace_from_args(args) -> Workspace:
    """Resolve --home/--candidate or the config, turning errors into JSON failures."""
    try:
        home = resolve_home(getattr(args, "home", None))
        return resolve_workspace(home, getattr(args, "candidate", None)).ensure()
    except WorkspaceError as exc:
        fail(
            str(exc),
            code=EXIT_CONFIG,
            fix="init --home <path> [--candidate <name>]",
        )


def add_common_arguments(parser) -> None:
    """Add the flags every subcommand accepts."""
    parser.add_argument(
        "--home",
        default=None,
        help=f"Workspace directory. Overrides ${ENV_VAR} and {CONFIG_DIR_NAME}/{CONFIG_FILE_NAME}.",
    )
    parser.add_argument(
        "--candidate",
        default=None,
        help=f"Candidate folder under <home>/{CANDIDATES_DIR_NAME}. "
        f"Overrides ${ENV_VAR_CANDIDATE} and the config file.",
    )


def add_init_subcommand(subparsers, script_name: str) -> None:
    """Register `init`, so any skill can bootstrap a workspace on its own."""
    parser = subparsers.add_parser(
        "init",
        help="Create a workspace and remember it in .jobsearch/config.json.",
    )
    parser.add_argument(
        "--home",
        default=None,
        help=f"Where to keep state. Default: ./{DEFAULT_HOME} in the current directory.",
    )
    parser.add_argument(
        "--candidate",
        default=None,
        help=f"Create <home>/{CANDIDATES_DIR_NAME}/<name>/cv and select it.",
    )
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Directory that receives .jobsearch/config.json. Default: current directory.",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Create the directory but do not write a config file.",
    )
    parser.set_defaults(handler=handle_init, script_name=script_name)


def handle_init(args) -> None:
    home = Path(args.home or DEFAULT_HOME).expanduser().resolve()
    slug = slugify(args.candidate) if args.candidate else None
    workspace = Workspace(home, slug).ensure()
    payload = {"ok": True, **workspace.stats()}
    if args.no_config:
        payload["config"] = None
    else:
        config_path = write_config(
            home, Path(args.config_dir) if args.config_dir else None, candidate=slug
        )
        payload["config"] = str(config_path)
        payload["gitignore_hint"] = f"{CONFIG_DIR_NAME}/"
    emit(payload)
