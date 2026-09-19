import json

import httpx
import pytest
from sqlalchemy import func, select

from app.agents.commit_gate import commit_run
from app.agents.runner import RunConflict, create_run, execute_run, mark_interrupted_runs, resume_run
from app.data.houston_client import HoustonClient, SourceUnavailable
from app.data.repository import (
    DataBlocker, coverage, grouped_cases, import_property, list_properties, refresh_property, search_candidates,
)
from app.data.task_store import LocalTaskStore, make_task_key
from app.db import dispose_engine, init_engine, session_scope
from app.models import Feedback, InvestigationRun, Property, SourceRecord, Task, TaskProposal
from app.tools import ToolContext, execute_tool

from conftest import TEST_HCAD_A, TEST_HCAD_B, payload, prow, run_to_end, vrow


def _count(model, *where):
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _run(run_id):
    with session_scope() as s:
        return s.get(InvestigationRun, run_id)


# 1 ---------------------------------------------------------------------------------------------
def test_two_violation_rows_with_one_case_id_count_as_one_case(fixture_property):
    with session_scope() as s:
        cases = {c["case_id"]: c for c in grouped_cases(s, fixture_property["a"])}
    assert set(cases) == {"C1", "C2"}
    assert cases["C1"]["violation_row_count"] == 2 and cases["C1"]["project_row_count"] == 1
    result = execute_tool(ToolContext(run_id=0, property_id=fixture_property["a"]), "get_property_cases",
                          {"property_id": fixture_property["a"]})
    assert result["ok"] and result["result"]["distinct_case_count"] == 2
    recurrence = {r["category"]: r["distinct_cases"] for r in result["result"]["category_recurrence_by_distinct_case"]}
    assert recurrence == {"Nuisance": 1, "Heavy Trash": 1}


def test_recurrence_claim_requires_distinct_cases(fixture_property):
    pid = fixture_property["a"]
    with session_scope() as s:
        c1 = [r.id for r in s.scalars(select(SourceRecord).where(SourceRecord.property_id == pid, SourceRecord.case_id == "C1"))]
    run_id = create_run(pid)["run_id"]
    bad = execute_tool(ToolContext(run_id, pid), "propose_task", {
        "property_id": pid, "action_type": "review_case_history", "case_ids": ["C1"], "priority": "medium",
        "title": "Review repeated nuisance history", "reason": "Nuisance repeated across two rows of case C1.",
        "evidence_ids": c1})
    assert not bad["ok"] and "distinct case" in bad["error"]["message"]


# 2 ---------------------------------------------------------------------------------------------
def test_second_investigation_cannot_create_identical_task(real_data):
    with session_scope() as s:
        pid = next(p.id for p in list_properties(s) if p.hcad == "0551730000009")
    first = run_to_end(pid)
    tasks_after_first = _count(Task, Task.property_id == pid)
    assert _run(first).status == "completed" and tasks_after_first >= 1
    second = run_to_end(pid)
    assert _run(second).status == "completed"
    assert _count(Task, Task.property_id == pid) == tasks_after_first
    with session_scope() as s:
        proposals = list(s.scalars(select(TaskProposal).where(TaskProposal.run_id == second)))
        assert proposals and all(p.status == "committed" for p in proposals)
        keys = [t.task_key for t in s.scalars(select(Task).where(Task.property_id == pid))]
    assert len(keys) == len(set(keys))


# 3 ---------------------------------------------------------------------------------------------
def test_verified_and_dismissed_feedback_persist_after_restart_and_are_respected(real_data, env):
    from fastapi.testclient import TestClient

    from app.main import app

    with session_scope() as s:
        pid = next(p.id for p in list_properties(s) if p.hcad == "0551730000009")
    run_to_end(pid)
    with session_scope() as s:
        tasks = {t.action_type: t.id for t in s.scalars(select(Task).where(Task.property_id == pid))}
    with TestClient(app) as client:
        r = client.patch(f"/api/tasks/{tasks['verify_current_condition']}",
                         json={"status": "verified", "note": "Current inspection completed; no further action needed."})
        assert r.status_code == 200 and r.json()["status"] == "verified"
        r = client.patch(f"/api/tasks/{tasks['reconcile_records']}", json={"status": "dismissed", "note": "City confirmed closed."})
        assert r.status_code == 200
        assert client.patch(f"/api/tasks/{tasks['review_case_history']}", json={"status": "bogus"}).status_code == 422

    dispose_engine()  # simulated backend restart: brand-new engine and connections
    import os
    init_engine(os.environ["DATABASE_URL"])

    with session_scope() as s:
        assert s.get(Task, tasks["verify_current_condition"]).status == "verified"
        assert s.get(Task, tasks["reconcile_records"]).status == "dismissed"
        notes = [f.note for f in s.scalars(select(Feedback).where(Feedback.task_id == tasks["verify_current_condition"]))]
    assert "Current inspection completed; no further action needed." in notes

    before = _count(Task, Task.property_id == pid)
    rerun = run_to_end(pid)
    assert _count(Task, Task.property_id == pid) == before
    with session_scope() as s:
        assert s.get(Task, tasks["verify_current_condition"]).status == "verified"
        assert s.get(Task, tasks["reconcile_records"]).status == "dismissed"
        summary = s.get(InvestigationRun, rerun).summary
    assert "Respected prior feedback" in summary and "verified" in summary


