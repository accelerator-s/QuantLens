import json
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai.router import AIRouter
from ai.schemas import FactorCandidate
from exceptions import ContractViolation, ModelRouteError
from webui.backend.routes import factor_chat as routes


def candidate(**changes):
    return {
        "factor_name": "gentle_trend_probe",
        "display_name": "温和趋势测试",
        "assumption": "温和上涨可能延续；若扣费后无超额收益则失效。预期持有20个交易日。",
        "direction": "positive",
        "timescale": "daily",
        "dependencies": ["adj_close_wd"],
        "code": "def compute(panel):\n    return panel['adj_close_wd'].pct_change(5)\n",
        **changes,
    }


class Reporter:
    def __init__(self):
        self.lines = []
        self.steps = []

    def __call__(self, line):
        self.lines.append(line)

    def progress(self, step):
        self.steps.append(step)


def research_run(stack, outputs):
    router = AIRouter.__new__(AIRouter)
    router.pick = Mock(return_value=SimpleNamespace(name="test-model"))
    router._chat_completion = Mock(side_effect=outputs)
    stack.enter_context(patch("ai.router._api_key_for", return_value="test-key"))
    stack.enter_context(patch.object(routes, "AIRouter", return_value=router))
    stack.enter_context(patch.object(routes, "_factor_context", return_value="没有重复因子"))
    stack.enter_context(patch.object(routes, "_validate_candidate"))
    panel = pd.DataFrame({"000001": range(10, 40), "600000": range(20, 50)})
    stack.enter_context(patch.object(routes, "_sample_panel", return_value={"adj_close_wd": panel}))
    register = stack.enter_context(patch.object(routes, "_register_draft", return_value=("P-test", "research_test")))
    evolve = stack.enter_context(patch.object(routes, "start_factor_evolution", return_value={
        "research_id": "E-test", "task_id": "T-evolve", "program_id": "RP-test",
    }))
    start = stack.enter_context(patch.object(routes.manager, "start", return_value="T-generate"))
    payload = routes.FactorChatPayload(messages=[routes.ChatMessage(role="user", content="研究温和上涨的延续性，持有20个交易日。")])
    assert routes.start_factor_chat_research(payload)["task_id"] == "T-generate"
    return start.call_args.args[1], router._chat_completion, register, evolve


def test_schema_errors_are_automatically_regenerated_before_registration():
    wrong_names = {"factor_name": "gentle_trend_probe", "description": "温和趋势", "direction": "long_high", "compute": "def compute(panel): pass"}
    with ExitStack() as stack:
        run, completion, register, evolve = research_run(stack, [
            json.dumps(wrong_names), json.dumps(candidate(timescale="20_trading_days")), json.dumps(candidate()),
        ])
        report = Reporter()
        result = run(report)
        assert completion.call_count == 3
        assert "assumption" in completion.call_args_list[1].kwargs["user"]
        assert "20_trading_days" in completion.call_args_list[2].kwargs["user"]
        for call in completion.call_args_list:
            schema = json.loads(call.kwargs["system"].split("FactorCandidate JSON Schema：\n", 1)[1])
            assert schema["additionalProperties"] is False
            assert schema["properties"]["direction"]["enum"] == ["positive", "negative"]
            assert "daily" in schema["properties"]["timescale"]["enum"]
            assert {"assumption", "code", "timescale"} <= set(schema["required"])
        register.assert_called_once()
        assert register.call_args.args[0].timescale == "daily"
        evolve.assert_called_once_with("gentle_trend_probe", model_name="test-model")
        assert result["research_id"] == "E-test"


@pytest.mark.parametrize("invalid", ["not JSON", "[]"])
def test_malformed_output_is_regenerated_automatically(invalid):
    with ExitStack() as stack:
        run, completion, register, _ = research_run(stack, [invalid, json.dumps(candidate())])
        run(Reporter())
        assert completion.call_count == 2
        register.assert_called_once()


def test_exhaustion_keeps_failed_candidates_out_of_registry_and_experiments():
    with ExitStack() as stack:
        run, completion, register, evolve = research_run(stack, [json.dumps(candidate(timescale="20_trading_days"))] * 3)
        report = Reporter()
        with pytest.raises(ContractViolation) as raised:
            run(report)
        assert raised.value.code == "FACTOR_AI_CANDIDATE_EXHAUSTED"
        assert raised.value.context["last_error_code"] == "AI_OUTPUT_SCHEMA_INVALID"
        assert "无需补充投资假设" in raised.value.suggested_action
        assert "已达到尝试上限" in report.lines[-1]
        assert "AI 将自动修正" not in report.lines[-1]
        assert report.steps[-1]["step"] == "候选生成失败"
        assert completion.call_count == 3
        register.assert_not_called()
        evolve.assert_not_called()


def test_transport_failure_does_not_trigger_candidate_regeneration():
    with ExitStack() as stack:
        failure = ModelRouteError(code="AI_HTTP_STATUS_ERROR", message="接口密钥无效")
        run, completion, register, evolve = research_run(stack, [failure])
        with pytest.raises(ModelRouteError) as raised:
            run(Reporter())
        assert raised.value is failure
        assert completion.call_count == 1
        register.assert_not_called()
        evolve.assert_not_called()


@pytest.mark.parametrize("direction", ["positive", "negative"])
def test_registration_preserves_current_direction_contract(direction, tmp_path):
    value = FactorCandidate.model_validate(candidate(direction=direction, model_name="test-model"))
    with ExitStack() as stack:
        proposal = stack.enter_context(patch.object(routes, "ProposalStore"))
        proposal.return_value.create.return_value = (SimpleNamespace(proposal_id="P-test"), None)
        plugin = stack.enter_context(patch.object(routes.FactorPlugin, "default"))
        plugin.return_value.ws.root = tmp_path
        plugin.return_value.write.return_value = tmp_path / "factors" / "probe.py"
        registry = stack.enter_context(patch.object(routes, "FactorRegistry"))
        profiles = stack.enter_context(patch.object(routes, "StrategyProfileStore"))
        stack.enter_context(patch("session.load_session", return_value=SimpleNamespace(active_market="CN-A", active_instrument_class="stock")))
        stack.enter_context(patch("session.has_active_market", return_value=True))
        routes._register_draft(value)
        metadata = registry.return_value.upsert.call_args.args[0]
        assert metadata.direction == direction
        assert metadata.status.value == "draft"
        assert metadata.production_permission is False
        assert profiles.return_value.upsert.call_args.args[0].factors.items[0].name == value.factor_name
