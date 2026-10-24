---
name: job-search
description: Collect LinkedIn job postings for a set of search terms and store their full descriptions locally, with a SQLite cache so repeat runs skip postings it has already seen. Use when the user wants to search LinkedIn for jobs, refresh a job search, scrape a job listing URL, or gather job descriptions to score. Hands off a run directory for the match-jobs skill.
---

# Job Search

Collects **Job Descriptions** from LinkedIn into a run directory. This skill makes
no judgments: search, paginate, dedupe, cache, fetch. Scoring is the `match-jobs`
skill's job.

Paths below are relative to this skill's directory. Resolve that directory first,
then run the commands with absolute paths.

## Step 1: resolve the workspace

```
uv run scripts/jobsearch.py doctor
```

`uv run` reads the script's inline dependency block, so there is nothing to
install first.

Three things can come back:

- **The `workspace` check fails.** `doctor` exits 1 whenever a critical check
  fails, which is normal until setup is finished: read the JSON rather than
  treating the exit code as a crash. Ask the user where state should live,
  suggesting `./resource` in the current directory. Then:

  ```
  uv run scripts/jobsearch.py init --home <path>
  ```

  This creates the directory and records it in `.jobsearch/config.json` in the
  current directory. If the user is in a git repo, tell them to add `.jobsearch/`
  to `.gitignore`. Never edit `.gitignore` yourself without asking.

- **`chromium` failed.** Run `uv run --with playwright playwright install chromium`,
  then `doctor` again.

- **`session_dir` failed, or the user has never logged in.**

  ```
  uv run scripts/jobsearch.py login
  ```

  A browser window opens. Tell the user to sign in there, including any 2FA. The
  command blocks until LinkedIn's auth cookie appears, up to ten minutes. Do not
  start a run before this succeeds.

`search_terms` and `profile` appear in `doctor` as non-critical checks. If either
is missing, use the `job-search-terms` or `candidate-profile` skill.

## Step 2: check the search terms

`<home>/search_terms.json` must be a bare JSON array of strings:

```json
["Software Architect", "Head of Software", "Medical Device Software"]
```

If it is missing, run the `job-search-terms` skill, or ask the user for terms.
Never invent terms and never write this file yourself from this skill.

## Step 3: collect

```
uv run scripts/jobsearch.py collect --source terms --past-days 7
```

It prints JSON with the run directory, the descriptions file, and counts. The
happy path is one command. Use the variants only when the user asked for them:

| User said | Command |
|---|---|
| "search for jobs" | `--source terms` |
| "check my recommended jobs" | `--source recommended` |
| "these jobs, from this URL" | `--source url --url "<listing url>"` |
| "only the last 24 hours" | `--past-days 1` |
| "remote only" | `--workplace 2` |
| "somewhere other than Cambridgeshire" | `--geo-id <id>` |

Other flags worth knowing: `--max-terms` (default 12), `--max-pages` (default 5),
`--headless`, `--strict` (exit 3 if any description failed), `--delay` (default
1.0s, do not lower it).

## Step 4: report back

Use the numbers from the JSON, not from your own reading of the file:

- how many unique jobs were found
- how many came from the cache and how many were scraped
- how many descriptions failed
- the run directory path

If **count is 0**, the terms are too narrow or the geo filter is wrong. Say so and
suggest the `job-search-terms` skill. Do not silently retry with invented terms.

If **failed is greater than 0**, mention it. Those jobs are in the output with an
empty description, and they were deliberately not cached, so a later run retries
them.

## What it writes

All under the workspace, never inside this skill's directory:

| Path | Meaning |
|---|---|
| `<home>/jobs.db` | Description cache, keyed by job URL. Shared by every run. |
| `<home>/session/` | Persistent Chromium profile. Never commit it. |
| `<home>/runs/<timestamp>/descriptions.json` | The Job Descriptions. |
| `<home>/runs/<timestamp>/run.json` | Provenance: terms, source, counts, hashes. |

Only ever pass the run directory from the JSON output to the next skill. Do not
construct run paths by hand.

## Rules

- **Never write inside this skill's directory.** It may be a read-only install.
- **Never edit `descriptions.json`.** It is the input contract for `match-jobs`.
- **Do not raise the caps.** `--max-terms` and `--max-pages` exist to stay inside
  what LinkedIn tolerates from an automated session. If the user wants more, ask
  them to accept the risk explicitly.
- **Do not retry a failed run more than once.** Report what happened and stop.
- **If the session expired** (exit 4), tell the user to run `login`. Do not loop.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Worked |
| 1 | `doctor` found a failed critical check |
| 2 | Configuration problem, for example no workspace |
| 3 | The work could not be completed, for example no search terms |
| 4 | No LinkedIn session, or it expired mid-run |

## Next

Hand the run directory to the `match-jobs` skill.
