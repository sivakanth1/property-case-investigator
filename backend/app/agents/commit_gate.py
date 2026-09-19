import json

from sqlalchemy import select

from ..data.repository import loads
from ..data.task_store import TaskPayload, TaskStore
from ..db import session_scope
from ..models import TaskProposal
from ..tools.proposal_tools import validate_task_payload


def commit_proposal(proposal_id: int, store: TaskStore, hcad: str, human_approved: bool = False) -> dict:
    """Deterministic gate. Model review is advisory; these checks always run before any task is written.

    Uses short transactions: the task store may talk to Supabase, and no database transaction is held across that.
    """
    with session_scope() as s:
        p = s.get(TaskProposal, proposal_id)
        case_ids, evidence_ids = loads(p.case_ids_json, []), loads(p.evidence_ids_json, [])
        issues = validate_task_payload(s, p.property_id, action_type=p.action_type, case_ids=case_ids, title=p.title,
                                       reason=p.reason, priority=p.priority, evidence_ids=evidence_ids)
        payload = TaskPayload(p.property_id, hcad, p.action_type, case_ids, p.title, p.reason, p.priority,
                              evidence_ids, run_id=p.run_id)
        if not issues and payload.task_key != p.task_key:
            issues.append("Task key does not match the proposal scope.")
        if issues:
            p.status, p.issues_json = "rejected", json.dumps(issues)
            return {"proposal_id": f"T-{p.id}", "outcome": "rejected", "issues": issues}

    if store.find_by_key(payload.task_key) is None and not human_approved:
        overlaps = store.overlapping(payload)
        if overlaps:
            issues = [f"Overlaps existing task {t['id']} (cases {', '.join(t['case_ids'])}); approve explicitly to "
                      "create a separate task." for t in overlaps]
            with session_scope() as s:
                p = s.get(TaskProposal, proposal_id)
                p.status, p.issues_json = "needs_review", json.dumps(issues)
            return {"proposal_id": f"T-{proposal_id}", "outcome": "needs_review", "issues": issues}

    task, outcome = store.upsert(payload)
    with session_scope() as s:
        p = s.get(TaskProposal, proposal_id)
        p.status, p.task_id, p.issues_json = "committed", task["id"], "[]"
    return {"proposal_id": f"T-{proposal_id}", "outcome": outcome, "task_id": task["id"], "task_status": task["status"]}


def commit_run(run_id: int, store: TaskStore, hcad: str) -> list[dict]:
    with session_scope() as s:
        ids = [p.id for p in s.scalars(select(TaskProposal).where(TaskProposal.run_id == run_id,
                                                                  TaskProposal.status == "approved")
                                       .order_by(TaskProposal.id))]
    return [commit_proposal(pid, store, hcad) for pid in ids]
