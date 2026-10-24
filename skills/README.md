# Job search skills

Four skills that replace the pydantic-ai LinkedIn job search CLI. They work in any
harness that can load a `SKILL.md`, run a shell command and read a file. No MCP
server, no plugin API, no agent framework.

## The pipeline

```
candidate-profile      CV PDF              -> <candidate>/cv/ + <candidate>/profile.json
        |
job-search-terms       profile             -> <candidate>/search_terms.json
        |
job-search             terms               -> <run>/descriptions.json  (+ <candidate>/jobs.db cache)
        |
match-jobs             profile + run       -> <run>/matches/*.json -> <run>/report.md (+ scores in jobs.db)
```

`<candidate>` is `<home>/candidates/<name>`: one folder per person, holding their
CV, profile, terms, cache and runs.

Run them out of order and the scripts refuse, naming what is missing.

## The skills

| Skill | Needs | Produces | Scripts |
|---|---|---|---|
| [candidate-profile](./candidate-profile/SKILL.md) | a CV PDF | `profile.json` | `jobprofile.py` (pypdf) |
| [job-search-terms](./job-search-terms/SKILL.md) | `profile.json` | `search_terms.json` | none |
| [job-search](./job-search/SKILL.md) | `search_terms.json`, a LinkedIn session | `descriptions.json`, cache | `jobsearch.py` (playwright) |
| [match-jobs](./match-jobs/SKILL.md) | `profile.json`, a run | `matches/*.json`, `report.md`, saved scores | `jobmatch.py` (pydantic) |

`job-search` is deliberately the only skill that touches a browser, and the only
one with a heavy dependency. `job-search-terms` has no scripts at all: it is pure
judgement.

## Why the split is where it is

Every agent that was in the previous implementation was doing one of two things:
deciding, or typing. Deciding (scoring, structuring a CV, choosing terms) is what
the harness's model already does. Typing (paging through LinkedIn, deduping URLs,
merging chunks, rendering Markdown) is what a script does better, cheaper and
identically every time.

So the mechanical half is Python, the judgement half is prose, and the seam
between them is a JSON file described by a schema.

## Workspace

Nothing is written inside a skill folder: skills get copied or symlinked into a
read-only harness install. All state lives in a workspace, resolved in this order:

1. `--home <path>`
2. `$JOBSEARCH_HOME`
3. `.jobsearch/config.json`, walking up from the current directory
4. otherwise the skill asks the user, defaulting to `./resource`

```
<home>/
├── session/                       # persistent Chromium profile, never commit
└── candidates/<name>/
    ├── cv/                        # the original CV
    ├── cv.txt                     # text extracted from it
    ├── profile.json
    ├── search_terms.json
    ├── jobs.db                    # description cache + saved match scores
    └── runs/<timestamp>/
        ├── descriptions.json
        ├── run.json
        ├── chunks/chunk-NN.json
        ├── matches/chunk-NN.json
        ├── matches.all.json       # every score, plus band counts
        └── report.md
```

The cache and runs are per candidate, so one workspace can hold several people
without mixing their postings. The session is account-level and stays at the
workspace root. A candidate is selected with `--candidate`, `$JOBSEARCH_CANDIDATE`
or the `candidate` key in `.jobsearch/config.json`; with exactly one candidate
folder it is found automatically.

## Installing

Copy or symlink each skill folder into a harness skill directory, for example
`~/.agents/skills/` or `~/.claude/skills/`:

```sh
ln -s "$PWD/skills/job-search" ~/.agents/skills/job-search
```

The four skills are independent enough to install separately, but the pipeline
only works with all four present: each one names the skill that feeds it.

Prerequisites:

- `uv` (the scripts declare their own dependencies inline)
- Chromium for Playwright, once: `uv run --with playwright playwright install chromium`
- A LinkedIn account, for the initial `login`

## Development

```sh
uv sync
uv run pytest
```

Tests cover the deterministic half: URL construction, dedupe, cache round trip,
chunking, contract enforcement, report rendering. `tests/test_models_parity.py`
imports the previous pydantic-ai implementation read-only and asserts the Match
Report Markdown is byte-identical.

`job_models.py` and `job_workspace.py` are duplicated across the three
script-bearing skills so each folder stands alone. `tests/test_drift.py` fails if
the copies diverge. After editing the canonical copy in `skills/match-jobs/scripts/`:

```sh
for d in candidate-profile job-search; do
  cp skills/match-jobs/scripts/job_models.py skills/match-jobs/scripts/job_workspace.py \
     skills/$d/scripts/
done
```

## Schemas

The committed JSON Schemas are generated from the pydantic models and are the
human-readable contract. At run time, prefer printing the live schema:

```
uv run skills/match-jobs/scripts/jobmatch.py schema --name match-batch
uv run skills/job-search/scripts/jobsearch.py schema --name description-list
uv run skills/candidate-profile/scripts/jobprofile.py schema
```
