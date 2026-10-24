# Install and use the job search skills

A walkthrough for someone who wants to use these skills, not develop them. It
takes about ten minutes, plus one LinkedIn sign-in, and ends with a ranked Match
Report of real job postings.

The four skills are plain folders with a `SKILL.md`, some Python and some
reference material. Any harness that can read a skill and run a shell command can
use them. There is nothing to compile and no service to host.

```
candidate-profile  ->  job-search-terms  ->  job-search  ->  match-jobs
```

You supply a CV; the skills produce a profile, search terms, a folder of job
descriptions and a scored report.

## 1. What you need

| Need | Why |
|---|---|
| Linux or macOS | Windows works through WSL |
| `git` | to download and update the skills |
| [`uv`](https://docs.astral.sh/uv/) | runs the scripts with their dependencies, no virtualenv to manage |
| Chromium | installed once for the browser skill |
| A LinkedIn account | the job source |
| An agent harness with skills | OpenCode, Claude Code, or anything that loads `SKILL.md` |
| Google Chrome (optional) | only if your LinkedIn account signs in through Google |

Install `uv` if you do not have it:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

or `brew install uv`. No other Python setup is needed: each script declares its
own dependencies inline and `uv` fetches them on first run.

## 2. Download the skills

```sh
git clone https://github.com/szerming/agentic_skills.git ~/src/agentic_skills
```

Put the clone somewhere stable; the install in the next step links to it. If you
do not use `git`, use **Code → Download ZIP** on GitHub, unzip it, and use that
folder instead.

## 3. Install into your agent

Skills live in a directory your harness scans. Pick the one that matches your
tool:

| Harness | Global skill directory |
|---|---|
| OpenCode | `~/.config/opencode/skills` |
| OpenCode / other agents (compatibility) | `~/.agents/skills` |
| Claude Code | `~/.claude/skills` |

For the rest of this guide, `SKILLS` means the directory you chose:

```sh
export SKILLS=~/.agents/skills
mkdir -p "$SKILLS"
cd ~/src/agentic_skills
for skill in candidate-profile job-search-terms job-search match-jobs; do
  ln -sfn "$PWD/skills/$skill" "$SKILLS/$skill"
done
```

Symlinks mean `git pull` updates the installed skills in place. If symlinks are
not practical on your machine, copy instead and repeat the copy after updates:

```sh
cp -R skills/candidate-profile skills/job-search-terms \
      skills/job-search skills/match-jobs "$SKILLS/"
```

To make the skills available in one project only, use `.agents/skills/` or
`.opencode/skills/` at that project's root.

Check that the harness sees them: open your agent and ask what job search skills
it has, or invoke one by name (`candidate-profile`, `job-search-terms`,
`job-search`, `match-jobs`). OpenCode reloads skills on config save; use
`/reload` or restart the session if the list is stale.

## 4. Set up your workspace

The skills never write inside their own folder. Everything — CV, profile, search
terms, cached postings, reports — goes into a **workspace** you choose.

Pick a private, writable folder outside this repository, for example
`~/jobsearch`. Keep it out of version control: it contains your CV and a
logged-in browser profile.

```sh
mkdir -p ~/jobsearch
cd ~/jobsearch
uv run "$SKILLS/job-search/scripts/jobsearch.py" init --home . --candidate alex
```

`init` creates the folder layout and records it, plus the candidate, in
`.jobsearch/config.json` in the current directory. Later commands run from
`~/jobsearch` (or a subfolder) find that config automatically. If you prefer to
work from anywhere, set the workspace once in your shell profile instead:

```sh
export JOBSEARCH_HOME=~/jobsearch
export JOBSEARCH_CANDIDATE=alex
```

If `~/jobsearch` is inside a git repository, add `.jobsearch/` to that
repository's `.gitignore`.

### 4.1 Install Chromium

```sh
uv run --with playwright playwright install chromium
```

This is the only heavy download (roughly 150 MB), and only the `job-search` skill
needs it.

### 4.2 Sign in to LinkedIn

```sh
uv run "$SKILLS/job-search/scripts/jobsearch.py" login
```

A browser window opens. Sign in there yourself, including any two-factor step.
The command waits until LinkedIn's session cookie appears, up to ten minutes.
The cookie is stored in `~/jobsearch/session/` and reused by later runs.

If Google refuses the sign-in with "this browser or app may not be secure",
borrow a normal Chrome for that one step:

```sh
uv run "$SKILLS/job-search/scripts/jobsearch.py" login --channel chrome
```

The session is shared, so later `collect` runs can keep using the default
browser.

### 4.3 Check the setup

```sh
uv run "$SKILLS/job-search/scripts/jobsearch.py" doctor
```

It prints JSON with one entry per check. A missing `profile` or `search_terms`
is expected at this point — the next section creates them. Any other failure
includes a `fix` line telling you what to run.

## 5. Use the skills

You normally do not type the scripts. Open your agent **in the workspace**
(`cd ~/jobsearch`), or with `JOBSEARCH_HOME` exported, and ask in plain
language. The agent loads the matching skill, runs the mechanical commands and
writes the judgement-based files.

### Step 1 — turn your CV into a profile

> Use the candidate-profile skill on my CV at `~/Documents/cv.pdf`. My name is
> Alex.

The skill copies the CV into the workspace, extracts its text, builds the
Candidate Profile and validates it against the schema. Extraction happens once,
so later runs reuse the JSON rather than re-reading the PDF.

### Step 2 — choose search terms

> Use job-search-terms to propose search terms for my profile. I am looking for
> senior architect roles around Cambridge, UK, and remote is fine.

The skill shows a table of proposed titles and asks before writing
`search_terms.json`. Location is not stored in that file: the scraper filters by
LinkedIn's numeric `geoId`, so say the location in the conversation and let the
skill handle it.

### Step 3 — collect job descriptions

> Use job-search to collect jobs from the last 7 days.

This opens the browser session, searches each term, deduplicates by URL and saves
full descriptions into a timestamped run folder. It reports how many jobs were
found, how many came from the cache and how many failed. Other useful asks:

- "check my recommended jobs" — scrape the LinkedIn recommended feed
- "collect this job: `<listing URL>`" — one pasted posting
- "only the last 24 hours" — narrower `--past-days`

### Step 4 — score and report

> Use match-jobs to score this run and build the Match Report.

The skill splits the run into chunks, scores each job against
`reference/matching-rubric.md`, validates that every job was scored, then renders
the report at:

```
~/jobsearch/candidates/alex/runs/<timestamp>/report.md
```

Open it: jobs are ranked by score, with location, salary, domain, link,
reasoning, strengths, gaps and the full description.

### Afterwards

- Re-run step 3 whenever you want fresh postings. The SQLite cache means already
  seen jobs cost almost nothing the second time.
- Every score is remembered per candidate. Ask "which jobs have I saved?" or
  "what did that architect job score?" and the `match-jobs` lookup answers from
  the database without re-scoring.

## 6. Command reference

When you want to run the mechanics yourself instead of through the agent:

```sh
S=~/.agents/skills   # adjust if you installed somewhere else

# workspace and readiness
uv run "$S/job-search/scripts/jobsearch.py" doctor

# CV -> profile text -> (agent writes profile.json)
uv run "$S/candidate-profile/scripts/jobprofile.py" candidate --candidate alex --cv ~/cv.pdf
uv run "$S/candidate-profile/scripts/jobprofile.py" text --pdf ~/cv.pdf

# collect and score
uv run "$S/job-search/scripts/jobsearch.py" collect --past-days 7
uv run "$S/match-jobs/scripts/jobmatch.py" prepare --run ~/jobsearch/candidates/alex/runs/<stamp>
uv run "$S/match-jobs/scripts/jobmatch.py" report --run ~/jobsearch/candidates/alex/runs/<stamp>

# inspect what is already known
uv run "$S/job-search/scripts/jobsearch.py" cache stats
uv run "$S/match-jobs/scripts/jobmatch.py" lookup --query "architect" --min-score 75
```

Commands print JSON on stdout and progress on stderr. Exit codes are consistent:
`0` worked, `1` `doctor` found a failed check, `2` configuration problem, `3`
work could not be completed or a file broke its contract, `4` no LinkedIn
session or it expired mid-run.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| The agent does not list the skills | Check the folder exists in `$SKILLS` and contains `SKILL.md`; use the right directory for your harness; reload the session |
| "No workspace" or `2` | Run `init`, export `JOBSEARCH_HOME`, or run from the directory holding `.jobsearch/config.json` |
| `chromium` check fails | `uv run --with playwright playwright install chromium` |
| Google says the browser is not secure | `login --channel chrome`, or pass `--executable-path /path/to/chrome` |
| Login expired mid-run (`4`) | Run `login` again; do not loop the collection |
| Zero jobs found | Terms are too narrow or the `geoId` is wrong — revisit `job-search-terms` |
| "Ambiguous candidate" | Pass `--candidate <name>`, or set `JOBSEARCH_CANDIDATE` |
| `report` refuses to render | It names the unscored chunk and job; score that chunk, then run `report` again |
| Permission denied | The workspace must be writable by you and outside a read-only skill install |

## 8. Privacy and housekeeping

- Your CV, profile, jobs and search history stay in the workspace; nothing is
  written into the skill folder.
- `session/` is a signed-in browser profile. Treat it like a password: never
  commit it, never share it.
- The model sees the CV text, the search terms and the job descriptions. If that
  matters to you, check your harness's data policy first.
- Job data comes from LinkedIn by automating a signed-in browser. Keep the
  default one-second delay, run modest batches, and respect LinkedIn's terms.
- Update the skills with `git -C ~/src/agentic_skills pull`. Symlinked installs
  pick the change up immediately; copied installs need copying again.

The rule list and command details for each skill are in its `SKILL.md`; the
reasoning behind the design is in [README.md](./README.md).
