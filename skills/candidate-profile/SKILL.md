---
name: candidate-profile
description: Turn a CV PDF into a structured Candidate Profile JSON that the job matching skills score against, and validate it. Use when the user wants to set up or refresh their CV for job matching, asks to extract or read their CV, or needs a candidate profile before searching for jobs.
---

# Candidate Profile

Converts a CV into the **Candidate Profile** that every matching run scores
against. Extraction happens once; the JSON is reused forever after, so no model
tokens are spent re-reading the same CV.

Paths below are relative to this skill's directory. Resolve that directory first.

## Step 1: resolve the workspace

If a command reports that no workspace is configured, ask the user where state
should live, suggesting `./resource` in the current directory, then:

```
uv run scripts/jobprofile.py init --home <path>
```

If the user is in a git repo, tell them to add `.jobsearch/` to `.gitignore`.

## Step 2: extract the text

```
uv run scripts/jobprofile.py text --pdf /path/to/cv.pdf
```

This writes `<home>/cv.txt` and prints its path, page count, character count and a
short preview. Then **read `<home>/cv.txt` in full**. Do not work from the
preview.

If it fails because the PDF produced no text, it is a scan. Say so, and offer to
work from a text version the user provides instead. Do not guess at the contents.

## Step 3: build the profile

Print the target shape once:

```
uv run scripts/jobprofile.py schema
```

Write `<home>/profile.json`. The fields that matching actually leans on are
`headline`, `seniority`, `years_experience`, `hard_skills`, `domains`,
`target_titles`, `experience` and `strengths`. Fill the rest only when the CV
supports it.

Rules:

- **Only facts the CV supports.** Never invent an employer, a date, a
  qualification or a skill.
- **Infer conservatively.** `seniority` comes from the titles held, not from
  aspiration. `years_experience` comes from the dates, and should be a number.
- **`hard_skills` are concrete**: languages, frameworks, tools, standards,
  methods. Not "leadership".
- **`soft_skills` stay short.** Three or four items, not a personality essay.
- **`target_titles` are roles this CV plausibly wins**, not roles the user dreams
  about. The `job-search-terms` skill builds on these.
- **`strengths` and `unique_qualities` are evidence-backed bullets**, one clause
  each.
- **`evidence_snippets` are verbatim phrases** from the CV that support the
  strongest claims. Quote, do not paraphrase.
- **`visa_constraints` only when stated.** An empty list is the correct answer
  when the CV is silent.

If `<home>/profile.json` already exists, summarise what changed before
overwriting, and ask. A profile the user has hand-edited is worth more than a
fresh extraction.

## Step 4: validate

```
uv run scripts/jobprofile.py validate --in <home>/profile.json
```

Fix what it reports and run it again. Do not hand the user a profile that has not
passed this check.

## Step 5: hand over

Report:

- the profile path
- headline, seniority, years of experience
- the three or four strongest `hard_skills` and `domains`
- anything the CV was thin on, especially if it will weaken matching

Then point at the `job-search-terms` skill.

## Rules

- **Never write inside this skill's directory.**
- **Never leave a CV or profile in the repo.** They live in the workspace.
- **Never interpolate** an obviously questionable field. Leave it out and say why.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Worked |
| 2 | Configuration problem, for example no workspace |
| 3 | The PDF could not be read, or the JSON does not satisfy the contract |
