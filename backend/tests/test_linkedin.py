import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage

from app.config import Settings
from app.linkedin import (
    LINKEDIN_VARIANT,
    DoneSection,
    LinkedInDone,
    LinkedInPack,
    WrittenSection,
    mark_done,
    plan_sections,
    since_done,
    write_pack,
)
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, reply
from app.llm.usage import MemoryCallLog
from app.schema.jobs import Application, JobPosting
from app.schema.profile import Profile
from app.selection import select
from app.validate.claims import ClaimReply, JudgedClaim

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
TEXTS = {
    "headline": "Software Engineer | Python services and data tooling",
    "about": "I build Python services at Northwind Traders.\n\nBefore, I built research tools.",
    "position:northwind": "I build Python services that process order events.",
    "position:contoso": "I built a Python tool that labelled sensor logs.",
}
KEYS = list(TEXTS)
SUPPORTED = ClaimReply(
    claims=(JudgedClaim(claim="Python", reason="The facts say so.", verdict="supported"),)
)
ADDED_KAFKA = ClaimReply(
    claims=(
        JudgedClaim(
            claim="with Kafka",
            reason="No fact names Kafka.",
            verdict="unsupported",
            issue="added_tool",
            term="Kafka",
        ),
    )
)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def written(text: str) -> BetaMessage:
    return reply(WrittenSection(text=text))


def passing(*keys: str) -> list[BetaMessage]:
    """For each section, the writer's reply and a claim check that passes it."""
    return [message for key in keys for message in (written(TEXTS[key]), reply(SUPPORTED))]


def write(
    *replies: BetaMessage, profile: Profile = PROFILE, **options: Any
) -> tuple[LinkedInPack, FakeMessages]:
    fake = FakeMessages(*replies)
    client = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    return write_pack(profile, client, **options), fake


def request(fake: FakeMessages, number: int) -> str:
    return fake.requests[number]["messages"][0]["content"]


def sent_facts(fake: FakeMessages, number: int) -> list[dict[str, Any]]:
    message = request(fake, number)
    return json.loads(message.split("<facts>\n")[1].split("\n</facts>")[0])


def done_from(pack: LinkedInPack) -> LinkedInDone:
    return mark_done(pack, None, now=NOW)[0]


def with_bullet_text(profile: Profile, bullet_id: str, text: str) -> Profile:
    data = profile.model_dump(mode="json")
    for item in (*data["work"], *data["education"], *data["projects"]):
        for bullet in item["highlights"]:
            if bullet["id"] == bullet_id:
                bullet["text"] = text
    return Profile.model_validate(data)


def test_the_first_pack_writes_and_verifies_every_section() -> None:
    pack, fake = write(*passing(*KEYS))
    assert [section.key for section in pack.sections] == KEYS
    assert [section.text for section in pack.sections] == list(TEXTS.values())
    assert all(section.changed for section in pack.sections)
    assert all(section.report and section.report.passed for section in pack.sections)
    assert [r["model"] for r in fake.requests] == ["standard-model"] * 8
    system = fake.requests[0]["system"]
    assert system[0]["text"].startswith("You write one section of someone's LinkedIn profile")
    assert request(fake, 0).startswith("Write the headline section, in at most 220 characters.")
    assert request(fake, 2).startswith("Write the about section, in at most 2,600 characters.")
    assert request(fake, 4).startswith(
        "Write the position section, in at most 2,000 characters.\n\nThe role: Software "
        "Engineer at Northwind Traders, a current role, since August 2025. The role is sensitive."
    )
    assert "a past role, from January 2024 to June 2024." in request(fake, 6)


def test_positions_use_the_first_approved_title() -> None:
    pack, _ = write(*passing(*KEYS))
    northwind = pack.sections[2]
    assert (northwind.title, northwind.label) == (
        "Software Engineer",
        "Position: Software Engineer at Northwind Traders",
    )
    assert pack.sections[3].title == "Research Intern", "no variants: the official title"


def test_sections_carry_their_length_and_limit() -> None:
    pack, _ = write(*passing(*KEYS))
    headline = pack.sections[0]
    assert (headline.length, headline.limit) == (len(TEXTS["headline"]), 220)
    assert pack.sections[1].limit == 2600
    assert pack.sections[2].limit == 2000


def test_each_section_is_checked_against_its_own_facts() -> None:
    _, fake = write(*passing(*KEYS))
    position = [fact["id"] for fact in sent_facts(fake, 4)]
    assert position == ["role-northwind", "nw_orders", "nw_export"]
    headline = [fact["id"] for fact in sent_facts(fake, 0)]
    assert headline == ["label", "role-northwind", "nw_orders", "nw_export", "skills"]
    about = [fact["id"] for fact in sent_facts(fake, 2)]
    assert {"role-contoso", "ct_datasets", "project-recipe_box", "rb_app"} <= set(about)
    assert {"education-springfield_state", "ss_minor"} <= set(about)
    assert "nw_tracker" not in about, "benched bullets never reach the pack"


