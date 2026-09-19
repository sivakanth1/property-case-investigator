import json

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..data.repository import loads, make_task_key
from ..db import utcnow
from ..models import Task, TaskProposal
from ..tools.proposal_tools import overlapping_tasks, validate_task_payload


def commit_proposal(s: Session, p: TaskProposal, human_approved: bool = False) -> dict:
    """Deterministic gate. Model review is advisory; these checks always run before any task write."""
    case_ids = loads(p.case_ids_json, [])
    evidence_ids = loads(p.evidence_ids_json, [])
    issues = validate_task_payload(s, p.property_id, action_type=p.action_type, case_ids=case_ids, title=p.title,
                                   reason=p.reason, priority=p.priority, evidence_ids=evidence_ids)
    key = make_task_key(p.property_id, p.action_type, case_ids) if not issues else None
    if key and key != p.task_key:
        issues.append("Task key does not match the proposal scope.")
    if issues:
        p.status, p.issues_json = "rejected", json.dumps(issues)
        return {"proposal_id": f"T-{p.id}", "outcome": "rejected", "issues": issues}

    existing = s.scalars(select(Task).where(Task.task_key == key)).first()
    if existing is None:
        overlaps = overlapping_tasks(s, p.property_id, p.action_type, case_ids)
        if overlaps and not human_approved:
            issues = [f"Overlaps existing task #{t.id} (cases {', '.join(loads(t.case_ids_json, []))}); approve explicitly "
                      "to create a separate task." for t in overlaps]
            p.status, p.issues_json = "needs_review", json.dumps(issues)
            return {"proposal_id": f"T-{p.id}", "outcome": "needs_review", "issues": issues}
        now = utcnow()
        inserted = s.execute(sqlite_insert(Task).values(
            property_id=p.property_id, task_key=key, action_type=p.action_type, case_ids_json=json.dumps(case_ids),
            title=p.title, reason=p.reason, status="open", priority=p.priority,
            evidence_ids_json=json.dumps(sorted(set(evidence_ids))), last_run_id=p.run_id, created_at=now,
            updated_at=now).on_conflict_do_nothing(index_elements=["task_key"])).rowcount
        existing = s.scalars(select(Task).where(Task.task_key == key)).first()
        if inserted:
            p.status, p.task_id, p.issues_json = "committed", existing.id, "[]"
            return {"proposal_id": f"T-{p.id}", "outcome": "created", "task_id": existing.id}

    merged = sorted(set(loads(existing.evidence_ids_json, [])) | set(evidence_ids))
    existing.evidence_ids_json = json.dumps(merged)
    existing.last_run_id = p.run_id
    existing.updated_at = utcnow()
    p.status, p.task_id, p.issues_json = "committed", existing.id, "[]"
    return {"proposal_id": f"T-{p.id}", "outcome": "refreshed", "task_id": existing.id, "task_status": existing.status}


def commit_run(s: Session, run_id: int) -> list[dict]:
    proposals = s.scalars(select(TaskProposal).where(TaskProposal.run_id == run_id, TaskProposal.status == "approved")
                          .order_by(TaskProposal.id))
    return [commit_proposal(s, p) for p in proposals]
