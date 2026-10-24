"""Data contracts for the job-search skills.

Identical copies of this file live in candidate-profile/, job-search/ and
match-jobs/ so that every skill folder can be copied standalone. tests/test_drift.py
fails if the copies diverge, and tests/test_models_parity.py pins the parts that
must keep matching the pydantic-ai reference implementation.

Ported from agentic_workflow/src/agentic_workflows/models/models.py with the
`count` computed fields removed, so the JSON Schema handed to a model stays flat
and contains no read-only surprises.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExperienceItem(BaseModel):
    company: str
    company_summary: str | None = None
    title: str
    start_date: str | None = None
    end_date: str | None = None
    summary: str = ""
    skills_used: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    school: str
    department: str = ""
    qualification_achieved: str
    summary: str = ""
    skills_learned: list[str] = Field(default_factory=list)
    # CVs often omit degree dates. Leave them optional rather than inventing them.
    start_year: int | None = None
    end_year: int | None = None


class CandidateProfile(BaseModel):
    """The structured form of a CV. Extracted once, reused by every run."""

    full_name: str | None = None
    headline: str
    years_experience: float | None = None
    seniority: str | None = None
    locations: list[str] = Field(default_factory=list)
    remote_preferences: list[str] = Field(default_factory=list)
    target_titles: list[str] = Field(default_factory=list)
    hard_skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    unique_qualities: list[str] = Field(default_factory=list)
    visa_constraints: list[str] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    evidence_snippets: list[str] = Field(default_factory=list)


class JobEntry(BaseModel):
    """One row on a LinkedIn results page: no posting text."""

    title: str = Field(description="The full job title")
    url: str = Field(description="The link to the job posting")
    company: str = Field(description="The company offering the job")
    location: str = Field(description="The location of the job, if available")


class JobDescription(JobEntry):
    """A Job Entry plus the full posting text. The unit the matcher scores."""

    description: str = Field(description="A full description of the job")


class JobEntryList(BaseModel):
    """The content of a LinkedIn job search listing page."""

    jobs: list[JobEntry] = Field(
        default_factory=list,
        description=(
            "A list of job titles, companies, and urls extracted from the LinkedIn job "
            "search results page. If there is no matching job, this will be an empty list."
        ),
    )


class JobDescriptionList(BaseModel):
    """Every Job Description collected in one run. The input to matching."""

    jobs: list[JobDescription] = Field(
        default_factory=list,
        description=(
            "A list of job titles, companies, urls and descriptions collected during one run."
        ),
    )


class JobSearchTerms(BaseModel):
    """Output of the career adviser: what to search for, and where."""

    job_search_terms: list[str] = Field(
        description=(
            "A list of job titles to search for, e.g. ['Software Engineer', 'Data Scientist']"
        )
    )
    preferred_location: str = Field(
        description="The location to search for jobs in, e.g. 'Cambridge, UK'"
    )


class MatchResult(JobDescription):
    """A Job Description scored against a Candidate Profile."""

    domain: str = Field(
        description=(
            "The domain that the job role is focusing on, (for example: FinTech, MedTech, "
            "Education, HealthTech, Defence)"
        )
    )
    salary_range: str = Field(
        description=("Salary range or renumeration, if stated or mentioned in the job description")
    )
    match_score: int = Field(
        description=(
            "A score from 0 to 100 indicating how well the candidate matches the job requirements."
        )
    )
    reasoning: str = Field(
        description="An explanation of why the candidate matches the job requirements."
    )
    strengths: str = Field(
        description=(
            "Describe the strength and attributes that makes the candidate score higher in this job"
        )
    )
    gaps: str = Field(
        description="Highlight any gaps that the candidate has over this job description"
    )

    def to_markdown(self) -> str:
        """Formats the result into markdown, including parent fields and collapsible description.

        Kept byte-identical to the reference implementation; tests/test_models_parity.py
        checks that.
        """

        # Color indicator based on score
        color = "green" if self.match_score >= 80 else "orange" if self.match_score >= 50 else "red"

        return (
            f"\n## {self.title} @ {self.company}\n"
            f"**Match Score:** <span style='color:{color}'>**{self.match_score}%**</span>\n\n"
            f"### 📍 Job Details\n"
            f"- **Location:** {self.location or 'Remote/Not specified'}\n"
            f"- **Salary:** {self.salary_range or 'N/A'}\n"
            f"- **Domain:** {self.domain}\n"
            f"- **URL:** [{self.url}]({self.url})\n\n"
            f"### 🧠 Match Analysis\n"
            f"**Reasoning:** {self.reasoning}\n\n"
            f"- **✅ Key Strengths:** {self.strengths}\n"
            f"- **⚠️ Potential Gaps:** {self.gaps}\n\n"
            f"### 📑 Full description\n"
            f"<details>\n"
            f"<summary><b> View Full Job Description</b> (Click to expand)</summary>\n\n"
            f"{self.description}\n\n"
            f"</details>\n"
            f"\n---\n"
        )


class MatcherOutput(BaseModel):
    """The aggregate Match Report: every score, sorted at render time."""

    match_results: list[MatchResult] = Field(default_factory=list)
    bands: dict[str, int] = Field(
        default_factory=dict,
        description="How many scored jobs fall in each score band.",
    )

    def to_markdown(self, extra: str = "") -> str:
        """Aggregates all match results into a single Markdown report."""
        if not self.match_results:
            return "# Job Match Results\n\nNo suitable matches were found for this profile."

        sorted_matches = sorted(self.match_results, key=lambda x: x.match_score, reverse=True)

        report_header = [
            "# Job Match Report\n",
            "### Summary\n",
            f"- Total Positions Matched: {len(self.match_results)} \n",
            f"{extra} \n",
            "---\n\n",
            "",
        ]

        match_sections = [m.to_markdown() for m in sorted_matches]

        return "\n".join(report_header + match_sections)


class ChunkEnvelope(BaseModel):
    """One batch of Job Descriptions handed to the model for scoring.

    Written by `jobmatch.py prepare`, read by the model. `total` lets the model
    tell whether it is on the last chunk without counting files.
    """

    index: int = Field(description="1-based position of this chunk in the run")
    total: int = Field(description="How many chunks the run was split into")
    jobs: list[JobDescription] = Field(default_factory=list)


class MatchChunk(BaseModel):
    """The model's scored output for one chunk. Written to matches/chunk-NN.json."""

    chunk: int = Field(description="The index of the chunk these results belong to")
    match_results: list[MatchResult] = Field(default_factory=list)
