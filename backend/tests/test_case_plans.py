import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.agents.case_planner import CaseClosed, generate_plan, update_step
from app.agents.llm import ModelError
from app.data.repository import apply_fetched
from app.db import dispose_engine, init_engine, session_scope
from app.models import CasePlan, SourceRecord

from conftest import payload, prow, vrow

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


def _plans(**where):
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(CasePlan).filter_by(**where)) or 0


def fake_client(*contents):
    replies = iter(contents)

    def create(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(replies), tool_calls=None))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_plan_is_saved_once_and_reused(open_case_property):
    plan, created = generate_plan(open_case_property, "OPEN1")
    assert created and plan["mode"] == "deterministic_demo" and plan["summary"].startswith("Deterministic demo checklist")
    titles = [s["title"] for s in plan["steps"]]
    assert titles[0].startswith("Inspect the property") and titles[-1].startswith("Document the fix")
    assert "Remove or enclose the vehicle" in titles and "Clear weeds, grass and brush" in titles
    assert any(s["ordinance"] == "10-451" for s in plan["steps"])

    again, created_again = generate_plan(open_case_property, "OPEN1")
    assert not created_again and again["id"] == plan["id"]
    assert [s["id"] for s in again["steps"]] == [s["id"] for s in plan["steps"]]
    assert _plans(case_id="OPEN1") == 1


def test_closed_case_cannot_get_steps(open_case_property):
    with pytest.raises(CaseClosed):
        generate_plan(open_case_property, "DONE1")
    assert _plans() == 0


def test_step_progress_persists_after_restart(open_case_property):
    from fastapi.testclient import TestClient

    from app.main import app

    plan, _ = generate_plan(open_case_property, "OPEN1")
    update_step(plan["steps"][0]["id"], True)
    dispose_engine()
    init_engine(os.environ["DATABASE_URL"])
    with TestClient(app) as client:
        cases = {c["case_id"]: c for c in client.get(f"/api/properties/{open_case_property}/cases").json()["cases"]}
        assert cases["OPEN1"]["is_open"] and not cases["DONE1"]["is_open"]
        saved = cases["OPEN1"]["plan"]
        assert saved["id"] == plan["id"] and saved["progress"]["done"] == 1 and saved["steps"][0]["done"]
        assert cases["DONE1"]["plan"] is None
        assert client.post(f"/api/properties/{open_case_property}/cases/DONE1/plan").status_code == 409
        assert client.post(f"/api/properties/{open_case_property}/cases/NOPE/plan").status_code == 404
        assert client.patch("/api/plan-steps/99999", json={"done": True}).status_code == 404


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
    plan, created = generate_plan(open_case_property, "OPEN1", llm_factory=lambda settings: fake_client(content))
    assert created and plan["mode"] == "live_model" and plan["model"] == "Qwen/Qwen3-32B"
    steps = plan["steps"]
    assert steps[0]["title"].startswith("Inspect the property")  # verification step enforced first
    assert steps[1]["ordinance"] == "10-451" and steps[1]["evidence_ids"] == [own[1]]  # foreign id dropped
    assert steps[2]["ordinance"] is None and steps[2]["evidence_ids"]  # invented ordinance removed
    assert "currently in violation" not in plan["summary"]


def test_unusable_model_output_saves_nothing(open_case_property, monkeypatch):
    from fastapi.testclient import TestClient

    from app.agents import case_planner
    from app.main import app

    monkeypatch.setenv("FEATHERLESS_API_KEY", "test-key")
    with pytest.raises(ModelError):
        generate_plan(open_case_property, "OPEN1", llm_factory=lambda settings: fake_client("sorry", "still no"))
    monkeypatch.setattr(case_planner, "make_llm_client", lambda settings: fake_client("nope", "nope"))
    with TestClient(app) as client:
        r = client.post(f"/api/properties/{open_case_property}/cases/OPEN1/plan")
    assert r.status_code == 502 and r.json()["error"]["code"] == "model_error"
    assert _plans() == 0


def test_regenerate_supersedes_the_saved_plan(open_case_property):
    first, _ = generate_plan(open_case_property, "OPEN1")
    second, created = generate_plan(open_case_property, "OPEN1", regenerate=True)
    assert created and second["id"] != first["id"]
    assert _plans(status="active") == 1 and _plans(status="superseded") == 1
