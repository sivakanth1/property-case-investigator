"""Investigation tasks are saved per account (Supabase), so status and feedback follow the user to any device."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agents.runner import create_run, execute_run
from app.db import session_scope
from app.models import InvestigationRun, Task

from conftest import TEST_HCAD_A, FakeSupabase


@pytest.fixture
def client(fixture_property):
    from app.api import auth as auth_api
    from app.main import app

    for email in ("a@example.com", "b@example.com"):
        auth_api.throttle.reset(email)
    fake = FakeSupabase()
    with TestClient(app) as c:
        c.app.state.supabase = fake
        c.app.state.run_manager.admin = fake
        c.fake, c.pid = fake, fixture_property["a"]
        yield c


def account(c, email, password="correct horse 1"):
    c.post("/api/auth/signup", json={"email": email, "password": password})
    token = c.post("/api/auth/signin", json={"email": email, "password": password}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def investigate(c, headers):
    """Runs the investigation synchronously as the signed-in account."""
    c.app.state.run_manager.enqueue = lambda run_id: None
    run_id = c.post("/api/investigations", json={"property_id": c.pid}, headers=headers).json()["run_id"]
    execute_run(run_id, admin=c.fake)
    return run_id


def test_investigation_tasks_are_saved_to_the_account_and_sync_across_devices(client):
    device1 = account(client, "a@example.com")
    other = account(client, "b@example.com")

    run_id = investigate(client, device1)
    with session_scope() as s:
        assert s.get(InvestigationRun, run_id).status == "completed"
        assert s.get(InvestigationRun, run_id).user_id == "user-1"
        assert (s.scalar(select(func.count()).select_from(Task)) or 0) == 0  # nothing in the local file

    saved = client.fake.tasks
    assert saved and all(t["user_id"] == "user-1" and t["property_hcad"] == TEST_HCAD_A for t in saved)
    assert all(t["status"] == "open" and t["evidence_refs"] for t in saved)

    mine = client.get("/api/tasks", headers=device1).json()
    assert len(mine) == len(saved) and all(t["storage"] == "account" for t in mine)
    assert mine[0]["evidence_ids"], "stable evidence references map back to local source rows"
    assert client.get("/api/tasks", headers=other).json() == []  # another account
    assert client.get("/api/tasks").json() == []  # guest on this computer

    task_id = mine[0]["id"]
    done = client.patch(f"/api/tasks/{task_id}", headers=device1,
                        json={"status": "verified", "note": "Current inspection completed; no further action needed."})
    assert done.status_code == 200 and done.json()["status"] == "verified"
    assert done.json()["feedback"][0]["note"].startswith("Current inspection completed")

    device2 = {"Authorization": "Bearer " + client.post(
        "/api/auth/signin", json={"email": "a@example.com", "password": "correct horse 1"}).json()["token"]}
    from_other_device = {t["id"]: t for t in client.get("/api/tasks", headers=device2).json()}
    assert from_other_device[task_id]["status"] == "verified"
    assert from_other_device[task_id]["feedback"][0]["action"] == "verified"

    assert client.patch(f"/api/tasks/{task_id}", headers=other, json={"status": "open"}).status_code == 404
    counts = {p["id"]: p["open_task_count"] for p in client.get("/api/properties", headers=device1).json()}
    assert counts[client.pid] == len(saved) - 1  # the verified one no longer counts as open


def test_rerun_respects_account_feedback_and_creates_no_duplicates(client):
    headers = account(client, "a@example.com")
    investigate(client, headers)
    tasks = client.get("/api/tasks", headers=headers).json()
    verify = next(t for t in tasks if t["action_type"] == "verify_current_condition")
    client.patch(f"/api/tasks/{verify['id']}", headers=headers, json={"status": "verified", "note": "Inspected."})

    rerun = investigate(client, headers)
    after = client.get("/api/tasks", headers=headers).json()
    assert len(after) == len(tasks) and len(client.fake.tasks) == len(tasks)
    assert next(t for t in after if t["id"] == verify["id"])["status"] == "verified"
    with session_scope() as s:
        summary = s.get(InvestigationRun, rerun).summary
    assert "Respected prior feedback" in summary and "verified" in summary


def test_guest_runs_stay_on_this_computer(client):
    run_id = create_run(client.pid)["run_id"]
    execute_run(run_id, admin=client.fake)
    with session_scope() as s:
        assert s.get(InvestigationRun, run_id).user_id is None
        assert (s.scalar(select(func.count()).select_from(Task)) or 0) > 0
    assert client.fake.tasks == []
    guest_tasks = client.get("/api/tasks").json()
    assert guest_tasks and all(t["storage"] == "local" for t in guest_tasks)
    assert client.get("/api/tasks", headers=account(client, "a@example.com")).json() == []
