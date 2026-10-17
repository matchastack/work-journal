"""The fake Claude client: scripted replies in order, or replies by request from several threads."""

import threading
from pathlib import Path
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.fake import FakeMessages, output_name, prompt_text, reply
from app.llm.prompts import load_prompt
from app.llm.usage import MemoryCallLog
from app.schema.common import Model, NonEmptyStr

PROMPT = load_prompt("triage", 1, root=Path(__file__).parent / "fixtures" / "prompts")


class Triage(Model):
    work_related: bool
    topics: tuple[NonEmptyStr, ...] = ()


class Echo(Model):
    message: NonEmptyStr


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def client(fake: FakeMessages) -> tuple[LLMClient, MemoryCallLog]:
    log = MemoryCallLog()
    return LLMClient(Settings(llm_model_light="light-model"), fake, log), log


def triage(llm: LLMClient, message: str, output: type[Model] = Triage) -> Any:
    variables = {"sender": "Casey", "message": message}
    return llm.call("message_triage", PROMPT, output, variables=variables).output


def test_scripted_replies_answer_in_order() -> None:
    fake = FakeMessages(reply(Triage(work_related=True)), reply(Triage(work_related=False)))
    llm, log = client(fake)
    assert triage(llm, "Moved the import onto a queue.") == Triage(work_related=True)
    assert triage(llm, "Had lunch.") == Triage(work_related=False)
    assert [call.request_id for call in log.calls] == ["req_fake_1", "req_fake_2"]
    with pytest.raises(AssertionError, match="no scripted reply left"):
        triage(llm, "One more.")
    assert len(fake.requests) == 3


def test_requests_from_several_threads_each_get_one_reply() -> None:
    count = 20
    fake = FakeMessages(*(reply(Triage(work_related=True)) for _ in range(count)))
    llm, log = client(fake)
    start = threading.Barrier(count, timeout=5)
    results: list[Triage] = []

    def call(number: int) -> None:
        start.wait()
        results.append(triage(llm, f"Message {number}."))

    threads = [threading.Thread(target=call, args=(number,)) for number in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [Triage(work_related=True)] * count
    assert (len(fake.requests), fake.replies) == (count, [])
    assert sorted(call.request_id or "" for call in log.calls) == sorted(
        f"req_fake_{number}" for number in range(1, count + 1)
    )


def test_answer_replies_by_what_each_request_asks() -> None:
    def answer(request: dict[str, Any]) -> BetaMessage | Exception:
        message = prompt_text(request).split("\n\n", 1)[1]
        if output_name(request) == "Echo":
            return reply(Echo(message=message))
        if message == "Fail.":
            return ValueError("not a reply")
        return reply(Triage(work_related="queue" in message))

    fake = FakeMessages(answer=answer)
    llm, _ = client(fake)
    assert triage(llm, "Had lunch.") == Triage(work_related=False)
    assert triage(llm, "Echo this.", Echo) == Echo(message="Echo this.")
    assert triage(llm, "Moved the import onto a queue.") == Triage(work_related=True)
    with pytest.raises(ValueError, match="not a reply"):
        triage(llm, "Fail.")
    assert [output_name(request) for request in fake.requests] == [
        "Triage",
        "Echo",
        "Triage",
        "Triage",
    ]
    assert prompt_text(fake.requests[0]) == "Message from Casey:\n\nHad lunch."


def test_scripted_replies_and_answer_dont_mix() -> None:
    with pytest.raises(ValueError, match="not both"):
        FakeMessages(reply(Triage(work_related=True)), answer=lambda request: ValueError())