# 4 ---------------------------------------------------------------------------------------------
def test_invalid_or_cross_property_evidence_cannot_create_committed_task(fixture_property):
    a, b = fixture_property["a"], fixture_property["b"]
    with session_scope() as s:
        foreign = s.scalars(select(SourceRecord.id).where(SourceRecord.property_id == b)).first()
        own = s.scalars(select(SourceRecord.id).where(SourceRecord.property_id == a, SourceRecord.case_id == "C1")).first()
    run_id = create_run(a)["run_id"]

    tool = execute_tool(ToolContext(run_id, a), "propose_task", {
        "property_id": a, "action_type": "verify_current_condition", "case_ids": ["C1"], "priority": "low",
        "title": "Verify case C1", "reason": "Case C1 recorded nuisance rows in 2016.", "evidence_ids": [foreign]})
    assert not tool["ok"] and "different property" in tool["error"]["message"]
    denied = execute_tool(ToolContext(run_id, a), "get_property_cases", {"property_id": b})
    assert not denied["ok"] and denied["error"]["type"] == "property_access_denied"
    bad_args = execute_tool(ToolContext(run_id, a), "propose_task", {"property_id": a, "action_type": "demolish"})
    assert not bad_args["ok"] and bad_args["error"]["type"] == "invalid_arguments"

    bad = [
        ("verify_current_condition", ["C1"], [foreign]),        # cross-property evidence
        ("verify_current_condition", ["C1"], [999999]),         # evidence that does not exist
        ("verify_current_condition", ["NOPE"], [own]),          # unknown case id
        ("issue_citation", ["C1"], [own]),                      # action outside the allowlist
    ]
    with session_scope() as s:
        for action, cases, ev in bad:
            s.add(TaskProposal(run_id=run_id, property_id=a, task_key=make_task_key(TEST_HCAD_A, action, cases), action_type=action,
                               case_ids_json=json.dumps(cases), title="Forced proposal", reason="Bypassing the tool layer.",
                               priority="low", evidence_ids_json=json.dumps(ev), status="approved"))
    outcomes = commit_run(run_id, LocalTaskStore(), TEST_HCAD_A)
    assert [o["outcome"] for o in outcomes] == ["rejected"] * 4
    assert _count(Task) == 0


def test_present_tense_claims_are_rejected_but_verification_wording_passes(fixture_property):
    a = fixture_property["a"]
    with session_scope() as s:
        own = s.scalars(select(SourceRecord.id).where(SourceRecord.property_id == a, SourceRecord.case_id == "C1")).first()
    run_id = create_run(a)["run_id"]
    base = {"property_id": a, "action_type": "verify_current_condition", "case_ids": ["C1"], "priority": "low",
            "title": "Verify case C1", "evidence_ids": [own]}
    claim = execute_tool(ToolContext(run_id, a), "propose_task", {**base, "reason": "The lot is currently in violation of 10-451."})
    assert not claim["ok"] and "present condition" in claim["error"]["message"]
    ok = execute_tool(ToolContext(run_id, a), "propose_task", {**base, "reason": "Verify whether the violation still exists; case C1 is from 2016."})
    assert ok["ok"], ok


# 5 ---------------------------------------------------------------------------------------------
def _failing_client(calls: list) -> HoustonClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(503, json={"success": False})
    return HoustonClient(http=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)


def test_api_failure_uses_labeled_cache_or_explicit_blocker(fixture_property):
    calls: list = []
    client = _failing_client(calls)
    result = refresh_property(client, fixture_property["a"], cap=100)
    assert result["status"] == "cached_fallback" and "cached real records" in result["message"]
    assert len(calls) == 3  # one attempt plus two retries
    with session_scope() as s:
        cov = coverage(s, s.get(Property, fixture_property["a"]))
    assert cov["cache_status"] == "cached_after_failed_refresh" and cov["last_error"] and cov["fetched_at"]
    assert cov["row_count"] == 4

    with pytest.raises(SourceUnavailable):
        import_property(client, "9990000000077", cap=100)
    with session_scope() as s:
        empty = Property(hcad="9990000000088", address="TEST FIXTURE EMPTY", normalized_address="TEST FIXTURE EMPTY")
        s.add(empty)
        s.flush()
        empty_id = empty.id
    with pytest.raises(DataBlocker):
        refresh_property(client, empty_id, cap=100)
    with pytest.raises(DataBlocker):
        create_run(empty_id)


