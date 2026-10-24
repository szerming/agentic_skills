"""Workspace resolution shared by the job-search skills.

Identical copies of this file live in candidate-profile/, job-search/ and
match-jobs/. tests/test_drift.py fails if the copies diverge.

Resolution order:
  1. an explicit --home argument
  2. $JOBSEARCH_HOME
  3. .jobsearch/config.json, walking up from the current directory
  4. fail, with instructions. The skill then asks the user where state should
     live (default: ./resource next to the harness's working directory).

Nothing is ever written inside the skill's own directory: skills get copied or
symlinked into a read-only harness install, and a skill that writes next to
__file__ corrupts that install.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR_NAME = ".jobsearch"
CONFIG_FILE_NAME = "config.json"
ENV_VAR = "JOBSEARCH_HOME"
DEFAULT_HOME = "resource"

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


def find_config(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for .jobsearch/config.json."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        config = candidate / CONFIG_DIR_NAME / CONFIG_FILE_NAME
        if config.is_file():
            return config
    return None


def home_from_config(config_path: Path) -> Path:
    """Read a config file. Relative `home` values resolve against the config's directory."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"{config_path} is not readable JSON: {exc}") from exc

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


def write_config(home: Path, config_dir: Path | None = None) -> Path:
    """Write .jobsearch/config.json under `config_dir` (default: cwd)."""
    target_dir = (config_dir or Path.cwd()).resolve() / CONFIG_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    config_path = target_dir / CONFIG_FILE_NAME
    config_path.write_text(json.dumps({"home": str(home)}, indent=2) + "\n", encoding="utf-8")
    return config_path


@dataclass(frozen=True)
class Workspace:
    """Every path a skill may write to. All of them live under `home`."""

    home: Path

    @property
    def profile(self) -> Path:
        return self.home / "profile.json"

    @property
    def search_terms(self) -> Path:
        return self.home / "search_terms.json"

    @property
    def db(self) -> Path:
        return self.home / "jobs.db"

    @property
    def runs(self) -> Path:
        return self.home / "runs"

    @property
    def session(self) -> Path:
        return self.home / "session"

    def ensure(self) -> "Workspace":
        self.home.mkdir(parents=True, exist_ok=True)
        self.runs.mkdir(parents=True, exist_ok=True)
        return self

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
        return (self.runs / candidate).resolve()

    def stats(self) -> dict:
        return {
            "home": str(self.home),
            "profile": self.profile.is_file(),
            "search_terms": self.search_terms.is_file(),
            "database": self.db.is_file(),
            "session": self.session.is_dir(),
            "runs": len([p for p in self.runs.iterdir() if p.is_dir()])
            if self.runs.is_dir()
            else 0,
        }


def workspace_from_args(args) -> Workspace:
    """Resolve --home or the config, turning WorkspaceError into a JSON failure."""
    try:
        return Workspace(resolve_home(getattr(args, "home", None))).ensure()
    except WorkspaceError as exc:
        fail(str(exc), code=EXIT_CONFIG, fix="init --home <path>")


def add_common_arguments(parser) -> None:
    """Add the flags every subcommand accepts."""
    parser.add_argument(
        "--home",
        default=None,
        help=f"Workspace directory. Overrides ${ENV_VAR} and {CONFIG_DIR_NAME}/{CONFIG_FILE_NAME}.",
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
    workspace = Workspace(home).ensure()
    payload = {"ok": True, **workspace.stats()}
    if args.no_config:
        payload["config"] = None
    else:
        config_path = write_config(home, Path(args.config_dir) if args.config_dir else None)
        payload["config"] = str(config_path)
        payload["gitignore_hint"] = f"{CONFIG_DIR_NAME}/"
    emit(payload)
