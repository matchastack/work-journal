from pathlib import Path

import pytest

from app.importers.master_resume import ImportResult, MasterResumeError, import_master_resume
from app.schema.profile import Bullet, Profile, Project, Role

FIXTURE = Path(__file__).parent / "fixtures" / "master-resume.tex"


@pytest.fixture(scope="module")
def result() -> ImportResult:
    return import_master_resume(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def profile(result: ImportResult) -> Profile:
    return result.profile


def role(profile: Profile, role_id: str) -> Role:
    return next(role for role in profile.work if role.id == role_id)


def project(profile: Profile, project_id: str) -> Project:
    return next(project for project in profile.projects if project.id == project_id)


def bullet(profile: Profile, bullet_id: str) -> Bullet:
    return next(bullet for bullet in profile.all_bullets() if bullet.id == bullet_id)


def line_of(text: str) -> int:
    """The fixture's line number for a line that contains `text`."""
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return next(number for number, line in enumerate(lines, 1) if text in line)


def test_heading(profile: Profile) -> None:
    basics = profile.basics
    assert (basics.name, basics.email, basics.phone, basics.url) == (
        "Casey Morgan",
        "casey@example.com",
        "+1 555 0100",
        "https://example.com",
    )
    assert [(p.network, p.username) for p in basics.profiles] == [
        ("LinkedIn", "casey-example"),
        ("GitHub", "casey-example"),
    ]
    assert basics.phone_visibility == "resume_only"


def test_role_types_come_from_labels_that_share_a_part(profile: Profile) -> None:
    assert [(rt.id, rt.name) for rt in profile.role_types] == [
        ("backend", "Backend / Platform"),
        ("data", "Data / ML"),
    ]
    assert [(summary.role_type, summary.text[:20]) for summary in profile.summaries] == [
        ("backend", "Backend engineer run"),
        ("data", "Engineer who builds "),
    ]
    assert "- from raw sensor logs" in profile.summaries[1].text


def test_education(profile: Profile) -> None:
    [entry] = profile.education
    assert (entry.id, entry.study_type, entry.area, entry.honours) == (
        "springfield_state_university",
        "Bachelor of Science",
        "Computer Science",
        "Magna Cum Laude",
    )
    assert (entry.start_date, entry.end_date, entry.location) == ("2020-08", "2024-05", None)
    assert entry.courses[-1] == "Statistics & Probability"
    assert [(s.role_type, len(s.courses)) for s in entry.coursework_subsets] == [
        ("backend", 3),
        ("data", 5),
    ]
    assert entry.rules == (
        "never print the GPA.",
        "Complete coursework list. Print three or four at most.",
    )
    [minor] = entry.highlights
    assert (minor.id, minor.text) == ("springfield_state_university_1", "Minor in Mathematics")
    assert minor.notes[0].text == "NOTE ON ORDER: the minor always comes after the major."


def test_role_metadata(profile: Profile) -> None:
    northwind = role(profile, "northwind_traders")
    assert (northwind.name, northwind.position) == ("Northwind Traders", "Engineer")
    assert northwind.title_variants == (
        "Software Engineer",
        "Backend Engineer",
        "Platform Engineer",
    )
    assert (northwind.start_date, northwind.end_date) == ("2024-08", None)
    assert northwind.load_bearing
    assert northwind.sensitivity == "sensitive"
    assert not northwind.during_education
    assert northwind.honesty_boundaries == (
        "did not own the on-call rotation - never claim incident command.",
    )
    contoso = role(profile, "contoso_research")
    assert (contoso.position, contoso.location) == ("Research Intern", "Springfield, IL")
    assert contoso.during_education
    assert (contoso.keep_for, contoso.cut_for) == (("data",), ("backend",))
    assert [note.text for note in contoso.notes] == [
        "Part-time while studying.",
        "KEEP for: ML/data, research, sensor-analytics roles.",
        "CUT for: pure platform, mobile roles.",
    ]


def test_bullet_metadata(profile: Profile) -> None:
    orders = bullet(profile, "nw_orders")
    assert (orders.strength, orders.verification, orders.verification_note) == (
        "high",
        "partial",
        "volume unknown",
    )
    assert [(swap.text, swap.contexts) for swap in orders.swaps] == [
        ("event-driven Python services", ("platform roles",)),
        (
            "services that consume order events from a queue and write them to PostgreSQL",
            ('data roles - matches "streaming"',),
        ),
    ]
    assert orders.needs == ("q_nw_volume",)
    assert orders.sources == ("fact_nw_orders",)

    export = bullet(profile, "nw_export")
    assert export.strength == "low"
    assert [(o.role_type, o.strength) for o in export.strength_overrides] == [("data", "high")]
    assert export.swaps[0].text == "Cut the nightly export job from 50 to 12 minutes."
    assert export.notes[0].kind == "warning"

    alerts = bullet(profile, "nw_alerts")
    assert (alerts.verification, alerts.verification_note) == ("partial", "dates yes, scope no")
    assert [(note.kind, note.text) for note in alerts.notes] == [
        ("upgrade", "add the alert volume once it is known.")
    ]

    docs = bullet(profile, "nw_docs")
    assert docs.needs == ("q_nw_docs",)
    assert len(docs.notes) == 2

    sensors = bullet(profile, "ct_sensors")
    assert sensors.swaps[0].text.endswith("of manual review a week for two research teams.")
    model = bullet(profile, "ct_model")
    assert (model.strength, model.strength_overrides) == ("low", ())
    assert (model.verification, model.verification_note) == ("yes", "metric = recall")
    assert model.notes[0].text == "STRENGTH: low (HIGH if a posting asks for testing)"
    assert model.swaps[0].text == "Rewrote the evaluation script"
    assert model.swaps[0].contexts[0].startswith('lead with "Rewrote the evaluation script"')


def test_benched_bullets(profile: Profile) -> None:
    northwind = role(profile, "northwind_traders")
    benched = [b for b in northwind.highlights if b.status == "benched"]
    assert [(b.id, b.text, b.status_reason) for b in benched] == [
        (
            "nw_tracker",
            "Used an issue tracker for sprint planning.",
            "it names a tool, not a result.",
        ),
        (
            "nw_meetings",
            "Attended design reviews with the platform team.",
            "attendance is not an outcome.",
        ),
    ]


def test_projects(profile: Profile) -> None:
    recipe = project(profile, "recipe_box")
    assert recipe.keywords == ("TypeScript", "React", "PostgreSQL")
    assert (recipe.start_date, recipe.end_date, recipe.keep_for) == (
        "2023-06",
        "2023-09",
        ("backend",),
    )
    lint = project(profile, "lint_rules")
    assert lint.keywords == ("Python", "pytest")
    assert (lint.start_date, lint.keep_for, lint.cut_for) == (None, ("backend",), ())
    assert [(note.kind, note.text) for note in lint.notes] == [
        ("note", "INCLUDE for: backend roles."),
        ("note", "OMIT for: mobile, design roles."),
        (
            "warning",
            "*** CHECK BEFORE INCLUDING *** The rule set is small; only use it where code "
            "quality is asked for.",
        ),
        ("warning", "TODO: add the dates"),
    ]


def test_planned_project(profile: Profile) -> None:
    planned = project(profile, "bird_call_classifier")
    assert (planned.status, planned.keywords) == ("planned", ("Python", "PyTorch"))
    assert planned.status_reason == "NOT YET BUILT - keep it out of every resume for now."
    assert [(b.status, b.text) for b in planned.highlights] == [
        (
            "planned",
            "Phase 1: classify bird species from short audio clips, tested against a baseline.",
        ),
        ("planned", "Phase 2: serve the model behind an API."),
    ]
    assert [note.text for note in planned.notes] == ["WHY: closes the audio-ML gap."]


def test_skills(profile: Profile) -> None:
    skills = {skill.name: skill for skill in profile.skills}
    assert list(skills) == [
        "Python", "SQL", "TypeScript", "Kotlin", "FastAPI", "Node.js", "React", "PostgreSQL",
        "Apache Airflow",
    ]  # fmt: skip
    assert skills["FastAPI"].category == "Web & APIs"
    assert [skills[name].tier for name in ("Python", "TypeScript", "Kotlin")] == [
        "strong",
        "working",
        "academic",
    ]
    assert skills["Kotlin"].verify_before_shipping
    assert skills["Kotlin"].notes[0].kind == "warning"
    assert [note.text for note in skills["React"].notes] == ["React: side projects only."]
    assert profile.skill_gaps == (
        "Swift",
        "SwiftUI",
        "Flutter",
        "Rust",
        "Haskell",
        "mobile app work",
    )
    assert [(p.role_type, [line.label for line in p.lines]) for p in profile.skill_presets] == [
        ("backend", ["Languages", "Web & APIs"]),
        ("data", ["Languages", "Data"]),
    ]
    assert profile.skill_presets[0].lines[1].skills == ("FastAPI", "Node.js")


def test_open_questions(profile: Profile) -> None:
    assert [(q.id, q.question) for q in profile.open_questions] == [
        ("q_nw_volume", "how many order events a day do the services handle?"),
        ("q_project_dates", "Project Dates: when was each project built?"),
        ("q_recipe_box", "how many monthly users are there now, not at launch?"),
        ("q_nw_docs", "What detail would strengthen nw_docs?"),
    ]


def test_every_active_bullet_has_a_fact_with_its_metrics(result: ImportResult) -> None:
    active = [b for b in result.profile.all_bullets() if b.status == "active"]
    facts = {fact.id: fact for fact in result.facts}
    assert sorted(facts) == sorted(f"fact_{b.id}" for b in active)
    assert all(fact.origin == "import" and fact.entry_id is None for fact in facts.values())
    export = facts["fact_nw_export"]
    assert (export.role_id, export.statement) == (
        "northwind_traders",
        "Cut the nightly export job from 50 to 12 minutes by batching database writes.",
    )
    [metric] = export.metrics
    assert (metric.value.model_dump(), metric.unit) == (
        {"kind": "change", "before": 50.0, "after": 12.0},
        "minute",
    )
    assert facts["fact_lr_rules"].project_id == "lint_rules"
    assert facts["fact_springfield_state_university_1"].kind == "learning"
    assert sum(len(fact.metrics) for fact in facts.values()) == 7


def test_the_report_lists_what_to_check_and_what_was_left_out(result: ImportResult) -> None:
    report = result.report
    assert report.of("problem") == []
    assert report.summary.startswith("Imported 2 roles, 1 education entry and 3 projects")
    checks = [item.message for item in report.of("check")]
    assert '"ML / data" is role type data (Data / ML)' in checks
    assert any(message.startswith("swap by drop:") for message in checks)
    generated = [item.message for item in report.of("generated")]
    assert "northwind_traders for the role Software Engineer, Northwind Traders" in generated
    skipped = {item.line: item.message for item in report.of("skipped")}
    assert skipped[line_of("This layout puts the dates")].startswith(
        "comment on Springfield State University"
    )
    assert skipped[line_of("Keep this section last.")] == (
        'comment before the Technical Skills section: "Keep this section last."'
    )
    rendered = report.render()
    assert "Check these mappings:" in rendered
    assert f"\n  line {line_of('Phone only on resumes')}: comment before the heading:" in rendered


def body(content: str) -> str:
    return "\\begin{document}\n" + content + "\n\\end{document}\n"


HEADING = "\\begin{center}\\textbf{Casey Morgan}\\end{center}\n"


def test_problems_are_reported_not_raised() -> None:
    source = body(
        HEADING
        + "\\section{Work Experience}\n\\resumeSubHeadingListStart\n"
        + "\\resumeSubheadingOneLine{Engineer, Northwind}{2023}\n"
        + "\\resumeSubheadingOneLine{Engineer, Contoso}{Jan 2023 -- Present}\n"
        + "\\resumeItemListStart\n"
        + "% ID: dup | STRENGTH: huge\n\\resumeItem{First \\foo{thing}.}\n"
        + "% ID: dup\n\\resumeItem{Second.}\n"
        + "\\resumeItemListEnd\n\\resumeSubHeadingListEnd\n"
        + "stray words\n"
    )
    result = import_master_resume(source)
    problems = [(item.line, item.message) for item in result.report.of("problem")]
    assert problems[0][1].startswith('"Engineer, Northwind" needs a title')
    assert any("isn't high, medium or low" in message for _, message in problems)
    assert any("the ID dup is used twice" in message for _, message in problems)
    assert [b.id for b in result.profile.all_bullets()] == ["dup", "contoso_2"]
    skipped = [item.message for item in result.report.of("skipped")]
    assert 'text outside the template\'s commands: "stray words"' in skipped
    assert "LaTeX commands dropped from the text: \\foo" in skipped


def test_files_that_cannot_be_imported_raise() -> None:
    with pytest.raises(MasterResumeError, match="begin\\{document\\}"):
        import_master_resume("\\documentclass{article}")
    with pytest.raises(MasterResumeError, match="no name"):
        import_master_resume(body("\\section{Education}"))
    with pytest.raises(MasterResumeError, match="line 3"):
        import_master_resume(body(HEADING + "\\resumeItem{never closed"))
