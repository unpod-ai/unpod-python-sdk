"""faq_bot — a plain LLM voice assistant for the playground (M1).

Adding an agent = adding one file like this + one registry line in
``catalog.py``. The builder returns any object the SDK ``Session`` accepts as a
``dialog_machine`` (here a superdialog ``LLMAgent``).
"""

from __future__ import annotations

from typing import Any

from superdialog import LLMAgent

SYSTEM_PROMPT = """You are a helpful FAQ voice assistant in a dev playground.
Answer questions clearly and concisely — spoken responses must stay under three
sentences. Be conversational and natural."""


def build(llm: str) -> Any:
    """Return the dialog machine for a single call, bound to ``llm``."""
    return LLMAgent(llm=llm, system_prompt=SYSTEM_PROMPT)