# 6 ---------------------------------------------------------------------------------------------
def test_missing_credentials_produce_labeled_deterministic_mode(fixture_property, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    run_id = run_to_end(fixture_property["a"])
    run = _run(run_id)
    assert run.mode == "deterministic_demo" and run.model is None
    assert run.summary.startswith("Deterministic demo mode (scripted policy, not a live AI run)")
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["model"]["mode"] == "deterministic_demo" and "FEATHERLESS_API_KEY" in health["model"]["warning"]
        monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key-not-real")
        health = client.get("/api/health").json()
    assert health["model"]["mode"] == "live_model" and health["model"]["endpoint_host"] == "api.featherless.ai"
    assert health["model"]["model"] == "Qwen/Qwen3-32B"
    assert "test-key-not-real" not in json.dumps(health)


# 7 ---------------------------------------------------------------------------------------------
class SimulatedCrash(BaseException):
    """Stands in for the process dying; bypasses the runner's error handling like a real crash would."""


def test_interrupted_run_resumes_without_duplicate_committed_actions(real_data):
    with session_scope() as s:
        pid = next(p.id for p in list_properties(s) if p.hcad == "0372600000006")

    def crash_mid_investigation(state):
        if state.get("tool_calls_used") == 5 and not state.get("_crashed"):
            state["_crashed"] = True
            raise SimulatedCrash()

    run_id = create_run(pid)["run_id"]
    with pytest.raises(SimulatedCrash):
        execute_run(run_id, hooks={"after_save": crash_mid_investigation})
    assert _run(run_id).status == "running"
    assert mark_interrupted_runs() == 1 and _run(run_id).status == "interrupted"
    other = create_run(pid)["run_id"]  # a newer active run blocks resuming the old one
    with pytest.raises(RunConflict):
        resume_run(run_id)
    with session_scope() as s:
        s.delete(s.get(InvestigationRun, other))
    resume_run(run_id)
    execute_run(run_id)
    run = _run(run_id)
    assert run.status == "completed"
    assert json.loads(run.checkpoint_json)["tool_calls_used"] == 8
    task_count = _count(Task, Task.property_id == pid)
    assert task_count == 3 and _count(TaskProposal, TaskProposal.run_id == run_id) == 3

    # Crash after the commit transaction but before the run was marked finished: replaying commit is harmless.
    with session_scope() as s:
        run = s.get(InvestigationRun, run_id)
        state = json.loads(run.checkpoint_json)
        state["phase"] = "committing"
        run.checkpoint_json, run.status = json.dumps(state), "running"
        for p in s.scalars(select(TaskProposal).where(TaskProposal.run_id == run_id)):
            p.status = "approved"  # worst case: commit is replayed in full
    mark_interrupted_runs()
    resume_run(run_id)
    execute_run(run_id)
    assert _run(run_id).status == "completed"
    assert _count(Task, Task.property_id == pid) == task_count


# 8 ---------------------------------------------------------------------------------------------
def test_ambiguous_address_candidates_are_not_merged(env):
    rows = [vrow(1, "C1", hcad=TEST_HCAD_A), vrow(2, "C2", hcad=TEST_HCAD_B), vrow(3, "C3", hcad=None)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "result": {"records": rows if "1446a3ec" in str(request.url) else [],
                                                                      "total": 3}})

    client = HoustonClient(http=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)
    result = search_candidates(client, "100 test fixture")
    hcads = [c["hcad"] for c in result["candidates"]]
    assert sorted(hcads) == [TEST_HCAD_A, TEST_HCAD_B]
    assert all(c["ambiguous"] and c["shares_address_with_other_parcel"] for c in result["candidates"])
    assert result["rows_without_hcad"] == 1 and result["note"]
    assert _count(Property) == 0  # searching never imports or merges


# API contract ------------------------------------------------------------------------------------
def test_api_status_codes(fixture_property):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.app.state.run_manager.enqueue = lambda run_id: None  # keep runs queued for the conflict check
        assert client.post("/api/investigations", json={"property_id": 999}).status_code == 404
        assert client.post("/api/investigations", json={"property_id": "x"}).status_code == 422
        first = client.post("/api/investigations", json={"property_id": fixture_property["a"]})
        assert first.status_code == 202 and first.json()["status"] == "queued"
        dup = client.post("/api/investigations", json={"property_id": fixture_property["a"]})
        assert dup.status_code == 409 and dup.json()["error"]["code"] == "run_active"
        assert client.post(f"/api/investigations/{first.json()['run_id']}/resume").status_code == 409
        assert client.get("/api/investigations/12345").status_code == 404
        assert client.patch("/api/tasks/1", json={}).status_code == 422
        cases = client.get(f"/api/properties/{fixture_property['a']}/cases").json()
        notes = cases["cases"][0]["records"][0]["fields"]["notes"]
        assert "_x000d_" not in notes and "_x000d_" in json.dumps(cases["cases"][0]["records"][0]["raw"])
