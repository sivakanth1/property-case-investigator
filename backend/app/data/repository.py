import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import (
    CKAN_API_BASE, DATASET_URL, PROJECTS_RESOURCE_ID, RESOURCE_LABELS, RUN_ACTIVE_STATUSES, VIOLATIONS_RESOURCE_ID,
    get_settings,
)
from ..db import iso, session_scope, utcnow
from ..models import Feedback, Finding, InvestigationRun, Property, SourceRecord, SourceSnapshot, Task, TaskProposal
from .houston_client import HoustonClient, SourceUnavailable
from .normalization import case_id_of, display_address, normalize_address, normalized_fields, text_or_none

RESOURCES = (VIOLATIONS_RESOURCE_ID, PROJECTS_RESOURCE_ID)


class DataBlocker(Exception):
    """No real source data is available for the request."""


def loads(value: str | None, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def make_task_key(property_id: int, action_type: str, case_ids: list[str]) -> str:
    return f"p{property_id}:{action_type}:{'|'.join(sorted({c.strip() for c in case_ids}))}"


def source_url(resource_id: str, filters: dict) -> str:
    return str(httpx.URL(CKAN_API_BASE + "datastore_search", params={"resource_id": resource_id, "filters": json.dumps(filters)}))


# ---------------------------------------------------------------- properties

def get_property(s: Session, property_id: int) -> Property | None:
    return s.get(Property, property_id)


def list_properties(s: Session, query: str | None = None) -> list[Property]:
    stmt = select(Property)
    if query and query.strip():
        needle = normalize_address(query)
        stmt = stmt.where(or_(Property.normalized_address.contains(needle), Property.hcad.contains(query.strip())))
    return list(s.scalars(stmt.order_by(Property.is_demo.desc(), Property.id)))


def active_run(s: Session, property_id: int) -> InvestigationRun | None:
    return s.scalars(select(InvestigationRun).where(
        InvestigationRun.property_id == property_id, InvestigationRun.status.in_(RUN_ACTIVE_STATUSES))).first()


def latest_run(s: Session, property_id: int) -> InvestigationRun | None:
    return s.scalars(select(InvestigationRun).where(InvestigationRun.property_id == property_id)
                     .order_by(InvestigationRun.id.desc())).first()


def coverage(s: Session, prop: Property) -> dict:
    snaps = list(s.scalars(select(SourceSnapshot).where(SourceSnapshot.property_id == prop.id)
                           .order_by(SourceSnapshot.fetched_at.desc(), SourceSnapshot.id.desc())))
    good = next((x for x in snaps if not x.error), None)
    last_attempt = snaps[0] if snaps else None
    row_count = s.scalar(select(func.count()).select_from(SourceRecord).where(SourceRecord.property_id == prop.id)) or 0
    if not row_count:
        cache_status = "no_data"
    elif last_attempt and last_attempt.error:
        cache_status = "cached_after_failed_refresh"
    else:
        cache_status = "cached"
    origin = good.origin if good else None
    fetched = iso(good.fetched_at) if good else None
    max_age = timedelta(minutes=get_settings().live_refresh_minutes)
    recent_failure = bool(last_attempt and last_attempt.error and utcnow() - last_attempt.fetched_at < timedelta(minutes=5))
    refresh_due = not recent_failure and (good is None or origin != "live_api" or utcnow() - good.fetched_at > max_age)
    if origin == "snapshot_file":
        label = f"Saved real snapshot retrieved {fetched[:10]} from the Houston CKAN API"
    elif origin == "live_api":
        label = f"Fetched from the Houston CKAN API on {fetched[:10]}; served from local cache"
    else:
        label = "No source records available"
    return {
        "row_count": row_count,
        "observed_min_date": good.observed_min_date if good else None,
        "observed_max_date": good.observed_max_date if good else None,
        "fetched_at": fetched,
        "origin": origin,
        "complete": bool(good and good.complete),
        "cache_status": cache_status,
        "refresh_due": refresh_due,
        "label": label,
        "last_error": last_attempt.error if last_attempt and last_attempt.error else None,
        "last_attempt_at": iso(last_attempt.fetched_at) if last_attempt else None,
        "resource_totals": loads(good.resource_totals_json, {}) if good else {},
        "dataset_url": DATASET_URL,
        "api_query_url": source_url(VIOLATIONS_RESOURCE_ID, {"HCAD": prop.hcad}),
        "historical_note": "Historical code-enforcement records. They do not establish current property conditions, "
                           "and missing records do not prove a property is problem-free.",
    }


def property_view(s: Session, prop: Property) -> dict:
    run = latest_run(s, prop.id)
    cases = {r.case_id for r in s.scalars(select(SourceRecord).where(SourceRecord.property_id == prop.id))}
    open_tasks = s.scalar(select(func.count()).select_from(Task).where(
        Task.property_id == prop.id, Task.status.in_(("open", "in_progress")))) or 0
    return {
        "id": prop.id, "hcad": prop.hcad, "address": prop.address, "zip": prop.zip, "is_demo": prop.is_demo,
        "case_count": len(cases - {None}), "open_task_count": open_tasks,
        "coverage": coverage(s, prop),
        "latest_run": {"id": run.id, "status": run.status, "mode": run.mode} if run else None,
        "active_run_id": run.id if run and run.status in RUN_ACTIVE_STATUSES else None,
    }


# ---------------------------------------------------------------- ingestion

def fetch_property_rows(client: HoustonClient, hcad: str, cap: int) -> dict:
    resources = {}
    for rid in RESOURCES:
        records, total, complete = client.fetch_all(rid, {"HCAD": hcad}, cap)
        resources[rid] = {"records": records, "total": total, "complete": complete}
    return {"hcad": hcad, "retrieved_at": iso(utcnow()), "resources": resources}


def _most_common(values: list[str | None]) -> str | None:
    values = [v for v in values if v]
    return Counter(values).most_common(1)[0][0] if values else None


def apply_fetched(s: Session, payload: dict, origin: str, is_demo: bool = False) -> Property:
    hcad = payload["hcad"]
    fetched_at = datetime.fromisoformat(payload["retrieved_at"].replace("Z", "+00:00"))
    all_rows = [(rid, raw) for rid, res in payload["resources"].items() for raw in res["records"]
                if text_or_none(raw.get("HCAD")) == hcad]
    if not all_rows:
        raise DataBlocker(f"No Houston source records were found for HCAD {hcad}.")

    address = _most_common([display_address(raw.get("Merged_Situs")) for _, raw in all_rows]) or f"HCAD {hcad}"
    zip_code = _most_common([text_or_none(raw.get("Zip") or raw.get("ZipCode")) for _, raw in all_rows])
    prop = s.scalars(select(Property).where(Property.hcad == hcad)).first()
    if prop is None:
        prop = Property(hcad=hcad, address=address, normalized_address=normalize_address(address), zip=zip_code, is_demo=is_demo)
        s.add(prop)
        s.flush()
    elif is_demo and not prop.is_demo:
        prop.is_demo = True

    existing = {(r.resource_id, r.source_row_id): r for r in s.scalars(
        select(SourceRecord).where(SourceRecord.property_id == prop.id))}
    for rid, raw in all_rows:
        row_id = str(raw.get("_id"))
        fields = normalized_fields(rid, raw)
        rec = existing.get((rid, row_id))
        if rec is None:
            rec = s.scalars(select(SourceRecord).where(SourceRecord.resource_id == rid,
                                                       SourceRecord.source_row_id == row_id)).first()
        if rec is not None and rec.property_id != prop.id:
            continue  # row already bound to another property; never merge
        if rec is None:
            rec = SourceRecord(resource_id=rid, source_row_id=row_id, property_id=prop.id)
            s.add(rec)
        rec.case_id = case_id_of(rid, raw)
        rec.violation_id = fields.get("violation_id")
        rec.raw_json = json.dumps(raw, sort_keys=True)
        rec.fetched_at = fetched_at
    s.flush()

    dates = [normalized_fields(r.resource_id, json.loads(r.raw_json))["created_date"]
             for r in s.scalars(select(SourceRecord).where(SourceRecord.property_id == prop.id))]
    dates = sorted(d for d in dates if d)
    totals = {rid: {"total": res["total"], "retrieved": len(res["records"]), "complete": res["complete"]}
              for rid, res in payload["resources"].items()}
    s.add(SourceSnapshot(
        property_id=prop.id, fetched_at=fetched_at, origin=origin,
        complete=all(res["complete"] for res in payload["resources"].values()),
        row_count=len(all_rows), observed_min_date=dates[0] if dates else None,
        observed_max_date=dates[-1] if dates else None, resource_totals_json=json.dumps(totals)))
    return prop


def import_property(client: HoustonClient, hcad: str, cap: int) -> int:
    hcad = hcad.strip()
    payload = fetch_property_rows(client, hcad, cap)  # network outside any transaction
    with session_scope() as s:
        return apply_fetched(s, payload, origin="live_api").id


def refresh_property(client: HoustonClient, property_id: int, cap: int) -> dict:
    with session_scope() as s:
        prop = get_property(s, property_id)
        if prop is None:
            raise LookupError("property")
        hcad = prop.hcad
    try:
        payload = fetch_property_rows(client, hcad, cap)
    except SourceUnavailable as exc:
        with session_scope() as s:
            s.add(SourceSnapshot(property_id=property_id, fetched_at=utcnow(), origin="live_api", complete=False,
                                 row_count=0, error=f"Refresh failed: {exc}"))
            has_cache = (s.scalar(select(func.count()).select_from(SourceRecord)
                                  .where(SourceRecord.property_id == property_id)) or 0) > 0
        if not has_cache:
            raise DataBlocker(f"Houston API unavailable and no cached records exist: {exc}") from exc
        return {"status": "cached_fallback", "message": f"Houston API unavailable ({exc}). Showing cached real records."}
    with session_scope() as s:
        apply_fetched(s, payload, origin="live_api")
    return {"status": "refreshed", "message": "Records refreshed from the Houston CKAN API."}


def load_snapshot_file(path: Path) -> int:
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    with session_scope() as s:
        for payload in data.get("properties", []):
            if s.scalars(select(Property).where(Property.hcad == payload["hcad"])).first():
                continue
            apply_fetched(s, payload, origin="snapshot_file", is_demo=True)
            count += 1
    return count


def search_candidates(client: HoustonClient, query: str) -> dict:
    query = query.strip()
    rows: list[tuple[str, dict]] = []
    if query.isdigit() and len(query) >= 6:
        for rid in RESOURCES:
            rows += [(rid, r) for r in client.search(rid, filters={"HCAD": query}, limit=100)[0]]
    else:
        needle = normalize_address(query)
        for rid in RESOURCES:
            rows += [(rid, r) for r in client.search(rid, q=query, limit=100)[0]
                     if needle in normalize_address(r.get("Merged_Situs"))]
    grouped: dict[str, dict] = {}
    without_hcad = 0
    for rid, raw in rows:
        hcad = text_or_none(raw.get("HCAD"))
        if not hcad:
            without_hcad += 1
            continue
        c = grouped.setdefault(hcad, {"hcad": hcad, "addresses": set(), "zips": set(), "case_ids": set(), "matched_rows": 0})
        c["addresses"].add(display_address(raw.get("Merged_Situs")))
        if text_or_none(raw.get("Zip") or raw.get("ZipCode")):
            c["zips"].add(text_or_none(raw.get("Zip") or raw.get("ZipCode")))
        if case_id_of(rid, raw):
            c["case_ids"].add(case_id_of(rid, raw))
        c["matched_rows"] += 1

    with session_scope() as s:
        imported = {p.hcad: p.id for p in s.scalars(select(Property).where(Property.hcad.in_(list(grouped))))}
    by_address: dict[str, set] = defaultdict(set)
    for c in grouped.values():
        for a in c["addresses"]:
            by_address[normalize_address(a)].add(c["hcad"])
    candidates = []
    for c in sorted(grouped.values(), key=lambda x: -x["matched_rows"]):
        shared = any(len(by_address[normalize_address(a)]) > 1 for a in c["addresses"])
        candidates.append({
            "hcad": c["hcad"], "addresses": sorted(c["addresses"]), "zips": sorted(c["zips"]),
            "matched_rows": c["matched_rows"], "case_count_in_sample": len(c["case_ids"]),
            "imported_property_id": imported.get(c["hcad"]),
            "ambiguous": len(grouped) > 1, "shares_address_with_other_parcel": shared,
        })
    return {
        "query": query, "candidates": candidates[:25], "rows_without_hcad": without_hcad,
        "note": "Candidates are grouped by HCAD and never merged. Choose the exact parcel to import."
        if len(candidates) > 1 else None,
    }


# ---------------------------------------------------------------- cases & evidence

def record_view(rec: SourceRecord) -> dict:
    raw = loads(rec.raw_json, {})
    return {
        "evidence_id": rec.id, "resource_id": rec.resource_id, "resource_label": RESOURCE_LABELS.get(rec.resource_id, rec.resource_id),
        "source_row_id": rec.source_row_id, "case_id": rec.case_id, "violation_id": rec.violation_id,
        "fetched_at": iso(rec.fetched_at), "fields": normalized_fields(rec.resource_id, raw), "raw": raw,
        "source_url": source_url(rec.resource_id, {"_id": int(rec.source_row_id)} if rec.source_row_id.isdigit() else {}),
    }


def property_records(s: Session, property_id: int) -> list[SourceRecord]:
    return list(s.scalars(select(SourceRecord).where(SourceRecord.property_id == property_id).order_by(SourceRecord.id)))


def grouped_cases(s: Session, property_id: int) -> list[dict]:
    groups: dict[str | None, list[SourceRecord]] = defaultdict(list)
    for rec in property_records(s, property_id):
        groups[rec.case_id].append(rec)
    cases = []
    for case_id, recs in groups.items():
        views = [record_view(r) for r in recs]
        vrows = [v for v in views if v["resource_id"] == VIOLATIONS_RESOURCE_ID]
        prows = [v for v in views if v["resource_id"] == PROJECTS_RESOURCE_ID]
        dates = sorted(d for v in views if (d := v["fields"]["created_date"]))
        cases.append({
            "case_id": case_id,
            "created_date": dates[0] if dates else None,
            "latest_record_date": dates[-1] if dates else None,
            "source_statuses": sorted({v["fields"]["source_status"] for v in views if v["fields"]["source_status"]}),
            "categories": sorted({v["fields"]["category"] for v in vrows if v["fields"].get("category")}),
            "violation_row_count": len(vrows),
            "project_row_count": len(prows),
            "service_request_ids": sorted({v["fields"]["service_request_id"] for v in views if v["fields"]["service_request_id"]}),
            "evidence_ids": [v["evidence_id"] for v in views],
            "records": views,
        })
    cases.sort(key=lambda c: (c["created_date"] or "", c["case_id"] or ""), reverse=True)
    return cases


def category_recurrence(cases: list[dict]) -> list[dict]:
    by_cat: dict[str, set] = defaultdict(set)
    for c in cases:
        for cat in c["categories"]:
            if c["case_id"]:
                by_cat[cat].add(c["case_id"])
    return sorted(({"category": k, "distinct_cases": len(v), "case_ids": sorted(v)} for k, v in by_cat.items()),
                  key=lambda x: (-x["distinct_cases"], x["category"]))


def evidence_records(s: Session, ids: list[int]) -> list[SourceRecord]:
    if not ids:
        return []
    return list(s.scalars(select(SourceRecord).where(SourceRecord.id.in_(ids))))


# ---------------------------------------------------------------- tasks, findings, runs

def feedback_views(s: Session, task_id: int) -> list[dict]:
    rows = s.scalars(select(Feedback).where(Feedback.task_id == task_id).order_by(Feedback.id.desc()))
    return [{"id": f.id, "action": f.action, "note": f.note, "created_at": iso(f.created_at)} for f in rows]


def task_view(s: Session, t: Task) -> dict:
    return {
        "id": t.id, "property_id": t.property_id, "task_key": t.task_key, "action_type": t.action_type,
        "case_ids": loads(t.case_ids_json, []), "title": t.title, "reason": t.reason, "status": t.status,
        "priority": t.priority, "evidence_ids": loads(t.evidence_ids_json, []), "last_run_id": t.last_run_id,
        "created_at": iso(t.created_at), "updated_at": iso(t.updated_at), "feedback": feedback_views(s, t.id),
    }


def list_tasks(s: Session, property_id: int | None = None) -> list[Task]:
    stmt = select(Task)
    if property_id is not None:
        stmt = stmt.where(Task.property_id == property_id)
    return list(s.scalars(stmt.order_by(Task.property_id, Task.id)))


def finding_view(f: Finding) -> dict:
    return {"id": f.id, "proposal_id": f"F-{f.id}", "run_id": f.run_id, "type": f.type, "summary": f.summary,
            "evidence_ids": loads(f.evidence_ids_json, []), "uncertainty": f.uncertainty,
            "review_status": f.review_status, "issues": loads(f.issues_json, [])}


def proposal_view(p: TaskProposal) -> dict:
    return {"id": p.id, "proposal_id": f"T-{p.id}", "run_id": p.run_id, "task_key": p.task_key,
            "action_type": p.action_type, "case_ids": loads(p.case_ids_json, []), "title": p.title, "reason": p.reason,
            "priority": p.priority, "evidence_ids": loads(p.evidence_ids_json, []), "status": p.status,
            "issues": loads(p.issues_json, []), "task_id": p.task_id}


def property_memory(s: Session, property_id: int, exclude_run_id: int | None = None) -> dict:
    tasks = [task_view(s, t) for t in list_tasks(s, property_id)]
    prev_run = s.scalars(select(InvestigationRun).where(
        InvestigationRun.property_id == property_id, InvestigationRun.status.in_(("completed", "partial")),
        InvestigationRun.id != (exclude_run_id or -1)).order_by(InvestigationRun.id.desc())).first()
    prev_findings = []
    if prev_run:
        prev_findings = [finding_view(f) for f in s.scalars(select(Finding).where(
            Finding.run_id == prev_run.id, Finding.review_status == "approved"))][:10]
    return {"tasks": tasks, "previous_findings": prev_findings,
            "previous_run": {"id": prev_run.id, "status": prev_run.status, "summary": prev_run.summary} if prev_run else None}
