from fastapi import APIRouter, Request, Response

from ..agents.case_planner import AccountPlanStore, CaseClosed, LocalPlanStore, PlanStore, generate_plan
from ..agents.llm import ModelError
from ..data.repository import get_property
from ..data.supabase_admin import SupabaseError
from ..db import session_scope
from ..schemas import PlanRequest, StepUpdate
from .auth import optional_user
from .errors import api_error, not_found

router = APIRouter(prefix="/api", tags=["case plans"])


def plan_store(request: Request, property_id: int) -> PlanStore:
    """Signed-in users get their own Supabase-backed plans; guests share this computer's local plans."""
    with session_scope() as s:
        prop = get_property(s, property_id)
        if prop is None:
            raise not_found("Property")
        hcad, address = prop.hcad, prop.address
    user = optional_user(request)
    admin = getattr(request.app.state, "supabase", None)
    if user is None or admin is None:
        return LocalPlanStore(property_id)
    return AccountPlanStore(admin, user["id"], property_id, hcad, address)


@router.get("/properties/{property_id}/cases/{case_id}/plan")
def get_plan(property_id: int, case_id: str, request: Request):
    try:
        plan = plan_store(request, property_id).get_active(case_id)
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", str(exc))
    if plan is None:
        raise not_found("Resolution plan")
    return plan


@router.post("/properties/{property_id}/cases/{case_id}/plan")
def create_plan(property_id: int, case_id: str, request: Request, response: Response, body: PlanRequest | None = None):
    """Returns the saved plan if one exists; generates and saves one only the first time (or on explicit regenerate)."""
    store = plan_store(request, property_id)
    try:
        plan, created = generate_plan(store, property_id, case_id, regenerate=bool(body and body.regenerate))
    except LookupError as exc:
        raise not_found("Case" if str(exc) == "case" else "Property")
    except CaseClosed as exc:
        raise api_error(409, "case_closed", str(exc))
    except ModelError as exc:
        raise api_error(502, "model_error", f"Featherless model call failed: {exc} Nothing was saved; try again.")
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", f"Could not save the steps to your account: {exc}")
    response.status_code = 201 if created else 200
    return plan


@router.patch("/properties/{property_id}/plan-steps/{step_id}")
def update_step(property_id: int, step_id: str, body: StepUpdate, request: Request):
    try:
        return plan_store(request, property_id).set_step(step_id, body.status == "completed")
    except LookupError:
        raise not_found("Step")
    except SupabaseError as exc:
        raise api_error(502, "accounts_backend_error", str(exc))