def test_bullets_stand_in_as_facts_with_the_numbers_in_their_text() -> None:
    _, fake = write(*passing(*KEYS))
    export = next(fact for fact in sent_facts(fake, 4) if fact["id"] == "nw_export")
    assert export["statement"].startswith("Cut the nightly export job from 50 to 12 minutes")
    assert export["metrics"][0]["value"] == {"kind": "change", "before": 50.0, "after": 12.0}


def test_the_skills_fact_leaves_out_skills_to_confirm_first() -> None:
    _, fake = write(*passing(*KEYS))
    skills = next(fact for fact in sent_facts(fake, 0) if fact["id"] == "skills")
    assert "Kotlin" not in skills["statement"], "verify before shipping (R3)"
    assert skills["tools"][:3] == ["Python", "TypeScript", "SQL"]


def test_content_kept_off_the_web_never_reaches_the_writer() -> None:
    data = PROFILE.model_dump(mode="json")
    data["work"][0]["highlights"][0]["visibility"] = "resume_only"
    profile = Profile.model_validate(data)
    private = data["work"][0]["highlights"][0]["text"]
    _, fake = write(*passing(*KEYS), profile=profile)
    for sent in fake.requests:
        assert private not in json.dumps(sent)
    assert "casey@example.com" not in json.dumps(fake.requests)
    assert "+1 555 0100" not in json.dumps(fake.requests)


def test_a_role_without_public_bullets_gets_no_position() -> None:
    data = PROFILE.model_dump(mode="json")
    for bullet in data["work"][1]["highlights"]:
        bullet["visibility"] = "private"
    profile = Profile.model_validate(data)
    plans = plan_sections(profile, select(profile, LINKEDIN_VARIANT))
    assert [plan.key for plan in plans] == ["headline", "about", "position:northwind"]


def test_sections_unchanged_since_the_last_done_pack_are_carried_over_without_a_call() -> None:
    first, _ = write(*passing(*KEYS))
    again, fake = write(done=done_from(first))
    assert fake.requests == []
    assert [section.text for section in again.sections] == list(TEXTS.values())
    assert not again.changed
    assert all(section.report is None for section in again.sections)


def test_a_changed_bullet_rewrites_only_the_sections_that_draw_on_it() -> None:
    first, _ = write(*passing(*KEYS))
    profile = with_bullet_text(PROFILE, "ct_datasets", "Surveyed 40 open datasets for the study.")
    texts = {"about": "I build Python services.", "position:contoso": "I surveyed datasets."}
    replies = [m for t in texts.values() for m in (written(t), reply(SUPPORTED))]
    pack, fake = write(*replies, profile=profile, done=done_from(first))
    assert len(fake.requests) == 4
    assert [section.key for section in pack.changed] == ["about", "position:contoso"]


def test_a_section_written_again_with_the_same_text_is_not_listed() -> None:
    first, _ = write(*passing(*KEYS))
    profile = with_bullet_text(PROFILE, "ct_datasets", "Surveyed 40 open datasets for the study.")
    pack, _ = write(*passing("about", "position:contoso"), profile=profile, done=done_from(first))
    assert not pack.changed
    assert pack.sections[3].report is not None


def test_everything_writes_every_section_again() -> None:
    first, _ = write(*passing(*KEYS))
    pack, fake = write(*passing(*KEYS), done=done_from(first), everything=True)
    assert len(fake.requests) == 8
    assert all(section.report is not None for section in pack.sections)
    assert not pack.changed


def test_style_notes_reach_the_writer_and_rewrite_sections_when_they_change() -> None:
    first, _ = write(*passing(*KEYS))
    pack, fake = write(
        *passing(*KEYS),
        done=done_from(first),
        avoid=("spearheaded",),
        preferences=("Keep it short",),
    )
    assert len(fake.requests) == 8
    assert "<avoid>\n- spearheaded\n</avoid>" in request(fake, 0)
    assert "<preferences>\n- Keep it short\n</preferences>" in request(fake, 0)
    assert pack.sections[0].source != first.sections[0].source


def test_a_word_to_avoid_fails_the_style_check() -> None:
    spearheaded = "I spearheaded Python services at Northwind Traders."
    replies = [written(TEXTS["headline"]), reply(SUPPORTED)]
    replies += [written(spearheaded), reply(SUPPORTED), written(TEXTS["about"]), reply(SUPPORTED)]
    replies += passing("position:northwind", "position:contoso")
    pack, fake = write(*replies, avoid=("spearhead",))
    about = pack.sections[1]
    assert (about.text, about.report and about.report.attempts) == (TEXTS["about"], 2)
    assert "<attempt>\nI spearheaded" in request(fake, 4)


