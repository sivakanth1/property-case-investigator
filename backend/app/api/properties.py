from fastapi import APIRouter, Query, Request

from ..agents.case_planner import case_is_open
from ..config import get_settings
from ..data.supabase_admin import SupabaseError
from ..data.houston_client import SourceUnavailable
from ..data.repository import (
    DataBlocker, category_recurrence, coverage, evidence_records, get_property, grouped_cases, import_property,
    list_properties, property_view, record_view, refresh_property, search_candidates,
)
from ..db import session_scope
from ..schemas import ImportRequest
from .errors import api_error, not_found
from .plans import plan_store
from .tasks import store_for

router = APIRouter(prefix="/api", tags=["properties"])


@router.get("/properties")
def properties(request: Request, query: str | None = Query(default=None, max_length=120)):
    store = store_for(request)
    try:
        counts = store.open_counts() if store.storage == "local" else store.open_counts_by_hcad()
    except SupabaseError:
        counts = {}
    with session_scope() as s:
        views = [property_view(s, p) for p in list_properties(s, query)]
    for v in views:
        v["open_task_count"] = counts.get(v["id"] if store.storage == "local" else v["hcad"], 0)
    return views


@router.get("/properties/{property_id}")
def property_detail(property_id: int, request: Request):
    with session_scope() as s:
        prop = get_property(s, property_id)
        if prop is None:
            raise not_found("Property")
        view = property_view(s, prop)
    try:
        store = store_for(request)
        counts = store.open_counts() if store.storage == "local" else store.open_counts_by_hcad()
        view["open_task_count"] = counts.get(view["id"] if store.storage == "local" else view["hcad"], 0)
    except SupabaseError:
        pass
    return view


@router.get("/properties/{property_id}/cases")
def property_cases(property_id: int, request: Request):
    store = plan_store(request, property_id)
    try:
        plans, plans_error = store.list_active(), None
    except SupabaseError as exc:
        plans, plans_error = {}, f"Could not load your saved resolution steps: {exc}"
    with session_scope() as s:
        prop = get_property(s, property_id)
        cases = grouped_cases(s, property_id)
        for c in cases:
            c["is_open"] = bool(c["case_id"]) and case_is_open(c)
            c["plan"] = plans.get(c["case_id"])
        return {"property_id": property_id, "coverage": coverage(s, prop), "cases": cases,
                "category_recurrence": category_recurrence(cases), "plans_storage": store.storage,
                "plans_error": plans_error}


@router.post("/properties/{property_id}/refresh")
def refresh(property_id: int, request: Request):
    try:
        result = refresh_property(request.app.state.houston, property_id, get_settings().max_rows_per_property)
    except LookupError:
        raise not_found("Property")
    except DataBlocker as exc:
        raise api_error(503, "data_blocker", str(exc))
    with session_scope() as s:
        result["property"] = property_view(s, get_property(s, property_id))
    return result


@router.get("/source/candidates")
def candidates(request: Request, query: str = Query(min_length=3, max_length=120)):
    try:
        return search_candidates(request.app.state.houston, query)
    except SourceUnavailable as exc:
        raise api_error(503, "source_unavailable", f"Houston API unavailable: {exc}")


@router.post("/properties/import", status_code=201)
def import_hcad(body: ImportRequest, request: Request):
    try:
        property_id = import_property(request.app.state.houston, body.hcad, get_settings().max_rows_per_property)
    except SourceUnavailable as exc:
        raise api_error(503, "source_unavailable", f"Houston API unavailable: {exc}")
    except DataBlocker as exc:
        raise api_error(404, "no_source_records", str(exc))
    with session_scope() as s:
        return property_view(s, get_property(s, property_id))


@router.get("/evidence")
def evidence(ids: str = Query(min_length=1, max_length=2000)):
    try:
        wanted = [int(x) for x in ids.split(",") if x.strip()][:100]
    except ValueError:
        raise api_error(422, "invalid_ids", "ids must be a comma-separated list of integers.")
    with session_scope() as s:
        return [record_view(r) for r in evidence_records(s, wanted)]
