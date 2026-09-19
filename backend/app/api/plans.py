from fastapi import APIRouter, Response

from ..agents.case_planner import CaseClosed, active_plan, generate_plan, plan_view, update_step
from ..agents.llm import ModelError
from ..db import session_scope
from ..schemas import PlanRequest, StepUpdate
from .errors import api_error, not_found

router = APIRouter(prefix="/api", tags=["case plans"])


@router.get("/properties/{property_id}/cases/{case_id}/plan")
def get_plan(property_id: int, case_id: str):
    with session_scope() as s:
        plan = active_plan(s, property_id, case_id)
        if plan is None:
            raise not_found("Resolution plan")
        return plan_view(s, plan)


@router.post("/properties/{property_id}/cases/{case_id}/plan")
def create_plan(property_id: int, case_id: str, response: Response, body: PlanRequest | None = None):
    """Returns the saved plan if one exists; generates and saves one only the first time (or on explicit regenerate)."""
    try:
        plan, created = generate_plan(property_id, case_id, regenerate=bool(body and body.regenerate))
    except LookupError as exc:
        raise not_found("Case" if str(exc) == "case" else "Property")
    except CaseClosed as exc:
        raise api_error(409, "case_closed", str(exc))
    except ModelError as exc:
        raise api_error(502, "model_error", f"Featherless model call failed: {exc} Nothing was saved; try again.")
    response.status_code = 201 if created else 200
    return plan


@router.patch("/plan-steps/{step_id}")
def patch_step(step_id: int, body: StepUpdate):
    try:
        return update_step(step_id, body.done)
    except LookupError:
        raise not_found("Step")