def test_a_headline_over_the_limit_is_written_again() -> None:
    long = "Software Engineer | " + "Python services | " * 13
    replies = [written(long), reply(SUPPORTED), *passing(*KEYS)]
    pack, fake = write(*replies)
    assert pack.sections[0].text == TEXTS["headline"]
    assert "LinkedIn allows 220 in the headline" in request(fake, 2)


def test_a_section_that_fails_twice_is_flagged() -> None:
    kafka = "I build Python services with Kafka."
    replies = [*passing("headline", "about")]
    replies += [written(kafka), reply(ADDED_KAFKA), written(kafka), reply(ADDED_KAFKA)]
    replies += passing("position:contoso")
    pack, _ = write(*replies)
    assert [section.flagged for section in pack.sections] == [False, False, True, False]


def test_marking_done_leaves_out_flagged_sections() -> None:
    kafka = "I build Python services with Kafka."
    replies = [*passing("headline", "about")]
    replies += [written(kafka), reply(ADDED_KAFKA), written(kafka), reply(ADDED_KAFKA)]
    replies += passing("position:contoso")
    pack, _ = write(*replies)
    earlier = DoneSection(
        key="position:northwind", label="Position", text="Earlier text.", source="earlier"
    )
    done, flagged = mark_done(pack, LinkedInDone(sections=(earlier,)), now=NOW)
    assert [section.key for section in flagged] == ["position:northwind"]
    assert [section.key for section in done.sections] == KEYS
    assert done.sections[2] == earlier, "LinkedIn still has the text marked done before"
    assert done.marked_at == NOW
    alone, _ = mark_done(pack, None, now=NOW)
    assert [section.key for section in alone.sections] == ["headline", "about", "position:contoso"]


def test_sections_the_profile_no_longer_has_are_listed_as_removed() -> None:
    first, _ = write(*passing(*KEYS))
    label = "Position: Tutor at A School"
    gone = DoneSection(key="position:old", label=label, text="Tutored.", source="old")
    done = done_from(first)
    done = done.model_copy(update={"sections": (*done.sections, gone)})
    pack, _ = write(done=done)
    assert pack.removed == (label,)


def test_a_pack_marked_done_since_it_was_written_lists_only_its_flagged_sections() -> None:
    """The LinkedIn page shows the pack last written against the newest snapshot (FR-LIN-2)."""
    kafka = "I build Python services with Kafka."
    replies = [*passing("headline", "about")]
    replies += [written(kafka), reply(ADDED_KAFKA), written(kafka), reply(ADDED_KAFKA)]
    replies += passing("position:contoso")
    pack, _ = write(*replies)
    assert since_done(pack, None) == pack
    done, _ = mark_done(pack, None, now=NOW)
    after = since_done(pack, done)
    assert [section.key for section in after.changed] == ["position:northwind"]
    assert after.removed == ()
    assert [section.text for section in after.sections] == [s.text for s in pack.sections]


def test_a_pack_against_a_newer_snapshot_lists_what_differs_and_what_it_no_longer_has() -> None:
    pack, _ = write(*passing(*KEYS))
    label = "Position: Tutor at A School"
    gone = DoneSection(key="position:old", label=label, text="Tutored.", source="old")
    about = DoneSection(key="about", label="About", text="Earlier About.", source="old")
    done = done_from(pack)
    sections = tuple(about if section.key == "about" else section for section in done.sections)
    after = since_done(pack, done.model_copy(update={"sections": (*sections, gone)}))
    assert [section.key for section in after.changed] == ["about"]
    assert after.removed == (label,)
    assert after.profile_version == pack.profile_version


def application(title: str) -> Application:
    return Application(
        id="app_1",
        applied_on=date(2026, 9, 1),
        company="Example Corp",
        position="Backend Engineer",
        posting=JobPosting(title="Backend Engineer"),
        posting_text="A posting.",
        title_variant=title,
    )


def test_a_current_title_that_differs_from_the_latest_application_is_flagged() -> None:
    pack, _ = write(*passing(*KEYS), latest_application=application("Backend Engineer"))
    northwind = pack.sections[2]
    assert northwind.notes == (
        'LinkedIn shows "Software Engineer" for Northwind Traders, but your latest application '
        '(Example Corp, 2026-09-01) used "Backend Engineer". Make them match, or check that both '
        "are right.",
    )
    assert pack.sections[3].notes == (), "only current roles are compared"


def test_a_matching_title_needs_no_note() -> None:
    pack, _ = write(*passing(*KEYS), latest_application=application("software engineer"))
    assert all(not section.notes for section in pack.sections)


def test_the_pack_round_trips_through_json() -> None:
    pack, _ = write(*passing(*KEYS), profile_version=3)
    assert LinkedInPack.model_validate_json(pack.model_dump_json()) == pack
