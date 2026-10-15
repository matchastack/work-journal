from pathlib import Path

from app.schema.jobs import JobPosting
from app.schema.profile import Profile
from app.schema.variant import Variant
from app.selection import Selection, select
from app.tailoring.coverage import CoverageReport, TermCoverage, coverage, report_lines

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
POSTING = JobPosting(
    title="Backend Engineer",
    must_have=("Python", "Go", "PostgreSQL"),
    nice_to_have=("python", "message queue", "Kafka"),
    key_terms=("full-text   search", "Distributed Systems", "C++"),
)


def backend_resume() -> Selection:
    return select(PROFILE, Variant(id="tailored", name="Backend", role_type="backend"))


def test_each_term_is_found_where_the_resume_shows_it() -> None:
    report = coverage(backend_resume(), POSTING)
    assert report.covered == (
        TermCoverage(
            term="Python",
            kind="must_have",
            locations=("work[northwind].highlights[nw_orders]", "skills[Languages]"),
        ),
        TermCoverage(
            term="PostgreSQL",
            kind="must_have",
            locations=("projects[recipe_box].keywords", "skills[Databases]"),
        ),
        TermCoverage(
            term="message queue",
            kind="nice_to_have",
            locations=("work[northwind].highlights[nw_orders]",),
        ),
        TermCoverage(
            term="full-text   search",
            kind="key_term",
            locations=("projects[recipe_box].highlights[rb_app]",),
        ),
        TermCoverage(
            term="Distributed Systems",
            kind="key_term",
            locations=("education[springfield_state].courses",),
        ),
    )
    assert [(entry.term, entry.kind) for entry in report.missing] == [
        ("Go", "must_have"),
        ("Kafka", "nice_to_have"),
        ("C++", "key_term"),
    ]


def test_only_what_the_resume_prints_counts() -> None:
    resume = backend_resume()
    fitted = resume.model_copy(update={"projects": ()})
    report = coverage(fitted, POSTING)
    postgres = next(entry for entry in report.covered if entry.term == "PostgreSQL")
    assert postgres.locations == ("skills[Databases]",), "the cut project doesn't count"
    assert "full-text   search" in [entry.term for entry in report.missing]


def test_a_term_must_be_whole_words() -> None:
    posting = JobPosting(title="Engineer", must_have=("SQL", "Java"), key_terms=("event",))
    report = coverage(backend_resume(), posting)
    sql = next(entry for entry in report.covered if entry.term == "SQL")
    assert sql.locations == ("skills[Languages]",), "PostgreSQL isn't SQL"
    assert [entry.term for entry in report.missing] == ["Java", "event"]


def test_the_report_prints_covered_terms_and_gaps() -> None:
    lines = report_lines(coverage(backend_resume(), POSTING))
    assert lines[0] == "Covers 5 of the posting's 8 skills and key terms:"
    assert lines[1] == (
        "   Python (must-have): work[northwind].highlights[nw_orders], skills[Languages]"
    )
    assert lines[-4:] == [
        "Gaps, which are never added:",
        "   must-have: Go",
        "   nice-to-have: Kafka",
        "   key term: C++",
    ]
    assert report_lines(CoverageReport()) == ["The posting names no skills or key terms to cover."]
