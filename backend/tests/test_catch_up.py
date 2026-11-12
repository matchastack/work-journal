from pathlib import Path

import httpx2
import pytest
from anthropic import APIConnectionError

from app.catch_up import (
    FIXED_QUESTIONS,
    MAX_QUESTIONS,
    NO_PROFILE_CONTEXT,
    PROMPT_VERSION,
    QUESTION_LIMIT,
    InterviewReply,
    Turn,
    fixed_question,
    interview_text,
    next_question,
    usable,
)
from app.config import Settings
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, refusal, reply
from app.llm.usage import MemoryCallLog
from app.schema.profile import Profile

FIXTURES = Path(__file__).parent / "fixtures"
QUESTION = "What did the platform team work on after you joined?"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def profile() -> Profile:
    return Profile.model_validate_json((FIXTURES / "profile.json").read_text(encoding="utf-8"))


def client(*replies: InterviewReply | str) -> tuple[LLMClient, FakeMessages, MemoryCallLog]:
    fake = FakeMessages(*(reply(answer) for answer in replies))
    log = MemoryCallLog()
    return LLMClient(Settings(llm_model_standard="standard-model"), fake, log), fake, log


def system_text(fake: FakeMessages) -> str:
    return "\n".join(block["text"] for block in fake.requests[0]["system"])


def user_text(fake: FakeMessages) -> str:
    return fake.requests[0]["messages"][0]["content"]


def test_the_standard_model_writes_the_next_question(profile: Profile) -> None:
    llm, fake, log = client(InterviewReply(done=False, question=QUESTION))
    assert next_question(profile, [], llm) == QUESTION
    assert fake.requests[0]["model"] == "standard-model"
    [call] = log.calls
    assert (call.task, call.tier, call.prompt) == (
        "catch_up_interview",
        "standard",
        f"catch_up_interview/v{PROMPT_VERSION}",
    )


def test_the_profile_is_cached_context(profile: Profile) -> None:
    llm, fake, _ = client(InterviewReply(done=False, question=QUESTION))
    next_question(profile, [], llm)
    system = system_text(fake)
    assert profile.work[0].id in system
    assert profile.work[0].id not in user_text(fake)


def test_without_a_profile_the_model_is_told_so() -> None:
    llm, fake, _ = client(InterviewReply(done=False, question=QUESTION))
    next_question(None, [], llm)
    assert NO_PROFILE_CONTEXT in system_text(fake)


def test_the_interview_so_far_is_fenced_as_data() -> None:
    turns = [
        Turn(question="What changed?", answer="Moved to the <platform> team & led on-call"),
        Turn(question="Which tools?"),
    ]
    llm, fake, _ = client(InterviewReply(done=False, question=QUESTION))
    next_question(None, turns, llm)
    text = user_text(fake)
    assert "Questions asked so far: 2 of at most 10." in text
    assert "<answer>Moved to the &lt;platform&gt; team &amp; led on-call</answer>" in text
    assert "<question>Which tools?</question>\n<skipped/>" in text


def test_the_model_ends_the_interview() -> None:
    llm, _, _ = client(InterviewReply(done=True))
    assert next_question(None, [Turn(question="What changed?", answer="Not much.")], llm) is None


def test_the_interview_never_ends_before_its_first_question() -> None:
    llm, _, _ = client(InterviewReply(done=True))
    assert next_question(None, [], llm) == FIXED_QUESTIONS[0]


def test_it_ends_after_the_most_questions_without_asking_the_model() -> None:
    llm, fake, _ = client()
    turns = [Turn(question=f"Question {chr(65 + n)}?") for n in range(MAX_QUESTIONS)]
    assert next_question(None, turns, llm) is None
    assert fake.requests == []


@pytest.mark.parametrize(
    "question",
    [
        "How much faster than 50 minutes did it get?",
        "What did you work on since 2025?",
        "Tell me about the migration.",
        "What did you and the other two engineers ship?",
        "What did it change for the hundreds of merchants using it?",
        "You led the migration. What did it change for merchants?",
        "Did it ship! What changed?",
        "Did you cut costs by half?",
        "Did you double the throughput?",
        "What did you build with a dozen engineers?",
        "",
        "What " + "really " * 40 + "changed?",
    ],
)
def test_a_question_that_fails_the_check_gives_way_to_a_fixed_one(question: str) -> None:
    llm, _, _ = client(InterviewReply(done=False, question=question))
    assert next_question(None, [], llm) == FIXED_QUESTIONS[0]


def test_a_question_asked_before_gives_way_to_the_next_fixed_one() -> None:
    turns = [Turn(question=FIXED_QUESTIONS[0], answer="A new team."), Turn(question=QUESTION)]
    llm, _, _ = client(InterviewReply(done=False, question=QUESTION))
    assert next_question(None, turns, llm) == FIXED_QUESTIONS[1]


def test_a_refusal_gives_way_to_a_fixed_question() -> None:
    fake = FakeMessages(refusal())
    llm = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    assert next_question(None, [], llm) == FIXED_QUESTIONS[0]


def test_an_unusable_reply_gives_way_to_a_fixed_question() -> None:
    llm, _, _ = client(*(["not json"] * 2))
    assert next_question(None, [], llm) == FIXED_QUESTIONS[0]


def test_the_fixed_questions_run_out() -> None:
    turns = [Turn(question=question) for question in FIXED_QUESTIONS]
    assert fixed_question(turns) is None


def test_the_fixed_questions_pass_the_check() -> None:
    assert all(usable(question) for question in FIXED_QUESTIONS)


def test_the_longest_question_allowed() -> None:
    question = "W" + "o" * (QUESTION_LIMIT - 2) + "?"
    assert usable(question)
    assert not usable("W" + question)


def test_an_empty_interview() -> None:
    assert interview_text([]) == "No questions asked yet."


def test_a_question_without_numbers_in_one_sentence_passes() -> None:
    assert usable("What did the platform team work on after you joined?")


def test_any_failed_call_gives_way_to_a_fixed_question() -> None:
    """Not only a refusal or an unusable reply: the interview keeps moving."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    fake = FakeMessages(APIConnectionError(request=request))
    llm = LLMClient(Settings(llm_model_standard="standard-model"), fake, MemoryCallLog())
    assert next_question(None, [], llm) == FIXED_QUESTIONS[0]
