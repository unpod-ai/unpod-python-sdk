"""Build a flow in Python instead of hand-writing its JSON.

``Flow(...).to_definition()`` is exactly the body the journey service's ``FlowDefinition``
accepts (``extra="forbid"`` on the flow, its steps and their content), so a
builder flow goes through ``client.flows.create`` like a dict would.

The agent defaults follow the rules the Unpod app's own builder applies:
the journey service refuses an agent flow with no ``agentic_profile``, and ``default`` is
the decision worker's own fallback, so filling it in changes nothing about how
the agent answers. Its tool allowlist fails closed, so it is seeded from the
channels the agent steps send on, plus ``human`` so the agent can always hand
over to a person.
"""

from __future__ import annotations

from typing import Any, Protocol

#: The agent profile the journey service requires on an agent flow (see the module docstring).
DEFAULT_PROFILE = "default"
#: the journey service's bounds on ``agentic_review_topics``.
MAX_REVIEW_TOPICS = 10
MAX_REVIEW_TOPIC_CHARS = 120


class ChannelLike(Protocol):
    """What a step needs from a channel: ``client.flows.channels.list`` returns these.

    ``connector_type`` is the channel's kind (``email``, ``whatsapp``): it is
    the step's ``action``, and the journey service's action roster knows only kinds.
    ``connector_id`` is the connector that sends on it (``gmail``): it is what
    the agent's tool allowlist names, because ``email`` on its own is
    the journey service's stub port and dispatches nothing.
    """

    connector_type: str | None
    connector_id: str | None
    account_id: str | None


def _send_target(
    channel: ChannelLike | None,
    account_id: str | None,
    action: str | None,
    connector: str | None,
) -> tuple[str, str, str]:
    """The (action, connector, account_id) a step sends with."""
    if channel is not None:
        if not getattr(channel, "account_id", None):
            raise ValueError(
                "this channel is not ready to send from: connect it first "
                "with client.flows.channels.connect(channel)"
            )
        action = getattr(channel, "connector_type", None) or action
        connector = getattr(channel, "connector_id", None) or connector
        account_id = str(channel.account_id)
    elif not account_id:
        raise ValueError("a step needs a channel, or an account_id with its action")
    if not action:
        raise ValueError("an account_id needs its action (e.g. action='email')")
    return action, connector or action, account_id


class _Step:
    """One step; ``to_step`` places it at its position in the flow."""

    def __init__(
        self,
        channel: ChannelLike | None,
        account_id: str | None,
        action: str | None,
        connector: str | None,
    ) -> None:
        self.action, self.connector, self.account_id = _send_target(
            channel, account_id, action, connector
        )

    def to_step(self, position: int) -> dict[str, Any]:
        return {
            "id": f"step_{position}",
            "order": position,
            "action": self.action,
            "connector_account_ids": [self.account_id],
        }


class AgentStep(_Step):
    """The agent decides what to say, on one channel, turn by turn."""

    agentic = True

    def __init__(
        self,
        *,
        channel: ChannelLike | None = None,
        agent: str | None = None,
        account_id: str | None = None,
        action: str | None = None,
        connector: str | None = None,
        reply_wait: str | None = None,
    ) -> None:
        super().__init__(channel, account_id, action, connector)
        self.agent = agent
        self.reply_wait = reply_wait

    def to_step(self, position: int) -> dict[str, Any]:
        step = super().to_step(position)
        if self.agent:
            step["agent_id"] = self.agent
        step["agentic"] = {"enabled": True}
        if self.reply_wait:
            step["reply_wait"] = self.reply_wait
        return step


class MessageStep(_Step):
    """A fixed message, sent as written."""

    agentic = False

    def __init__(
        self,
        *,
        body: str,
        channel: ChannelLike | None = None,
        subject: str | None = None,
        account_id: str | None = None,
        action: str | None = None,
        connector: str | None = None,
    ) -> None:
        if not body or not body.strip():
            raise ValueError("a message step needs a body")
        super().__init__(channel, account_id, action, connector)
        self.body = body
        self.subject = subject

    def to_step(self, position: int) -> dict[str, Any]:
        step = super().to_step(position)
        content: dict[str, Any] = {"mode": "fixed", "body": self.body}
        if self.subject:
            content["subject"] = self.subject
        step["content"] = content
        return step


def _clean_topics(topics: list[str] | None) -> list[str]:
    cleaned = [t.strip() for t in topics or [] if t and t.strip()]
    if len(cleaned) > MAX_REVIEW_TOPICS:
        raise ValueError(f"at most {MAX_REVIEW_TOPICS} review topics")
    if any(len(t) > MAX_REVIEW_TOPIC_CHARS for t in cleaned):
        raise ValueError(
            f"a review topic is at most {MAX_REVIEW_TOPIC_CHARS} characters"
        )
    return cleaned


class Flow:
    """A whole flow: its steps, plus how its agent behaves.

    ``goal`` is the agent's objective (the journey service's ``agentic_plan``).
    ``review_sensitive`` holds the agent's replies to sensitive email for a
    person to approve; ``review_topics`` adds topics of your own to the
    built-in list.
    """

    def __init__(
        self,
        *,
        name: str,
        steps: list[AgentStep | MessageStep],
        goal: str | None = None,
        follow_ups: bool = True,
        review_sensitive: bool = False,
        review_topics: list[str] | None = None,
        flow_id: str | None = None,
        triggers: list[Any] | None = None,
    ) -> None:
        if not steps:
            raise ValueError("a flow needs at least one step")
        self.name = name
        self.steps = list(steps)
        self.goal = goal
        self.follow_ups = follow_ups
        self.review_sensitive = review_sensitive
        self.review_topics = _clean_topics(review_topics)
        self.flow_id = flow_id
        self.triggers = triggers

    def to_definition(self) -> dict[str, Any]:
        """The flow as the journey service's ``FlowDefinition`` JSON."""
        definition: dict[str, Any] = {
            "name": self.name,
            "steps": [step.to_step(i) for i, step in enumerate(self.steps, start=1)],
        }
        if self.flow_id:
            definition["flow_id"] = self.flow_id
        if self.triggers is not None:
            definition["triggers"] = self.triggers
        agent_actions = sorted({s.connector for s in self.steps if s.agentic})
        if not agent_actions:
            # the journey service reads an absent profile as "not an agent flow".
            return definition
        definition["agentic_profile"] = DEFAULT_PROFILE
        definition["agentic_tools"] = [*agent_actions, "human"]
        if self.goal:
            definition["agentic_plan"] = self.goal
        definition["agentic_follow_ups"] = self.follow_ups
        if self.review_sensitive:
            definition["agentic_review_sensitive"] = True
        if self.review_topics:
            definition["agentic_review_topics"] = self.review_topics
        return definition
