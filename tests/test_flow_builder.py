"""Builder classes serialise to the JSON superbook's FlowDefinition accepts."""

from dataclasses import dataclass

import pytest

from unpod.flows import AgentStep, Flow, MessageStep


@dataclass
class _Channel:
    connector_type: str
    connector_id: str
    account_id: str | None


GMAIL = _Channel(connector_type="email", connector_id="gmail", account_id="acc_1")


def test_one_agent_step_flow_serialises_to_the_server_shape():
    flow = Flow(
        name="Support inbox",
        goal="Answer customer emails",
        steps=[AgentStep(channel=GMAIL, agent="sv:bot")],
    )
    assert flow.to_definition() == {
        "name": "Support inbox",
        "steps": [
            {
                "id": "step_1",
                "order": 1,
                "action": "email",
                "agent_id": "sv:bot",
                "connector_account_ids": ["acc_1"],
                "agentic": {"enabled": True},
            }
        ],
        "agentic_profile": "default",
        "agentic_tools": ["gmail", "human"],
        "agentic_plan": "Answer customer emails",
        "agentic_follow_ups": True,
    }


def test_review_fields_are_sent_only_when_on():
    off = Flow(name="f", steps=[AgentStep(channel=GMAIL)]).to_definition()
    assert "agentic_review_sensitive" not in off
    assert "agentic_review_topics" not in off
    on = Flow(
        name="f",
        steps=[AgentStep(channel=GMAIL)],
        review_sensitive=True,
        review_topics=["refunds"],
    ).to_definition()
    assert on["agentic_review_sensitive"] is True
    assert on["agentic_review_topics"] == ["refunds"]


def test_review_topics_are_cleaned_and_bounded():
    flow = Flow(
        name="f",
        steps=[AgentStep(channel=GMAIL)],
        review_sensitive=True,
        review_topics=["  refunds  ", "", "   "],
    )
    assert flow.to_definition()["agentic_review_topics"] == ["refunds"]
    with pytest.raises(ValueError, match="10"):
        Flow(name="f", steps=[AgentStep(channel=GMAIL)], review_topics=["x"] * 11)
    with pytest.raises(ValueError, match="120"):
        Flow(name="f", steps=[AgentStep(channel=GMAIL)], review_topics=["x" * 121])


def test_a_message_step_is_fixed_text():
    flow = Flow(
        name="f",
        steps=[MessageStep(channel=GMAIL, body="Thanks for writing", subject="Hi")],
    )
    step = flow.to_definition()["steps"][0]
    assert step["content"] == {
        "mode": "fixed",
        "body": "Thanks for writing",
        "subject": "Hi",
    }
    assert "agentic" not in step


def test_a_channel_that_is_not_ready_is_refused_at_build_time():
    with pytest.raises(ValueError, match="channels.connect"):
        AgentStep(channel=_Channel("email", "gmail", None))


def test_an_account_id_needs_its_action():
    with pytest.raises(ValueError, match="action"):
        AgentStep(account_id="acc_1")
    definition = Flow(
        name="f",
        steps=[AgentStep(account_id="acc_1", action="email", connector="gmail")],
    ).to_definition()
    assert definition["steps"][0]["action"] == "email"
    assert definition["agentic_tools"] == ["gmail", "human"]


def test_a_flow_with_no_agent_step_has_no_agentic_fields():
    definition = Flow(
        name="f", steps=[MessageStep(channel=GMAIL, body="Hello")]
    ).to_definition()
    assert not any(key.startswith("agentic") for key in definition)


def test_steps_are_numbered_and_tools_deduplicated():
    whatsapp = _Channel("whatsapp", "whatsapp_cloud", "acc_2")
    definition = Flow(
        name="f",
        steps=[
            AgentStep(channel=GMAIL),
            MessageStep(channel=whatsapp, body="Hi"),
            AgentStep(channel=GMAIL),
        ],
        follow_ups=False,
    ).to_definition()
    assert [(s["id"], s["order"]) for s in definition["steps"]] == [
        ("step_1", 1),
        ("step_2", 2),
        ("step_3", 3),
    ]
    assert definition["agentic_tools"] == ["gmail", "human"]
    assert definition["agentic_follow_ups"] is False


def test_a_flow_needs_a_step():
    with pytest.raises(ValueError, match="step"):
        Flow(name="f", steps=[])
