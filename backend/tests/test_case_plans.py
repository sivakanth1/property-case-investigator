import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agents.case_planner import CaseClosed, LocalPlanStore, generate_plan
from app.agents.llm import ModelError
from app.data.repository import apply_fetched
from app.db import dispose_engine, init_engine, session_scope
from app.models import CasePlan, SourceRecord

from conftest import FakeSupabase, payload, prow, vrow

OPEN_HCAD = "9990000000005"  # TEST FIXTURE parcel with one open and one closed case


@pytest.fixture
def open_case_property(env):
    with session_scope() as s:
        p = apply_fetched(s, payload(OPEN_HCAD, [
            vrow(21, "OPEN1", hcad=OPEN_HCAD, status="OPEN"),
            vrow(22, "OPEN1", hcad=OPEN_HCAD, status="OPEN", category="DON - 3 - Junked Motor Vehicle"),
            vrow(23, "DONE1", hcad=OPEN_HCAD, status="CLOSED"),
        ], [prow(21, "OPEN1", hcad=OPEN_HCAD, status="OPEN")]), origin="snapshot_file")
        return p.id


@pytest.fixture
def client(open_case_property):
    from app.api import auth as auth_api
    from app.main import app

    for email in ("a@example.com", "b@example.com"):
        auth_api.throttle.reset(email)
    fake = FakeSupabase()
    with TestClient(app) as c:
        c.app.state.supabase = fake
        c.fake, c.pid = fake, open_case_property
        yield c


def _local_plans(**where):
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(CasePlan).filter_by(**where)) or 0


