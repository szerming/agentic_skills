# agentic_skills

A collection of standalone agentic skills. Each skill is a folder holding a
`SKILL.md`, the scripts it needs, and any reference material it defers until
needed. Any harness that can read a skill and run a shell command can use one.

## Language

### Skills

**Skill**:
A folder containing `SKILL.md` plus whatever that skill vendors: scripts, schemas,
rubrics. Loadable on its own, with no install step beyond the runtime it declares.
_Avoid_: Plugin, tool, agent definition

**Harness**:
The agent runtime that loads a `SKILL.md` and runs its commands. Claude Code,
OpenCode and Codex are harnesses. Nothing here is written against one of them.
_Avoid_: Framework, runner

**Vendored script**:
A Python file inside a skill that declares its own dependencies in a PEP 723
header, so `uv run` provisions it on first use. Vendored means copied into the
skill, not imported from a package.
_Avoid_: Helper, utility script

### Job search

**Job Entry**:
One row on a LinkedIn results page: title, company, location and posting URL, but
no posting text.
_Avoid_: Card, listing, result

**Job Description**:
A Job Entry plus the full posting text scraped from its URL. The unit the matcher
scores.
_Avoid_: Posting, ad, vacancy

**Search Term**:
One LinkedIn query string. Boolean expressions are permitted.
_Avoid_: Keyword, query

**Search Terms File**:
`<workspace>/search_terms.json`, a bare JSON array of Search Terms. The only input
to a keyword search.
_Avoid_: Search config, query file

**Candidate Profile**:
The structured form of a CV, stored at `<workspace>/profile.json` and used as the
input to matching.
_Avoid_: CV, resume (those are the source documents, not the profile)

**Match Result**:
A Job Description plus a 0 to 100 match score, domain, salary range, and the
matcher's reasoning, strengths and gaps.
_Avoid_: JobMatch, score

**Match Report**:
The Markdown document aggregating Match Results, sorted by score, produced once
per run.
_Avoid_: Output, results

**Match Store**:
The `match_results` table in the candidate's `jobs.db`, keyed by job URL, holding
the latest Match Result for every job ever scored. It outlives the Run that
produced it; `lookup` reads it to answer questions about existing jobs.
_Avoid_: History, cache (that word means descriptions)

**Chunk**:
A batch of Job Descriptions handed to the model in a single turn, ten by default,
so each job keeps the model's attention.
_Avoid_: Page, batch (those mean other things here)

**Run**:
One directory under `<workspace>/runs/` holding the descriptions, chunks,
matches and report for a single search. Resumable: anything already scored is
never rescored.
_Avoid_: Session, job

**Workspace**:
The directory holding the profile, search terms, description cache, session and
runs. Resolved from `--home`, then `$JOBSEARCH_HOME`, then
`.jobsearch/config.json`, then asked of the user.
_Avoid_: Data dir, config dir

**Description cache**:
The `model_store` table in `<candidate>/jobs.db`, an SQLite key/value store keyed
by job URL in the same file as the Match Store. Makes repeat runs cheap and keeps
the load on LinkedIn down.
_Avoid_: Database, store (the file holds both)
