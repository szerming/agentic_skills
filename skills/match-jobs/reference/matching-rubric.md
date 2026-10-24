# Matching rubric

How to score one Job Description against one Candidate Profile. Read this before
scoring a chunk, then work from it. Do not paste it into the conversation.

This rubric was the matcher agent's system prompt in the previous implementation.
It is a document now, so it can be reviewed and versioned like anything else.

## Weighted criteria

| Criterion | Weight | What counts |
|---|---|---|
| Skills match | 40% | Required and preferred skills, compared with `hard_skills` and the skills named in `experience[].skills_used` |
| Experience relevance | 20% | Shape and depth of past work against the responsibilities in the posting |
| Industry / domain alignment | 15% | `domains` and `industries` against the posting's sector |
| Seniority alignment | 15% | `seniority` and `years_experience` against the level the posting implies |
| Tools / technology match | 10% | Named tools, platforms, standards, languages |

Weights are guidance for your judgement, not an arithmetic obligation. Do not
compute a score field by field and call it done: the score is your overall read,
and the weights are there to keep you honest about what dominates.

## Score bands

| Band | Meaning |
|---|---|
| 90 to 100 | Excellent fit, minimal gaps |
| 75 to 89 | Strong fit, minor gaps |
| 60 to 74 | Moderate fit, notable gaps |
| 40 to 59 | Weak fit |
| Below 40 | Poor fit |

The report threshold is 75 by default. That is where the interesting jobs start,
not where the analysis stops: every score is kept.

## Evidence rules

- **Cite the CV, not the vibe.** Reasoning must reference something specific from
  the profile and something specific from the posting.
- **Never credit a skill the profile does not claim.** Adjacent experience is not
  the same skill; say "adjacent" when that is what you mean.
- **Penalise hard blockers heavily.** Missing a stated must-have (a licence, a
  language, a work authorisation) is a decisive factor, not a rounding error.
- **Do not reward keyword overlap.** Ten shared technology words in a posting that
  is a different job is not a match.
- **Be conservative.** A user acts on the top of the list. Inflated scores waste
  their time in interviews.

## Field by field

- **`domain`**: the sector the role is actually in. Use the posting's product and
  customers, not its parent company's industry.
- **`salary_range`**: quote what the posting states, in its own units. If it says
  nothing, write `"Not stated"`. Never estimate.
- **`reasoning`**: two to four sentences. What the role needs, what the candidate
  has, and the single biggest reason the score is not higher.
- **`strengths`**: the two or three attributes that earned the score.
- **`gaps`**: the two or three things that cost it points. If the description was
  empty or truncated, say so here and score low.

## Chunk discipline

- Score **only** the jobs in the chunk you were given.
- Emit **exactly one** result per job, keeping `title`, `url`, `company`,
  `location` and `description` byte-for-byte. `description` is not for editing;
  the report embeds it.
- Compare jobs within the chunk only for consistency of scoring, never to
  rebalance. If job A scores 80 and job B 40, both numbers stand on their own.
- Change one thing between chunks at most. Ten chunks scored by ten conflicting
  rubrics produce a liar's ranking.

## Worked example

A candidate is a principal software architect with strong Python, regulated
medical-device delivery, and team leadership, but no MRI or radiotherapy
experience.

Posting: "Senior Software Architect, MRI Reconstruction. Required: C++, real-time
signal processing, medical imaging. Desirable: IEC 62304, team leadership."

A defensible result: **62**, moderate fit.

- Skills: architecture and IEC 62304 are real matches, but C++ and real-time
  signal processing are both required and absent.
- Experience: regulated delivery transfers well; imaging pipelines do not.
- Domain: MedTech matches.
- Seniority: matches.
- Tools: Python is not C++.

`gaps`: "No C++ in the profile. No real-time signal processing or imaging
pipeline experience, both listed as required."

A model that scored this 85 because of "medical device" and "architect" would be
exactly the failure this rubric exists to prevent.
