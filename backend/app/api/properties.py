from fastapi import APIRouter, Query, Request

from ..agents.case_planner import case_is_open, plans_by_case
from ..config import get_settings
from ..data.houston_client import SourceUnavailable
from ..data.repository import (
    DataBlocker, category_recurrence, coverage, evidence_records, get_property, grouped_cases, import_property,
    list_properties, property_view, record_view, refresh_property, search_candidates,
)
from ..db import session_scope
from ..schemas import ImportRequest
from .errors import api_error, not_found

router = APIRouter(prefix="/api", tags=["properties"])


@router.get("/properties")
def properties(query: str | None = Query(default=None, max_length=120)):
    with session_scope() as s:
        return [property_view(s, p) for p in list_properties(s, query)]


@router.get("/properties/{property_id}")
def property_detail(property_id: int):
    with session_scope() as s:
        prop = get_property(s, property_id)
        if prop is None:
            raise not_found("Property")
        return property_view(s, prop)


@router.get("/properties/{property_id}/cases")
def property_cases(property_id: int):
    with session_scope() as s:
        prop = get_property(s, property_id)
        if prop is None:
            raise not_found("Property")
        cases = grouped_cases(s, property_id)
        plans = plans_by_case(s, property_id)
        for c in cases:
            c["is_open"] = bool(c["case_id"]) and case_is_open(c)
            c["plan"] = plans.get(c["case_id"])
        return {"property_id": property_id, "coverage": coverage(s, prop), "cases": cases,
                "category_recurrence": category_recurrence(cases)}


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
