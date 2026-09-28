import pytest

from app.validate.style import (
    LINKEDIN_LIMITS,
    TEMPLATE_LAYOUT,
    BulletLayout,
    LinkedInSection,
    StyleCheck,
    check_linkedin,
    check_resume_bullet,
    estimate_lines,
)


def codes(check: StyleCheck) -> list[tuple[str, str]]:
    return [(finding.severity, finding.code) for finding in check.findings]


# Resume bullets: opening verb and tense


@pytest.mark.parametrize(
    ("text", "current"),
    [
        ("Built a caching layer in Go that cut p95 latency from 180 ms to 60 ms.", False),
        ("Build internal tools in Python that save the support team 5 hours a week.", True),
        ("Leads code reviews for a team of 6 engineers.", True),
    ],
)
def test_a_good_bullet_has_no_findings(text: str, current: bool) -> None:
    check = check_resume_bullet(text, current=current)
    assert check.findings == ()
    assert check.ok


@pytest.mark.parametrize(
    ("text", "current", "message"),
    [
        ("Built the billing service.", True, "the current role use the present tense"),
        ("Build the billing service.", False, "past roles use the past tense"),
        ("Leads the platform team.", False, "past roles use the past tense"),
    ],
)
def test_the_tense_must_match_the_role(text: str, current: bool, message: str) -> None:
    check = check_resume_bullet(text, current=current)
    assert codes(check) == [("error", "wrong_tense")]
    assert message in check.findings[0].message
    assert not check.ok


@pytest.mark.parametrize("current", [True, False])
def test_verbs_spelled_the_same_in_both_tenses_pass(current: bool) -> None:
    check = check_resume_bullet("Cut build times by 40% by caching dependencies.", current=current)
    assert check.findings == ()


def test_an_ing_opening_is_a_warning() -> None:
    check = check_resume_bullet("Building a feature store for the ranking team.", current=True)
    assert codes(check) == [("warning", "wrong_tense")]
    assert check.ok


def test_an_unknown_ed_word_is_taken_as_past_tense() -> None:
    text = "Terraformed the staging environment."
    assert check_resume_bullet(text, current=False).findings == ()
    assert codes(check_resume_bullet(text, current=True)) == [("warning", "wrong_tense")]


def test_a_word_the_verb_list_doesnt_know_is_a_warning() -> None:
    check = check_resume_bullet("Kubernetes clusters moved to the new region.", current=False)
    assert codes(check) == [("warning", "not_action_verb")]
    assert '"Kubernetes" may not be an action verb' in check.findings[0].message


@pytest.mark.parametrize(
    "text",
    [
        "Successfully moved 12 services to Kubernetes.",
        "The team shipped a new onboarding flow.",
        "Responsible for the nightly data exports.",
        "As the on-call lead, cut paging noise by half.",
        "10+ services moved to Kubernetes.",
    ],
)
def test_openings_that_are_clearly_not_verbs_are_errors(text: str) -> None:
    check = check_resume_bullet(text, current=False)
    assert codes(check) == [("error", "not_action_verb")]


# Resume bullets: pronouns


def test_first_person_pronouns_are_errors() -> None:
    check = check_resume_bullet("Built our deploy pipeline so we could ship daily.", current=False)
    assert codes(check) == [("error", "first_person")]
    assert check.findings[0].message == 'Uses first-person pronouns ("our", "we").'


def test_a_pronoun_opening_breaks_two_rules() -> None:
    check = check_resume_bullet("I built the deploy pipeline.", current=False)
    assert codes(check) == [("error", "not_action_verb"), ("error", "first_person")]


def test_terms_that_contain_pronoun_letters_pass() -> None:
    text = "Tuned I/O scheduling for US customers in us-east-1 with the R&I group."
    assert check_resume_bullet(text, current=False).findings == ()


# Resume bullets: length


def test_a_two_line_bullet_fits() -> None:
    text = (
        "Rebuilt the order pipeline as event-driven services on Kafka and Postgres, cutting "
        "checkout latency from 900 ms to 250 ms and letting the team ship payment changes daily."
    )
    assert estimate_lines(text) == 2
    assert check_resume_bullet(text, current=False).findings == ()


