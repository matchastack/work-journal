"""Triage: whether a journal entry is about work (FR-CAP-8).

A light-tier prompt labels each closed entry before extraction. An entry about work goes on to
fact extraction (`app/extraction.py`). Any other, such as chit-chat or weekend plans, gives no
facts, and the bot only acknowledges it. The label is stored with the entry.
"""

from typing import Literal

from app.llm.client import LLMClient
from app.llm.prompts import Prompt, load_prompt
from app.schema.common import Model

PROMPT_NAME = "message_triage"
PROMPT_VERSION = 1

Label = Literal["work", "other"]


class TriageReply(Model):
    label: Label


def triage(note: str, client: LLMClient, prompt: Prompt | None = None) -> Label:
    """Label `note`, the text of a journal entry, with the light-tier model."""
    prompt = prompt or load_prompt(PROMPT_NAME, PROMPT_VERSION)
    result = client.call("message_triage", prompt, TriageReply, variables={"note": note})
    return result.output.label
