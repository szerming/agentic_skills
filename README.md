# agentic_skills

[![CI](https://github.com/szerming/agentic_skills/actions/workflows/ci.yml/badge.svg)](https://github.com/szerming/agentic_skills/actions/workflows/ci.yml)

A collection of agentic skills I have used.

Each skill is a self-contained folder: a `SKILL.md` that says when to reach for it
and how to run it, vendored Python where the work is mechanical, and reference
material loaded only when it is needed. Any harness that can read a skill and run
a shell command can use them. There is no MCP server, no plugin API and no agent
framework to install.

**New here?** [INSTALL.md](./INSTALL.md) walks through downloading, installing
and running the job search skills for the first time, from a CV to a Match
Report.

## The skills

| Skill | What it gives you | Needs | Scripts |
|---|---|---|---|
| [candidate-profile](./skills/candidate-profile/SKILL.md) | A reusable `CandidateProfile` JSON extracted from a CV | a CV PDF | `jobprofile.py` (pypdf) |
| [job-search-terms](./skills/job-search-terms/SKILL.md) | Search terms and a location that fit that profile | a profile | none, pure judgement |
| [job-search](./skills/job-search/SKILL.md) | Job Descriptions from LinkedIn, with a cache so repeat runs refetch nothing | search terms, a LinkedIn session | `jobsearch.py` (Playwright) |
| [match-jobs](./skills/match-jobs/SKILL.md) | Scores, a ranked Markdown Match Report, and lookup of what was already saved | a profile and a run | `jobmatch.py` (pydantic) |

The four compose into one pipeline. Run them out of order and the scripts refuse,
naming the skill that produces what is missing.

```
candidate-profile      CV PDF          -> <candidate>/cv/ + <candidate>/profile.json
        |
job-search-terms       profile         -> <candidate>/search_terms.json
        |
job-search             terms           -> <run>/descriptions.json   (+ <candidate>/jobs.db)
        |
match-jobs             profile + run   -> <run>/matches/*.json -> <run>/report.md (+ scores in jobs.db)
```

`<candidate>` is `<home>/candidates/<name>`: one folder per person, holding their
CV, profile, terms, cache and runs. See [Workspace](#workspace).

## job-search: what the reader gets

### candidate-profile

Turns a CV into the structured profile every matching run scores against. The PDF
is parsed to text once, the model structures it once, and the JSON is reused
forever, so no tokens are spent re-reading the same CV on every search.

What it can do:

- extract text from a PDF, including scanned-looking layouts, using pypdf's layout
  mode (falls back gracefully on older pypdf)
- clean the whitespace noise pypdf leaves behind without destroying paragraph
  structure
- validate the resulting JSON against the `CandidateProfile` schema and report each
  field that fails
- print the schema it is aiming at, so the model writes to a contract rather than
  guessing
- find or prepare the candidate folder, and say what is still missing: create the
  folder, copy in a supplied CV, or report that it is ready to extract

What it produces: the CV copied into `<candidate>/cv/`, its text at
`<candidate>/cv.txt`, and `<candidate>/profile.json`, holding headline,
seniority, years of experience, hard and soft skills, domains, target titles,
strengths, evidence snippets and the full experience and education history.

Its rules are in the skill, not the code: only facts the CV supports, conservative
inference of seniority, evidence-backed strengths, and ask before overwriting a
profile the user may have hand-edited. Degree dates are optional in the schema,
because CVs often omit them and inventing them would break the first rule.

```sh
uv run skills/candidate-profile/scripts/jobprofile.py candidate
uv run skills/candidate-profile/scripts/jobprofile.py candidate --candidate alex --cv ~/cv.pdf
uv run skills/candidate-profile/scripts/jobprofile.py text --pdf ~/cv.pdf
uv run skills/candidate-profile/scripts/jobprofile.py validate \
  --in resource/candidates/alex/profile.json
uv run skills/candidate-profile/scripts/jobprofile.py schema
```

`candidate` returns a `next` block rather than failing when state is incomplete:
`create_candidate`, `drop_cv`, `extract_profile` or `ready`. Supplying a CV from
anywhere copies it into the candidate folder, so the next run finds it without
being told where it came from.

### job-search-terms

Turns a profile into the terms LinkedIn is actually searched with. This skill has
no scripts: choosing titles is judgement, and a script would only get in the way.

What it can do:

- propose five to ten concrete titles, covering adjacent levels and specialisms
  rather than six variants of one title
- use LinkedIn's boolean syntax for near-synonyms, for example
  `("Solution Architect") OR ("System Architect")`
- compare the proposal with the terms already in `search_terms.json` and say
  explicitly what would be removed
- explain the location trap: the scraper filters by a fixed LinkedIn `geoId`, so a
  city name in the terms file does nothing

What it produces: `<candidate>/search_terms.json`, a bare JSON array, and only
after the user has seen the proposed table and agreed. The cap is twelve terms per
run.

### job-search

Collects Job Descriptions from LinkedIn. This is the only skill that touches a
browser, and the only one with a heavy dependency.

What it can do:

- search by keyword, or scrape your recommended feed, or scrape any job listing
  URL you paste in
- paginate up to five pages per term, twenty-five results per page
- dedupe across every search term by job URL, so an overlapping result costs one
  fetch, not four
- keep a SQLite cache keyed by URL and skip any posting it has already described,
  which is where the bandwidth saving lives: a second run over the same listing is
  nearly free. The cache is per candidate, so two people in one workspace never
  share postings they did not search for
- resolve LinkedIn's virtualised result list: cards hydrate in batches as the list
  grows and again as each card scrolls into view, so the list is walked rather than
  sampled once
- read the posting from its stable `JobDetails_AboutTheJob_<id>` component, because
  LinkedIn's visible class names are now generated per build
- filter by posting age, LinkedIn `geoId`, seniority level and workplace type
- pace itself with a one second delay between requests, configurable but not
  advisable to lower
- report, per run, how many jobs were found, how many came from cache, how many
  were scraped and how many failed
- never cache a failed fetch, so a transient failure is retried on the next run
  instead of being remembered as an empty posting
- dump the page HTML for a posting when a selector breaks, so the fix is one file

What it produces: a run directory with `descriptions.json` (the `JobDescription`
contract) and `run.json` (source, terms, counts, description hash), plus the
candidate's cache and the persistent browser session.

| Command | Purpose |
|---|---|
| `doctor` | Browser build, session, workspace writability, candidate, terms, profile and cache, as JSON with a `fix` per failure |
| `login` | Opens a browser and waits for the LinkedIn auth cookie, up to ten minutes |
| `collect` | The happy path: search, dedupe, describe, cache |
| `describe` | One posting, for debugging selectors |
| `cache stats` / `cache prune` | Inspect or trim the description cache |
| `validate` / `schema` | Contract check and JSON Schema |

`login`, `collect`, `describe` and `doctor` accept `--candidate <name>` when the
workspace holds more than one candidate, and `--channel chrome` or
`--executable-path <chrome>` when Google refuses OAuth from Playwright's Chromium
("this browser or app may not be secure"). The signed-in profile is shared, so the
browser used for sign-in does not have to be the one used for scraping.

```sh
uv run skills/job-search/scripts/jobsearch.py doctor
uv run skills/job-search/scripts/jobsearch.py collect --past-days 7
uv run skills/job-search/scripts/jobsearch.py collect --source recommended
uv run skills/job-search/scripts/jobsearch.py collect --source url --url "<listing url>"
uv run skills/job-search/scripts/jobsearch.py cache stats
```

### match-jobs

Scores collected postings against the profile and renders the report. The script
owns everything deterministic; the model owns the judgement, using
`reference/matching-rubric.md`.

What it can do:

- split a run into chunks of ten postings, so each job keeps the model's attention,
  or collapse a run of thirty or fewer into a single pass
- enforce exactly one result per job, with matching URLs, before anything is
  rendered. A chunk that skipped a job invalidates the run rather than quietly
  producing a shorter report
- refuse to render while any chunk is unscored, and name the chunk and the job
  that is missing. That refusal is the resume mechanism: fix the named chunks, run
  `report` again, and nothing already scored is lost
- merge, sort and render the Match Report, with every score kept in
  `matches.all.json` and only the ones at or above the threshold in `report.md`
- count results by score band, and persist those counts alongside the scores, so
  the shape of the run is visible at a glance
- score a single pasted job with `fit`, without creating a run
- remember every score in the candidate's `jobs.db`, keyed by job URL, and answer
  "what did this score?" or "which jobs have I saved?" with `lookup`, without
  re-scoring or walking the run directories. Collected-but-unscored postings are
  listed too, so the gap between found and scored is visible

The rubric it follows weights skills at 40%, experience relevance at 20%, domain
alignment and seniority at 15% each, and tools at 10%, with bands from excellent
(90+) down to poor (under 40). It is a document, so it can be reviewed and changed
like any other document.

```sh
uv run skills/match-jobs/scripts/jobmatch.py prepare --run resource/candidates/alex/runs/<stamp>
uv run skills/match-jobs/scripts/jobmatch.py report --run resource/candidates/alex/runs/<stamp>
uv run skills/match-jobs/scripts/jobmatch.py fit --match /tmp/one-match.json
uv run skills/match-jobs/scripts/jobmatch.py lookup --query "architect" --min-score 75
```

What it produces: `chunks/`, `matches/`, `matches.all.json` and `report.md`, all
inside the run directory, plus a `match_results` table in `<candidate>/jobs.db`.
The report lists each job with score, location, salary, domain, link, reasoning,
strengths, gaps and a collapsible full description. `lookup` reads that database
only: `matches` are saved scores, `unscored` are descriptions collected by
job-search that have never been scored.

## What one run looks like

```sh
# once per machine
uv run skills/job-search/scripts/jobsearch.py init --home ./resource --candidate alex
uv run --with playwright playwright install chromium
uv run skills/job-search/scripts/jobsearch.py login
# add --channel chrome if Google says the browser is not secure

# once per candidate
uv run skills/candidate-profile/scripts/jobprofile.py candidate \
  --candidate alex --cv ~/cv.pdf
# the model then extracts and validates resource/candidates/alex/profile.json

# once per search, then whenever you want fresh jobs
uv run skills/job-search/scripts/jobsearch.py collect --past-days 7

# scoring, chunk by chunk, then the report
uv run skills/match-jobs/scripts/jobmatch.py prepare \
  --run resource/candidates/alex/runs/<stamp>
uv run skills/match-jobs/scripts/jobmatch.py report \
  --run resource/candidates/alex/runs/<stamp>
```

The commands in the middle, the ones that write `profile.json`, `search_terms.json`
and each `matches/chunk-NN.json`, are the model's work and are described in the
relevant `SKILL.md`. The scripts exist so that the model never has to do the
mechanical parts.

## Workspace

Nothing is written inside a skill folder: skills get copied or symlinked into a
harness install that may be read-only. All state lives in a workspace, resolved
in this order:

1. `--home <path>`
2. `$JOBSEARCH_HOME`
3. `.jobsearch/config.json`, walking up from the current directory
4. otherwise the skill asks the user, defaulting to `./resource`

Within a workspace, the candidate is resolved independently:

1. `--candidate <name>`
2. `$JOBSEARCH_CANDIDATE`
3. the `candidate` key in `.jobsearch/config.json`
4. the only folder under `<home>/candidates`, when there is exactly one
5. otherwise the flat, pre-candidate layout: `<home>` itself

```
<home>/
├── session/                       persistent Chromium profile, never commit
└── candidates/<name>/
    ├── cv/                        the original CV, exactly as supplied
    ├── cv.txt                     text extracted from it
    ├── profile.json               the Candidate Profile
    ├── search_terms.json          the Search Terms
    ├── jobs.db                    description cache + saved match scores
    └── runs/<timestamp>/
        ├── descriptions.json      the run's input
        ├── run.json               provenance: source, terms, counts, hash
        ├── chunks/chunk-NN.json   what the model scores
        ├── matches/chunk-NN.json  what the model scored
        ├── matches.all.json       every score, plus band counts
        └── report.md              the ranked Match Report
```

The session sits at workspace level because it belongs to the LinkedIn account,
not to a person. Everything else is per candidate, so one workspace can hold
several people without mixing caches, profiles or reports. A workspace created
before this layout (profile, terms, cache and runs directly under `<home>`) keeps
working; `init --candidate` creates the new shape.

## How it is put together

Every agent in the previous pydantic-ai implementation was doing one of two things:
deciding, or typing. Deciding, which is scoring a job, structuring a CV and
choosing search terms, is what a harness's model already does. Typing, which is
paging through LinkedIn, deduping URLs, merging chunks and rendering Markdown, is
what a script does better, cheaper and identically every time.

So the mechanical half is Python, the judgement half is prose, and the seam
between them is a JSON file described by a schema:

- **Contracts are schemas.** `job_models.py` is the source of truth, committed
  JSON Schemas sit next to each skill in `reference/`, and every consumer
  validates its input before trusting it. This is what replaces an agent
  framework's typed output.
- **Chunks are files.** Matching state lives on disk, so a long run is resumable
  and every intermediate score is inspectable.
- **The cache is per candidate.** One `jobs.db` under each candidate, used by
  every run for that person, so a shared workspace never mixes their postings.
  It holds both caches: descriptions from job-search and scores from match-jobs,
  which is why "what have I already seen, and what did it score?" is one query.
- **Output is machine-readable.** Every command prints JSON on stdout and progress
  on stderr, so a harness never has to parse prose. The one exception is
  `jobmatch.py fit`, which prints the rendered Markdown block because showing it
  to the user is the whole point; pass `--json` to get the data instead.

Exit codes are consistent enough to branch on:

| Code | Meaning |
|---|---|
| 0 | Worked |
| 1 | `doctor` found a failed critical check |
| 2 | Configuration problem, for example no workspace, an ambiguous candidate, or a browser that will not launch |
| 3 | The work could not be completed, or a file broke its contract |
| 4 | No LinkedIn session, or it expired mid-run |

## Requirements

- `uv`, which provisions each script's dependencies from its inline PEP 723 header
- Chromium for Playwright, installed once
- A LinkedIn account, for the first `login`
- A stock Chrome, only if your LinkedIn account signs in through Google: Google
  refuses OAuth from automation-flagged Chromium, so `login --channel chrome`
  borrows a real browser for the sign-in and the shared profile keeps the cookie

## Install

The short version: copy or symlink each skill folder into a harness skill
directory, for example `~/.agents/skills/` or `~/.claude/skills/`:

```sh
for skill in candidate-profile job-search-terms job-search match-jobs; do
  ln -s "$PWD/skills/$skill" ~/.agents/skills/$skill
done
```

The skills are independent enough to install separately, but the pipeline needs
all four, because each one names the skill that feeds it.

For the full user-facing walkthrough — choosing a skill directory, setting up a
workspace, installing Chromium, signing in to LinkedIn, using the skills through
an agent and troubleshooting — see [INSTALL.md](./INSTALL.md).

## Layout

```
skills/<name>/SKILL.md        the skill: trigger, workflow, rules
skills/<name>/scripts/        vendored Python, PEP 723, no install step
skills/<name>/reference/      schemas and rubrics, loaded only when needed
tests/                        dev-only; proves the deterministic half
```

`job_models.py` and `job_workspace.py` are duplicated across the three
script-bearing skills so each folder stands alone. `tests/test_drift.py` fails if
the copies diverge.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

CI runs the test suite on Python 3.12 and 3.13, plus the ruff lint and format
checks, for every push to `main` and every pull request; see
`.github/workflows/ci.yml`. Ruff is configured for a 100-character line length in
`pyproject.toml`.

The tests cover everything deterministic: URL construction, dedupe, the cache and
its prune, the collect wiring end to end with a faked browser, chunking, contract
enforcement, report rendering and the committed schemas. `test_models_parity.py`
imports the previous pydantic-ai implementation read-only and asserts the Match
Report Markdown is byte-identical to it; set `JOBSEARCH_REFERENCE` to that
checkout to run it, otherwise those tests skip.

Regenerate the schemas after changing `job_models.py`:

```sh
uv run python scripts/generate_schemas.py
```

## Adding a skill

1. `skills/<name>/SKILL.md` with `name` and `description` frontmatter. The name
   must match the folder, and `description` must carry the phrases a user or model
   would actually say.
2. Deterministic work goes in `scripts/`; judgement stays in prose.
3. Supporting material goes in `reference/`, referenced when needed, never inlined
   into the workflow.
4. If the skill writes state, resolve the workspace through `job_workspace.py`
   (copy it in) and never write inside the skill folder.

## Agent configuration

`AGENTS.md` and `docs/agents/` record where issues live, which triage labels are
in use, and how the domain docs are laid out. `GLOSSARY.md` fixes the vocabulary
the skills use: Job Entry, Job Description, Search Term, Candidate Profile, Match
Result, Match Report, Chunk, Run and Workspace.