def test_a_bullet_over_two_lines_is_too_long() -> None:
    text = (
        "Rebuilt the order pipeline as event-driven services on Kafka and Postgres, cutting "
        "checkout latency from 900 ms to 250 ms and letting the team ship payment changes daily "
        "instead of weekly, with no downtime during the migration and a rollback plan for every "
        "step."
    )
    check = check_resume_bullet(text, current=False)
    assert codes(check) == [("error", "too_long")]
    assert check.findings[0].message == (
        "Takes about 3 lines at the template's width; the limit is 2."
    )


def test_line_estimates_follow_the_characters_and_the_layout() -> None:
    narrow = "il " * 50  # 150 narrow characters fit on one line
    wide = "MW " * 50  # 150 wide ones take three
    assert estimate_lines(narrow) == 1
    assert estimate_lines(wide) == 3
    half_width = BulletLayout(line_width_pt=TEMPLATE_LAYOUT.line_width_pt / 2, font_size_pt=10)
    assert estimate_lines(narrow, half_width) == 2


def test_a_custom_line_limit() -> None:
    text = "Built the billing service in Go, with retries and a dead-letter queue."
    one_line = BulletLayout(line_width_pt=200, font_size_pt=10, max_lines=1)
    assert codes(check_resume_bullet(text, current=False, layout=one_line)) == [
        ("error", "too_long")
    ]


# Words to avoid (every style)


@pytest.mark.parametrize(
    ("text", "used"),
    [
        ("Leveraged Kafka to decouple the services.", "Leveraged"),
        ("Built tools, leveraging Kafka.", "leveraging"),
        ("Built tools that LEVERAGE Kafka.", "LEVERAGE"),
        ("Built tools to utilize spare capacity.", "utilize"),
        ("Cached results in order to cut load.", "in order to"),
        ("Cached results in  order\nto cut load.", "in  order\nto"),
    ],
)
def test_avoided_words_match_any_form_and_case(text: str, used: str) -> None:
    check = check_resume_bullet(text, current=False, avoid=["leverage", "utilize", "In order to"])
    assert codes(check) == [("error", "avoided_word")]
    assert check.findings[0].message == f'Uses "{used}", which is on your list of words to avoid.'


def test_avoided_words_match_whole_words_only() -> None:
    text = "Reused the caching layer because it was cheap to run."
    assert check_resume_bullet(text, current=False, avoid=["use", "ache"]).findings == ()


def test_each_avoided_word_is_reported() -> None:
    text = "Leveraged synergies to utilize spare capacity."
    avoid = ["leverage", "synergy", "utilize", "robust"]
    check = check_resume_bullet(text, current=False, avoid=avoid)
    assert codes(check) == [("error", "avoided_word")] * 3


# LinkedIn


@pytest.mark.parametrize("section", ["headline", "about", "position"])
def test_linkedin_sections_have_character_limits(section: LinkedInSection) -> None:
    limit = LINKEDIN_LIMITS[section]
    assert check_linkedin("x" * limit, section).findings == ()
    check = check_linkedin("x" * (limit + 1), section)
    assert codes(check) == [("error", "too_long")]
    assert f"LinkedIn allows {limit:,}" in check.findings[0].message


def test_linkedin_counts_an_emoji_as_two_characters() -> None:
    headline = "x" * 219 + "\N{ROCKET}"  # 220 characters, but 221 as LinkedIn counts them
    assert codes(check_linkedin(headline, "headline")) == [("error", "too_long")]


def test_linkedin_text_may_use_the_first_person() -> None:
    text = "I build data tools, and I love working with our analysts."
    assert check_linkedin(text, "about").findings == ()


def test_linkedin_text_avoids_the_owners_words() -> None:
    check = check_linkedin("I leverage data to help teams.", "about", avoid=["leverage"])
    assert codes(check) == [("error", "avoided_word")]
