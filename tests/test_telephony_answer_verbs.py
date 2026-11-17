"""Answer builders for record / get_digits / get_input, in the shapes
SuperSBC parses (``answer_url/actions.rs`` RecordAction, GetDigitsAction,
GetInputAction; ``xml.rs`` attribute names). The same output was checked
against SuperSBC's own parse_xml / parse_json on 2026-11-18."""

from __future__ import annotations

import pytest

from unpod.telephony.answer import Response


def test_record_blocking_and_session():
    r = Response().record(max_length=60, callback_url="https://x/rec")
    assert r.actions == [
        {"type": "record", "max_duration_secs": 60, "callback_url": "https://x/rec"}
    ]
    assert '<Record maxLength="60" callbackUrl="https://x/rec"/>' in r.to_xml()
    r = Response().record(
        record_session=True, action="https://x/a", method="GET", callback_method="POST"
    )
    assert r.actions[0] == {
        "type": "record",
        "record_session": True,
        "action": "https://x/a",
        "action_method": "GET",
        "callback_method": "POST",
    }
    assert (
        '<Record recordSession="true" action="https://x/a" method="GET" '
        'callbackMethod="POST"/>' in r.to_xml()
    )


@pytest.mark.parametrize(
    "kw",
    [
        {"action": "https://x/a"},  # action needs record_session
        {"record_session": True, "method": "PUT"},
        {"max_length": 0},
    ],
)
def test_record_rejects(kw):
    with pytest.raises(ValueError):
        Response().record(**kw)


def test_get_digits_with_prompts():
    r = Response().get_digits(
        "https://x/act",
        num_digits=4,
        finish_on_key="#",
        retries=2,
        valid_digits="123*#",
        say="Enter PIN",
        play="https://a/b.mp3",
    )
    assert r.actions == [
        {
            "type": "get_digits",
            "action": "https://x/act",
            "finish_on_key": "#",
            "num_digits": 4,
            "retries": 2,
            "valid_digits": "123*#",
            "prompts": [
                {"type": "speak", "text": "Enter PIN"},
                {"type": "play", "url": "https://a/b.mp3"},
            ],
        }
    ]
    assert r.to_xml().endswith(
        '<GetDigits action="https://x/act" finishOnKey="#" numDigits="4" retries="2" '
        'validDigits="123*#"><Speak>Enter PIN</Speak><Play>https://a/b.mp3</Play>'
        "</GetDigits></Response>"
    )


def test_get_digits_finish_on_key_can_be_disabled_and_is_checked():
    assert Response().get_digits(finish_on_key="").actions[0]["finish_on_key"] == ""
    with pytest.raises(ValueError):
        Response().get_digits(finish_on_key="##")


def test_get_input_dtmf_only():
    r = Response().get_input("https://x/in", input_type="dtmf", execution_timeout=10)
    assert r.actions[0] == {
        "type": "get_input",
        "action": "https://x/in",
        "input_type": "dtmf",
        "execution_timeout": 10,
    }
    assert (
        '<GetInput action="https://x/in" inputType="dtmf" executionTimeout="10">'
        in (r.to_xml())
    )
    with pytest.raises(ValueError, match="dtmf"):
        Response().get_input(input_type="speech")
