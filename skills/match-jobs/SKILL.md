---
name: match-jobs
description: Score collected job descriptions against a candidate profile and render a ranked Markdown Match Report. Use when the user wants to rank jobs, find their best-fitting jobs, score postings against their CV, check whether a single job is worth applying to, or regenerate a job match report.
---

# Match Jobs

Scores **Job Descriptions** against the **Candidate Profile**. The script does the
mechanical parts: splitting into chunks, enforcing the JSON contract, merging,
sorting and rendering. You supply the judgement, and the judgement lives in
`reference/matching-rubric.md`.

Read that rubric before scoring your first chunk. Do not restate it to the user,
and do not paste it into the conversation.

Paths below are relative to this skill's directory. Resolve that directory first.

## Before you start

Two inputs are required:

- `<candidate>/profile.json`, produced by the `candidate-profile` skill
- a run directory from the `job-search` skill, containing
  `descriptions.json` (and ideally `run.json`)

`<candidate>` is `<home>/candidates/<name>`. If the workspace has more than one
candidate and none is configured, pass `--candidate <name>`.

If a command reports that no workspace is configured, ask the user where state
should live (default `./resource`) and run `init --home <path>`.

If the profile or the run is missing, stop and name the skill that produces it.
Do not fabricate either one.

## Step 1: prepare the chunks

```
uv run scripts/jobmatch.py prepare --run <run-dir> --size 10
```

Chunk size:

- **More than 30 jobs:** `--size 10`. Each chunk is one model turn.
- **30 jobs or fewer:** `--size 1000`. One chunk, scored in a single pass. Do not
  pay for chunk ceremony on a small run.

The output lists every chunk with its status, and a `pending` array. If everything
is already scored, skip to Step 3. `--status` reports without writing anything.

## Step 2: score each pending chunk

For each chunk index in `pending`:

1. Read `<run-dir>/chunks/chunk-NN.json`. It is an envelope: `index`, `total`,
   `jobs`. Score only the jobs in this chunk.
2. Read `<home>/profile.json`.
3. Write `<run-dir>/matches/chunk-NN.json`:

```json
{
  "chunk": 1,
  "match_results": [
    {
      "title": "...", "url": "...", "company": "...", "location": "...",
      "description": "...",
      "domain": "...", "salary_range": "...", "match_score": 0,
      "reasoning": "...", "strengths": "...", "gaps": "..."
    }
  ]
}
```

Rules the script enforces, which you should not fight:

- **Exactly one result per job in the chunk.** Same `url` values, no extras, no
  omissions. A missing job makes the whole run refuse to render.
- **Copy `title`, `url`, `company`, `location` and `description` verbatim** from
  the chunk. The description goes into the report inside a collapsible block.
  Do not summarise it.
- **`match_score` is an integer 0 to 100.** Not a float, not a string, not a
  percentage.
- **`salary_range` is `"Not stated"`** when the posting does not say. Do not infer
  a market rate.

If you are unsure what the shape is, print it:

```
uv run scripts/jobmatch.py schema --name match-batch
```

Validate a chunk before moving on (cheap, catches a whole class of retries):

```
uv run scripts/jobmatch.py validate --in <run-dir>/matches/chunk-01.json --schema match-batch
```

A chunk at a time, then continue. Do not batch several chunks into one write: a
single malformed field invalidates the chunk, and smaller writes are easier to
recover from.

## Step 3: render the report

```
uv run scripts/jobmatch.py report --run <run-dir> --min-score 75
```

It writes:

| Path | Contents |
|---|---|
| `<run-dir>/report.md` | The Match Report, sorted by score, above the threshold |
| `<run-dir>/matches.all.json` | **Every** scored job, including the ones below the threshold, plus the band counts |

It refuses to render while any chunk is unscored or inconsistent, and names the
chunks that need work. That refusal is the resume mechanism: fix the named
chunks, run `report` again, and nothing already scored is lost.

The default threshold is 75 because that is what the previous implementation
reported. If the user asks for a different cutoff, pass `--min-score`. Never
lower it on your own to make the output look fuller.

## Step 4: report back

- The **top five** by score: title, company, score, and one sentence of reasoning.
- The path to `report.md`, so the user can open the full document.
- How many jobs scored below the threshold. They are still in `matches.all.json`.

Do not paste the whole report into chat. It is long, and it contains full job
descriptions.

## Scoring a single job

For "is this job worth applying to", use the two skills together:

```
uv run ../job-search/scripts/jobsearch.py describe --url "<job url>" --out /tmp/job.json
```

Then score that one job against `<home>/profile.json`, write the single result to
`/tmp/match.json`, and render it:

```
uv run scripts/jobmatch.py fit --match /tmp/match.json
```

`fit` validates the result and prints the same Markdown block the report uses,
without creating a run.

## Rules

- **Never invent a job.** No URL, title or description that is not in the chunk.
- **Never edit `matches.all.json` or `report.md`.** They are rendered, not authored.
- **Never rewrite `descriptions.json`.** If a description is empty, score it low
  and say the description was unavailable in `gaps`.
- **Never score a chunk you did not read in full.** Hidden truncation produces
  confident nonsense.
- **Be conservative.** A prompt that inflates scores is worse than one that
  under-reports, because the user acts on the top of the list.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Worked |
| 2 | Configuration problem, for example no workspace |
| 3 | The run is incomplete, or a file does not satisfy the contract |
