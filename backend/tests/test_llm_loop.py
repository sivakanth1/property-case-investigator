"""Live-model loop tests with a scripted fake client (no network, no paid calls)."""
import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import InvestigationRun, RunEvent, Task, TaskProposal

from conftest import run_to_end


@pytest.fixture
def live_env(fixture_property, monkeypatch):
    monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key")
    monkeypatch.setenv("FEATHERLESS_MODEL", "test/fake-model")
    return fixture_property


def reply(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def call(call_id, name, args):
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeClient:
    def __init__(self, investigator, reviewer=None):
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._investigator, self._reviewer = investigator, reviewer or (lambda kw: reply('{"decision": "approve", "issues": []}'))

    def _create(self, **kw):
        self.requests.append(kw)
        if kw["messages"][0]["content"].startswith("You review"):
            return self._reviewer(kw)
        return self._investigator(kw)


def tool_results(messages):
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append(json.loads(m["content"]))
        elif m["role"] == "user" and m["content"].startswith("TOOL_RESULT"):
            out.append(json.loads(m["content"].split("\n", 1)[1].rsplit("\n", 1)[0]))
    return out


def first_case(messages):
    cases = next(r["result"] for r in tool_results(messages) if r.get("ok") and "cases" in r.get("result", {}))
    c = cases["cases"][0]
    return cases["property"]["id"], c["case_id"], c["evidence_ids"]


def standard_policy(kw, pid):
    """memory -> cases -> one verification task -> final summary."""
    msgs = kw["messages"]
    n = len(tool_results(msgs))
    if n == 0:
        return reply(tool_calls=[call("c1", "get_property_memory", {"property_id": pid})])
    if n == 1:
        return reply(tool_calls=[call("c2", "get_property_cases", {"property_id": pid})])
    if n == 2:
        prop, case_id, ev = first_case(msgs)
        return reply(tool_calls=[call("c3", "propose_task", {
            "property_id": prop, "action_type": "verify_current_condition", "case_ids": [case_id], "priority": "low",
            "title": f"Verify current condition for case {case_id}",
            "reason": f"Case {case_id} appears in 2016 source records; verify whether the condition was resolved.",
            "evidence_ids": ev})])
    return reply("<think>internal notes</think>Reviewed 2 cases from 2016 records. One verification task proposed. "
                 "Uncertainty: historical data only; current condition unknown.")


def _run(run_id):
    with session_scope() as s:
        run = s.get(InvestigationRun, run_id)
        events = [e.event_type for e in s.scalars(select(RunEvent).where(RunEvent.run_id == run_id))]
        proposals = list(s.scalars(select(TaskProposal).where(TaskProposal.run_id == run_id)))
        tasks = list(s.scalars(select(Task)))
        return run, events, proposals, tasks


def test_native_tool_loop_creates_reviewed_task(live_env):
    pid = live_env["a"]
    fake = FakeClient(lambda kw: standard_policy(kw, pid))
    run_id = run_to_end(pid, llm_factory=lambda settings: fake)
    run, events, proposals, tasks = _run(run_id)
    assert run.mode == "live_model" and run.model == "test/fake-model" and run.status == "completed"
    assert events.count("tool_call") == 3 and "review_applied" in events
    assert [p.status for p in proposals] == ["committed"] and len(tasks) == 1
    assert "<think>" not in run.summary and run.summary.startswith("Reviewed 2 cases")
    assert "tools" in fake.requests[0]  # native tool definitions were sent
    assert any(r["messages"][0]["content"].startswith("You review") for r in fake.requests)


def test_auto_mode_falls_back_to_json_protocol_when_tools_rejected(live_env):
    pid = live_env["a"]

    def policy(kw):
        if "tools" in kw:
            raise openai.BadRequestError("tools not supported", response=httpx.Response(
                400, request=httpx.Request("POST", "https://example.invalid/v1/chat/completions")), body=None)
        native = standard_policy(kw, pid).choices[0].message
        if native.tool_calls:
            fn = native.tool_calls[0].function
            return reply(json.dumps({"tool": fn.name, "arguments": json.loads(fn.arguments)}))
        return reply(json.dumps({"final": native.content}))

    run_id = run_to_end(pid, llm_factory=lambda settings: FakeClient(policy))
    run, events, proposals, tasks = _run(run_id)
    assert "mode_switch" in events and run.status == "completed" and len(tasks) == 1


def test_reviewer_failure_preserves_drafts_as_partial(live_env):
    pid = live_env["a"]
    fake = FakeClient(lambda kw: standard_policy(kw, pid), reviewer=lambda kw: reply("I think it looks fine."))
    run_id = run_to_end(pid, llm_factory=lambda settings: fake)
    run, events, proposals, tasks = _run(run_id)
    assert run.status == "partial" and "reviewer_failed" in events
    assert [p.status for p in proposals] == ["draft"] and tasks == []
    assert "model review failed" in run.summary


def test_one_revision_cycle_corrects_flagged_proposal(live_env):
    pid = live_env["a"]
    reviews = []

    def reviewer(kw):
        packet = json.loads(kw["messages"][1]["content"])
        reviews.append(packet)
        if len(reviews) == 1:
            pid_ = packet["proposals"][0]["proposal_id"]
            return reply(json.dumps({"decision": "revise", "issues": [{"proposal_id": pid_, "reason": "Name the year."}]}))
        return reply('{"decision": "approve", "issues": []}')

    def policy(kw):
        msgs = kw["messages"]
        last = msgs[-1]["content"] if msgs[-1]["role"] == "user" else ""
        if last.startswith("The reviewer flagged"):
            prop, case_id, ev = first_case(msgs)
            proposal_id = last.split("- ", 1)[1].split(":", 1)[0]
            return reply(tool_calls=[call("r1", "propose_task", {
                "property_id": prop, "action_type": "verify_current_condition", "case_ids": [case_id], "priority": "low",
                "title": f"Verify current condition for case {case_id}", "revises_proposal_id": proposal_id,
                "reason": f"Case {case_id} was recorded in 2016; verify whether the condition was resolved.",
                "evidence_ids": ev})])
        if msgs[-1]["role"] == "tool" and msgs[-1]["tool_call_id"] == "r1":
            return reply("Revised the task to cite the 2016 record. Uncertainty: current condition unknown.")
        return standard_policy(kw, pid)

    run_id = run_to_end(pid, llm_factory=lambda settings: FakeClient(policy, reviewer))
    run, events, proposals, tasks = _run(run_id)
    assert run.status == "completed" and "revision" in events and len(reviews) == 2
    assert len(proposals) == 1 and proposals[0].status == "committed" and "recorded in 2016" in proposals[0].reason
    assert len(tasks) == 1


def test_tool_budget_exhaustion_gives_partial_result(live_env):
    pid = live_env["a"]

    def greedy(kw):
        if "tools" not in kw:
            return reply("Budget ran out after repeated case listing. Uncertainty: no tasks proposed.")
        return reply(tool_calls=[call(f"g{len(kw['messages'])}", "get_property_cases", {"property_id": pid})])

    run_id = run_to_end(pid, llm_factory=lambda settings: FakeClient(greedy))
    run, events, proposals, tasks = _run(run_id)
    checkpoint = json.loads(run.checkpoint_json)
    assert checkpoint["tool_calls_used"] == 8 and checkpoint["stop_reason"] == "tool_budget"
    assert run.status == "partial" and "Partial result" in run.summary and "budget_exhausted" in events


def test_cross_property_tool_call_returns_structured_error_and_continues(live_env):
    a, b = live_env["a"], live_env["b"]

    def policy(kw):
        if not tool_results(kw["messages"]):
            return reply(tool_calls=[call("x1", "get_case_details", {"property_id": b, "case_id": "C9"})])
        err = tool_results(kw["messages"])[0]
        assert err["ok"] is False and err["error"]["type"] == "property_access_denied"
        return reply("Access to another property was denied; no findings. Uncertainty: records not reviewed.")

    run_id = run_to_end(a, llm_factory=lambda settings: FakeClient(policy))
    run, events, proposals, tasks = _run(run_id)
    assert run.status == "completed" and "tool_error" in events and tasks == []
    assert "No findings or tasks were proposed" in run.summary


def test_model_timeout_is_recorded(live_env):
    pid = live_env["a"]

    def slow(kw):
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))

    run_id = run_to_end(pid, llm_factory=lambda settings: FakeClient(slow))
    run, events, proposals, tasks = _run(run_id)
    assert run.status == "failed" and "timed out" in run.error and tasks == []