def fake_llm(*contents, calls=None):
    replies = iter(contents)

    def create(**kw):
        if calls is not None:
            calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies), tool_calls=None))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def account(c, email, password="correct horse 1"):
    c.post("/api/auth/signup", json={"email": email, "password": password})
    token = c.post("/api/auth/signin", json={"email": email, "password": password}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def cases(c, headers=None):
    body = c.get(f"/api/properties/{c.pid}/cases", headers=headers or {}).json()
    return body, {x["case_id"]: x for x in body["cases"]}


# ------------------------------------------------------------------ local store (guests on this computer)

def test_plan_is_saved_once_and_reused(open_case_property):
    store = LocalPlanStore(open_case_property)
    plan, created = generate_plan(store, open_case_property, "OPEN1")
    assert created and plan["storage"] == "local" and plan["mode"] == "deterministic_demo"
    titles = [s["title"] for s in plan["steps"]]
    assert titles[0].startswith("Inspect the property") and titles[-1].startswith("Document the fix")
    assert "Remove or enclose the vehicle" in titles and "Clear weeds, grass and brush" in titles
    assert any(s["ordinance"] == "10-451" for s in plan["steps"])
    assert all(s["status"] == "pending" for s in plan["steps"])

    again, created_again = generate_plan(store, open_case_property, "OPEN1")
    assert not created_again and again["id"] == plan["id"]
    assert [s["id"] for s in again["steps"]] == [s["id"] for s in plan["steps"]]
    assert _local_plans(case_id="OPEN1") == 1


def test_closed_case_cannot_get_steps(open_case_property):
    with pytest.raises(CaseClosed):
        generate_plan(LocalPlanStore(open_case_property), open_case_property, "DONE1")
    assert _local_plans() == 0


def test_local_step_status_persists_after_restart(client):
    plan = client.post(f"/api/properties/{client.pid}/cases/OPEN1/plan").json()
    step = plan["steps"][0]["id"]
    done = client.patch(f"/api/properties/{client.pid}/plan-steps/{step}", json={"status": "completed"}).json()
    assert done["steps"][0]["status"] == "completed" and done["progress"]["done"] == 1
    dispose_engine()
    init_engine(os.environ["DATABASE_URL"])
    body, by_case = cases(client)
    assert body["plans_storage"] == "local" and by_case["OPEN1"]["is_open"] and not by_case["DONE1"]["is_open"]
    assert by_case["OPEN1"]["plan"]["id"] == plan["id"] and by_case["OPEN1"]["plan"]["steps"][0]["status"] == "completed"
    assert by_case["DONE1"]["plan"] is None
    assert client.post(f"/api/properties/{client.pid}/cases/DONE1/plan").status_code == 409
    assert client.post(f"/api/properties/{client.pid}/cases/NOPE/plan").status_code == 404
    assert client.patch(f"/api/properties/{client.pid}/plan-steps/99999", json={"status": "completed"}).status_code == 404
    assert client.patch(f"/api/properties/{client.pid}/plan-steps/{step}", json={"status": "done"}).status_code == 422


def test_live_model_plan_is_validated_against_the_case_records(open_case_property, monkeypatch):
    monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key")
    with session_scope() as s:
        own = sorted(r.id for r in s.scalars(select(SourceRecord).where(SourceRecord.case_id == "OPEN1")))
    content = "<think>plan</think>" + json.dumps({"summary": "The lot is currently in violation.", "steps": [
        {"title": "Tow the junked vehicle", "detail": "Call a licensed tow company.", "ordinance": "10-451*",
         "evidence_ids": [own[1], 99999]},
        {"title": "Cut the weeds", "detail": "Hire a crew.", "ordinance": "99-999", "evidence_ids": []},
        {"title": "Request re-inspection", "detail": "Call Houston 311.", "ordinance": None, "evidence_ids": [own[0]]},
    ]})
    plan, created = generate_plan(LocalPlanStore(open_case_property), open_case_property, "OPEN1",
                                  llm_factory=lambda settings: fake_llm(content))
    assert created and plan["mode"] == "live_model" and plan["model"] == "Qwen/Qwen3-32B"
    steps = plan["steps"]
    assert steps[0]["title"].startswith("Inspect the property")  # verification step enforced first
    assert steps[1]["ordinance"] == "10-451" and steps[1]["evidence_ids"] == [own[1]]  # foreign id dropped
    assert steps[2]["ordinance"] is None and steps[2]["evidence_ids"]  # invented ordinance removed
    assert "currently in violation" not in plan["summary"]


def test_unusable_model_output_saves_nothing(client, monkeypatch):
    from app.agents import case_planner

    monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key")
    with pytest.raises(ModelError):
        generate_plan(LocalPlanStore(client.pid), client.pid, "OPEN1", llm_factory=lambda settings: fake_llm("sorry", "no"))
    monkeypatch.setattr(case_planner, "make_llm_client", lambda settings: fake_llm("nope", "nope"))
    r = client.post(f"/api/properties/{client.pid}/cases/OPEN1/plan")
    assert r.status_code == 502 and r.json()["error"]["code"] == "model_error"
    assert _local_plans() == 0


def test_regenerate_supersedes_the_saved_plan(open_case_property):
    store = LocalPlanStore(open_case_property)
    first, _ = generate_plan(store, open_case_property, "OPEN1")
    second, created = generate_plan(store, open_case_property, "OPEN1", regenerate=True)
    assert created and second["id"] != first["id"]
    assert _local_plans(status="active") == 1 and _local_plans(status="superseded") == 1


# ------------------------------------------------------------------ account store (Supabase, any device)

def test_account_plans_and_step_status_follow_the_user_to_any_device(client):
    device1 = account(client, "a@example.com")
    device2 = {"Authorization": "Bearer " + client.post(
        "/api/auth/signin", json={"email": "a@example.com", "password": "correct horse 1"}).json()["token"]}
    other = account(client, "b@example.com")

    created = client.post(f"/api/properties/{client.pid}/cases/OPEN1/plan", headers=device1)
    assert created.status_code == 201
    plan = created.json()
    assert plan["storage"] == "account" and len(client.fake.plans) == 1
    saved = client.fake.plans[0]
    assert saved["user_id"] == "user-1" and saved["hcad"] == OPEN_HCAD and saved["case_id"] == "OPEN1"
    assert all(s["user_id"] == "user-1" and s["status"] == "pending" for s in client.fake.steps)
    assert all(r.keys() == {"resource_id", "row_id"} for s in client.fake.steps for r in s["evidence_refs"])

    step = plan["steps"][1]["id"]
    done = client.patch(f"/api/properties/{client.pid}/plan-steps/{step}", json={"status": "completed"}, headers=device1)
    assert done.status_code == 200 and done.json()["progress"]["done"] == 1
    assert next(s for s in client.fake.steps if s["id"] == step)["status"] == "completed"

    body, by_case = cases(client, device2)  # same account, another device
    same = by_case["OPEN1"]["plan"]
    assert body["plans_storage"] == "account" and same["id"] == plan["id"]
    assert [s["status"] for s in same["steps"]][:2] == ["pending", "completed"]
    assert same["steps"][0]["evidence_ids"] == plan["steps"][0]["evidence_ids"] and same["steps"][0]["evidence_ids"]
    assert client.post(f"/api/properties/{client.pid}/cases/OPEN1/plan", headers=device2).json()["id"] == plan["id"]

    assert cases(client, other)[1]["OPEN1"]["plan"] is None  # another account has its own list
    assert cases(client)[1]["OPEN1"]["plan"] is None  # guests use this computer's local list
    denied = client.patch(f"/api/properties/{client.pid}/plan-steps/{step}", json={"status": "pending"}, headers=other)
    assert denied.status_code == 404
    expired = client.get(f"/api/properties/{client.pid}/cases", headers={"Authorization": "Bearer stale"})
    assert expired.status_code == 401


def test_saved_ai_plan_is_reused_for_another_account_without_a_new_ai_call(client, monkeypatch):
    from app.agents import case_planner

    monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key")
    calls = []
    content = json.dumps({"summary": "Checklist for case OPEN1.", "steps": [
        {"title": "Inspect the lot", "detail": "Confirm the conditions.", "ordinance": None, "evidence_ids": []},
        {"title": "Tow the vehicle", "detail": "Licensed tow.", "ordinance": None, "evidence_ids": []}]})
    monkeypatch.setattr(case_planner, "make_llm_client", lambda settings: fake_llm(content, calls=calls))

    a, b = account(client, "a@example.com"), account(client, "b@example.com")
    first = client.post(f"/api/properties/{client.pid}/cases/OPEN1/plan", headers=a).json()
    client.patch(f"/api/properties/{client.pid}/plan-steps/{first['steps'][0]['id']}", json={"status": "completed"}, headers=a)
    second = client.post(f"/api/properties/{client.pid}/cases/OPEN1/plan", headers=b)
    assert second.status_code == 201 and len(calls) == 1
    reused = second.json()
    assert reused["id"] != first["id"] and reused["mode"] == "live_model"
    assert [s["title"] for s in reused["steps"]] == [s["title"] for s in first["steps"]]
    assert all(s["status"] == "pending" for s in reused["steps"])


def test_failed_step_save_leaves_no_half_saved_plan(client):
    headers = account(client, "a@example.com")
    client.fake.fail_step_insert = True
    r = client.post(f"/api/properties/{client.pid}/cases/OPEN1/plan", headers=headers)
    assert r.status_code == 502 and r.json()["error"]["code"] == "accounts_backend_error"
    assert client.fake.plans == [] and client.fake.steps == []
