import json
from types import SimpleNamespace

import pytest

from attribution import harness
from attribution.execution_trace import ExecutionTraceObserver
from attribution.harness import (
    COORDINATOR_INSTRUCTIONS, HARNESS_INSTRUCTIONS, HarnessAttributionOutput,
    _exception_details, _harness_kwargs, _language_requirement,
)


class DummyAgent:
    def as_tool(self, **kwargs):
        return kwargs


class DummyClient:
    def as_agent(self, **kwargs):
        return DummyAgent()


def test_harness_uses_pinned_core_strict_controls():
    options = _harness_kwargs(DummyClient())
    assert options["agent_instructions"]
    assert options["tools"]
    assert options["auto_approval_rules"] == []
    assert options["loop_max_iterations"] == 15
    assert options["disable_file_memory"] is True
    assert options["disable_tool_auto_approval"] is True


@pytest.mark.asyncio
async def test_foundry_function_call_limit_is_client_configuration(monkeypatch):
    import agent_framework
    import agent_framework.foundry
    import azure.identity

    captured = {"events": []}

    class FakeFoundryChatClient(DummyClient):
        def __init__(self, **kwargs):
            captured["client"] = kwargs

    class FakeCredential:
        pass

    class FakeHarnessAgent:
        async def run(self, prompt, **kwargs):
            captured["prompt"] = prompt
            captured["run"] = kwargs
            return SimpleNamespace(value=HarnessAttributionOutput(
                findings=[], summary="Structured response",
            ))

    async def emit(event_type, payload):
        captured["events"].append((event_type, payload))

    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.test")
    monkeypatch.setenv("FOUNDRY_MODEL_DEPLOYMENT", "test-model")
    monkeypatch.setenv("ATTRIBUTION_DEBUG_RAW_IO", "false")
    monkeypatch.setattr(agent_framework.foundry, "FoundryChatClient", FakeFoundryChatClient)
    monkeypatch.setattr(azure.identity, "AzureCliCredential", FakeCredential)
    def create_agent(**kwargs):
        captured["harness"] = kwargs
        return FakeHarnessAgent()

    monkeypatch.setattr(agent_framework, "create_harness_agent", create_agent)
    monkeypatch.setattr(harness, "build_attribution_report",
                        lambda _case, output: output)

    result = await harness.run_harness_attribution({
        "case_id": "case-1",
        "snapshot": {
            "shop": "shop-1", "shop_name": "Test Store",
            "sku_info": {"goods_code": "sku-1", "goods_name": "Test Beer", "category": "啤酒"},
            "decision_date": "2026-07-23",
        },
        "reason_code": "OPERATOR",
        "reason_text": "Heatwave forecast for the weekend.",
        "recommended_qty": 10,
        "override_qty": 15,
        "output_language": "en-US",
        "_trace_emitter": emit,
    })

    assert captured["client"]["function_invocation_configuration"] == {
        "max_function_calls": 10,
    }
    assert captured["run"]["options"] == {
        "response_format": HarnessAttributionOutput,
    }
    assert len(captured["client"]["middleware"]) == 1
    assert len(captured["harness"]["middleware"]) == 1
    assert "user-facing natural-language output in English" in captured["prompt"]
    assert "user-facing natural-language output in English" in (
        captured["harness"]["agent_instructions"])
    # The model can only explain an override it can actually see, and the operator's own
    # words must reach it while staying labelled as evidence rather than instructions.
    prompt = json.loads(captured["prompt"][captured["prompt"].index("{"):])
    assert prompt["override_to_explain"] == {
        "shop_code": "shop-1", "shop_name": "Test Store", "goods_code": "sku-1",
        "goods_name": "Test Beer", "category": "啤酒", "decision_date": "2026-07-23",
        "system_recommended_qty": 10, "store_manager_override_qty": 15, "direction": "INCREASE",
    }
    assert prompt["operator_claim"] == {
        "reason_code": "OPERATOR", "reason_text": "Heatwave forecast for the weekend.",
        "trust": "untrusted-evidence",
    }
    assert [event_type for event_type, _payload in captured["events"]] == [
        "HARNESS_STARTED",
        "HARNESS_STRUCTURED_OUTPUT",
        "DETERMINISTIC_REPORT_COMPLETED",
    ]
    assert result["summary"] == "Structured response"


def test_chinese_language_contract_requires_simplified_chinese():
    assert "简体中文" in _language_requirement("zh-CN")


def test_language_contract_bans_case_file_wording():
    """The model reached for 案情 unprompted and the output read like an investigation.

    Store managers are the readers here. Legal/case-file register turns an
    explanation of a replenishment decision into something that sounds like it is
    being held against them, so the vocabulary is pinned in the contract rather
    than left to the model's instincts.
    """
    zh = _language_requirement("zh-CN")
    for banned in ("案情", "案件", "本案"):
        assert banned in zh, f"{banned} must be named as forbidden, not merely avoided"
    assert "这次调整" in zh, "the replacement wording has to be supplied, not just the ban"

    en = _language_requirement("en-US")
    assert "case file" in en and "this adjustment" in en

    # The English instructions must not seed the register they forbid: the model
    # translated the prompt's own "case data" straight into 案情.
    for source in (COORDINATOR_INSTRUCTIONS, HARNESS_INSTRUCTIONS):
        assert "case data" not in source


@pytest.mark.asyncio
async def test_debug_observer_records_raw_io_and_redacts_private_data():
    events = []

    async def emit(event_type, payload):
        events.append((event_type, payload))

    class FakeMessage:
        def to_dict(self, **_kwargs):
            return {
                "role": "user",
                "contents": [
                    {"type": "text", "text": "case input"},
                    {"type": "reasoning", "text": "private reasoning"},
                ],
                "authorization": "secret",
            }

    class FakeResult:
        response_id = "response-1"
        model = "test-model"
        finish_reason = "stop"
        usage_details = {"input_token_count": 10, "output_token_count": 4}

        def to_dict(self, **_kwargs):
            return {
                "messages": [{"role": "assistant", "text": "visible output"}],
                "access_token": "secret",
                "chain_of_thought": "private reasoning",
            }

    context = SimpleNamespace(
        client=object(),
        messages=[FakeMessage()],
        options={"response_format": HarnessAttributionOutput},
        stream=False,
        result=None,
    )

    async def call_next():
        context.result = FakeResult()

    observer = ExecutionTraceObserver(
        emit, model="test-model", debug_raw_io=True)
    await observer.chat()(context, call_next)

    payloads = {event_type: payload for event_type, payload in events}
    raw_input = payloads["MODEL_RAW_INPUT"]
    raw_output = payloads["MODEL_RAW_OUTPUT"]
    assert raw_input["messages"][0]["authorization"] == "[REDACTED]"
    assert raw_input["messages"][0]["contents"][1] == {
        "type": "reasoning", "redacted": "private_reasoning",
    }
    assert raw_input["options"]["response_format"].endswith(
        ".HarnessAttributionOutput")
    assert raw_output["response"]["access_token"] == "[REDACTED]"
    assert raw_output["response"]["chain_of_thought"] == "[REDACTED]"
    assert raw_output["response"]["messages"][0]["text"] == "visible output"


def test_exception_details_preserve_safe_root_cause_classification():
    try:
        try:
            raise TypeError("unsupported request option")
        except TypeError as exc:
            raise RuntimeError("client invocation failed") from exc
    except RuntimeError as exc:
        details = _exception_details(exc)

    assert details == {
        "exception_chain": ["RuntimeError", "TypeError"],
        "root_message": "unsupported request option",
    }
