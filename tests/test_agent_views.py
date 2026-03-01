"""Unit tests for agent views: StepMetadata, AgentHistory, AgentHistoryList."""

import json
import tempfile
from pathlib import Path

import pytest

from heimdall.agent.views import (
    ActionResult,
    AgentBrain,
    AgentHistory,
    AgentHistoryList,
    AgentOutput,
    BrowserStateSnapshot,
    StepMetadata,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _output(
    eval_prev: str = "Success",
    memory: str = "Working",
    next_goal: str = "Click login",
    todo: list[str] | None = None,
    actions: list[dict] | None = None,
    thinking: str | None = None,
) -> AgentOutput:
    return AgentOutput(
        thinking=thinking,
        evaluation_previous_goal=eval_prev,
        memory=memory,
        next_goal=next_goal,
        todo=todo,
        action=actions or [{"click": {"index": 0}}],
    )


def _meta(start: float = 0.0, end: float = 5.0, step: int = 1) -> StepMetadata:
    return StepMetadata(step_start_time=start, step_end_time=end, step_number=step)


def _result(
    is_done: bool = False,
    success: bool = True,
    error: str | None = None,
    extracted: str | None = None,
) -> ActionResult:
    return ActionResult(is_done=is_done, success=success, error=error, extracted_content=extracted)


def _history(
    step_number: int = 1,
    output: AgentOutput | None = None,
    results: list[ActionResult] | None = None,
    metadata: StepMetadata | None = None,
) -> AgentHistory:
    return AgentHistory(
        step_number=step_number,
        model_output=output or _output(),
        results=results or [_result()],
        metadata=metadata or _meta(step=step_number),
    )


# ── StepMetadata ──────────────────────────────────────────────────────────────


class TestStepMetadata:
    def test_duration_seconds(self):
        meta = _meta(start=10.0, end=13.5)
        assert meta.duration_seconds == pytest.approx(3.5)

    def test_zero_duration(self):
        meta = _meta(start=5.0, end=5.0)
        assert meta.duration_seconds == 0.0


# ── AgentOutput.current_state ─────────────────────────────────────────────────


class TestAgentOutputCurrentState:
    def test_returns_agent_brain(self):
        output = _output(eval_prev="Good", memory="Noted", next_goal="Done")
        brain = output.current_state
        assert isinstance(brain, AgentBrain)
        assert brain.evaluation_previous_goal == "Good"
        assert brain.memory == "Noted"
        assert brain.next_goal == "Done"

    def test_none_fields_become_empty_string(self):
        output = AgentOutput(action=[{"done": {}}])
        brain = output.current_state
        assert brain.evaluation_previous_goal == ""
        assert brain.memory == ""
        assert brain.next_goal == ""


# ── AgentHistory.format_for_prompt ────────────────────────────────────────────


class TestFormatForPrompt:
    def test_returns_empty_string_when_no_output(self):
        h = AgentHistory(step_number=1)
        assert h.format_for_prompt() == ""

    def test_contains_step_tags(self):
        h = _history(step_number=3)
        text = h.format_for_prompt()
        assert "<step_3>" in text
        assert "</step_3>" in text

    def test_contains_evaluation(self):
        h = _history(output=_output(eval_prev="Previous step failed"))
        text = h.format_for_prompt()
        assert "Previous step failed" in text

    def test_contains_memory(self):
        h = _history(output=_output(memory="Visited 2 pages so far"))
        text = h.format_for_prompt()
        assert "Visited 2 pages so far" in text

    def test_contains_todo_when_set(self):
        h = _history(output=_output(todo=["Step A", "Step B"]))
        text = h.format_for_prompt()
        assert "Step A" in text
        assert "Step B" in text

    def test_no_todo_section_when_none(self):
        h = _history(output=_output(todo=None))
        text = h.format_for_prompt()
        assert "Todo:" not in text

    def test_contains_next_goal(self):
        h = _history(output=_output(next_goal="Navigate to checkout"))
        text = h.format_for_prompt()
        assert "Navigate to checkout" in text

    def test_successful_action_shown(self):
        output = _output(actions=[{"click": {"index": 2}}])
        h = _history(output=output, results=[_result(success=True)])
        text = h.format_for_prompt()
        assert "click" in text
        assert "Success" in text

    def test_successful_action_with_extracted_content(self):
        output = _output(actions=[{"extract": {"selector": ".price"}}])
        h = _history(
            output=output,
            results=[_result(success=True, extracted="$29.99")],
        )
        text = h.format_for_prompt()
        assert "$29.99" in text

    def test_failed_action_shows_error(self):
        output = _output(actions=[{"click": {"index": 99}}])
        h = _history(output=output, results=[_result(success=False, error="Element not found")])
        text = h.format_for_prompt()
        assert "Failed" in text
        assert "Element not found" in text


# ── AgentHistoryList ──────────────────────────────────────────────────────────


class TestAgentHistoryList:
    def _list(self, n: int = 3) -> AgentHistoryList:
        hl = AgentHistoryList()
        for i in range(1, n + 1):
            hl.add(
                _history(step_number=i, metadata=_meta(start=float(i - 1), end=float(i), step=i))
            )
        return hl

    # ── len

    def test_len_empty(self):
        hl = AgentHistoryList()
        assert len(hl) == 0

    def test_len_after_adding(self):
        hl = self._list(4)
        assert len(hl) == 4

    # ── last_output

    def test_last_output_none_when_empty(self):
        hl = AgentHistoryList()
        assert hl.last_output() is None

    def test_last_output_returns_last(self):
        hl = self._list(3)
        hl.add(_history(step_number=99, output=_output(next_goal="THE_LAST")))
        assert hl.last_output().next_goal == "THE_LAST"

    # ── is_done / is_successful

    def test_is_done_false_by_default(self):
        hl = self._list(2)
        assert hl.is_done() is False

    def test_is_done_true_when_last_result_is_done(self):
        hl = AgentHistoryList()
        hl.add(_history(results=[_result(is_done=True, success=True)]))
        assert hl.is_done() is True

    def test_is_successful_none_when_not_done(self):
        hl = self._list(2)
        assert hl.is_successful() is None

    def test_is_successful_true_when_done_and_success(self):
        hl = AgentHistoryList()
        hl.add(_history(results=[_result(is_done=True, success=True)]))
        assert hl.is_successful() is True

    def test_is_successful_false_when_done_and_failure(self):
        hl = AgentHistoryList()
        hl.add(_history(results=[_result(is_done=True, success=False)]))
        assert hl.is_successful() is False

    # ── total_duration_seconds

    def test_total_duration_sums_metadata(self):
        hl = self._list(3)  # each step is 1 second; 3 steps → 3.0
        assert hl.total_duration_seconds() == pytest.approx(3.0)

    def test_total_duration_zero_when_no_metadata(self):
        hl = AgentHistoryList()
        hl.add(AgentHistory(step_number=1, model_output=_output()))
        assert hl.total_duration_seconds() == 0.0

    # ── format_for_prompt with max_items

    def test_format_for_prompt_all_items(self):
        hl = self._list(3)
        text = hl.format_for_prompt()
        assert "<step_1>" in text
        assert "<step_2>" in text
        assert "<step_3>" in text

    def test_format_for_prompt_max_items_truncates(self):
        hl = self._list(5)
        text = hl.format_for_prompt(max_items=2)
        assert "<step_1>" not in text
        assert "<step_2>" not in text
        assert "<step_3>" not in text  # too old
        assert "<step_4>" in text
        assert "<step_5>" in text

    # ── screenshot_paths

    def test_screenshot_paths_returns_list(self):
        hl = self._list(3)
        paths = hl.screenshot_paths()
        assert len(paths) == 3

    def test_screenshot_paths_n_last(self):
        hl = self._list(5)
        paths = hl.screenshot_paths(n_last=2)
        assert len(paths) == 2

    # ── save_to_file / load_from_file

    def test_round_trip_json(self):
        hl = self._list(2)
        with tempfile.TemporaryDirectory() as tmp:
            filepath = Path(tmp) / "history.json"
            hl.save_to_file(filepath)
            loaded = AgentHistoryList.load_from_file(filepath)
            assert len(loaded) == 2

    def test_saved_json_is_valid(self):
        hl = self._list(1)
        with tempfile.TemporaryDirectory() as tmp:
            filepath = Path(tmp) / "history.json"
            hl.save_to_file(filepath)
            data = json.loads(filepath.read_text())
            assert "history" in data
            assert isinstance(data["history"], list)
            assert len(data["history"]) == 1

    # ── agent_steps

    def test_agent_steps_returns_list_of_strings(self):
        hl = self._list(2)
        steps = hl.agent_steps()
        assert len(steps) == 2
        assert all(isinstance(s, str) for s in steps)

    def test_agent_steps_contain_step_number(self):
        hl = self._list(3)
        steps = hl.agent_steps()
        assert "Step 1" in steps[0]
        assert "Step 2" in steps[1]
        assert "Step 3" in steps[2]
