---
name: job-search-terms
description: Propose LinkedIn search terms and a preferred location from a candidate profile, then write them to search_terms.json. Use when the user asks what job titles to search for, wants to tune or narrow their job search, or is starting a job hunt from scratch.
---

# Job Search Terms

Turns a **Candidate Profile** into the **Search Terms** that the `job-search`
skill feeds to LinkedIn. This skill has no scripts. It is entirely judgement plus
one file write.

## Before you start

Read `<candidate>/profile.json`, where `<candidate>` is
`<home>/candidates/<name>`. If it does not exist, stop and use the
`candidate-profile` skill first. If the workspace holds more than one candidate,
pass `--candidate <name>`.

If a command ever reports that no workspace is configured, ask the user where
state should live (default `./resource`) and run
`uv run ../candidate-profile/scripts/jobprofile.py init --home <path>`.

Read the existing `<home>/search_terms.json` if there is one. Do not propose a
plan that silently discards terms the user chose deliberately.

## Step 1: fill the gaps, briefly

The profile is usually enough. Ask at most three questions, and only when the
answer changes the terms:

- Is the next move a step up, a sideways move, or a deliberate change of domain?
- Which location and working pattern? (This does not go in the terms file; see
  the note on location below.)
- Any industries to avoid, or constraints the CV does not show?

Do not interview the user about things the profile already answers.

## Step 2: propose terms

Aim for **five to ten**, and never more than twelve in one run (`collect` caps at
twelve by default).

- **Use concrete titles**, the words a job advert would use: `Software Architect`,
  `Head of Engineering`, `Principal Firmware Engineer`.
- **Cover two or three adjacent levels or specialisms.** One title misses
  neighbouring roles; six variants of the same title waste the cap.
- **Boolean is allowed and useful** for a pair of near-synonyms:
  `("Solution Architect") OR ("System Architect")`.
- **Quote multi-word phrases** so LinkedIn treats them as phrases.
- **No single generic words.** `Engineer` floods the results with everything.
- **No location, salary or seniority filter in the terms.** Location is a separate
  parameter, and seniority is already applied by the scraper.

## Step 3: show, then ask

Present a table: term, and one clause on why it follows from the profile. Then the
preferred location.

Wait for approval. If `<home>/search_terms.json` already exists, show the terms
being added and removed, and say explicitly that the file will be replaced.

Never write the file before the user agrees.

## Step 4: write

The file is a bare JSON array. No wrapper object, no comments, no trailing
commas:

```json
[
  "Software Architect",
  "Head of Software",
  "(\"Solution Architect\") OR (\"System Architect\")",
  "Medical Device Software",
  "Digital Transformation Architect"
]
```

Report the path and the number of terms, then point at the `job-search` skill.

## A note on location

The preferred location is advisory. The scraper pins a LinkedIn `geoId`, which
defaults to Cambridgeshire (`102057244`) and is not read from this file.

If the user wants a different region, tell them two things:

1. The `job-search` skill accepts `--geo-id`. A `geoId` can be read from the
   `geoId=` parameter of any LinkedIn jobs search URL for that region. A city id
   is not the same thing, and usually returns fewer results.
2. Because `geoId` is fixed per run, search terms should not encode location.

Do not put a city name in the terms file and hope it filters.

## Rules

- **Never write a file you were not asked to write.** No extra config files.
- **Never write inside this skill's directory.**
- **Never present more than twelve terms** as ready to use. If the user wants more,
  say that only the first twelve are used unless they raise `--max-terms`.
