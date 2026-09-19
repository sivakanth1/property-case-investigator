import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ACTION_TYPES, FINDING_TYPES, PRIORITIES
from ..data.repository import evidence_records, loads, make_task_key, task_view
from ..models import Finding, SourceRecord, Task, TaskProposal
from ..schemas import ProposeFindingArgs, ProposeTaskArgs
from .errors import ToolError

_PRESENT_TENSE_CLAIMS = [
    r"\bis\s+(?:currently\s+|still\s+)?in\s+violation\b",
    r"\b(?:remains?|still)\s+in\s+violation\b",
    r"\bcurrently\s+(?:has|have|violat\w*|non-?compliant|unsafe|dangerous)\b",
    r"\b(?:active|ongoing|current|present|existing)\s+violations?\b",
    r"\bviolations?\s+(?:still\s+)?(?:exists?|persists?|remains?)\b",
    r"\bis\s+(?:currently\s+)?non-?compliant\b",
]
_RECURRENCE_WORDS = re.compile(r"\b(recurr\w*|repeat\w*|repeated|multiple\s+cases)\b", re.IGNORECASE)
_VERIFICATION_CONTEXT = re.compile(r"\b(whether|if|verify|confirm|determine|check|unknown|cannot|not\s+known)\b", re.IGNORECASE)


def historical_wording_issues(*texts: str) -> list[str]:
    joined = ". ".join(t or "" for t in texts)
    for pattern in _PRESENT_TENSE_CLAIMS:
        for m in re.finditer(pattern, joined, re.IGNORECASE):
            same_sentence_prefix = re.split(r"[.!?;]\s", joined[max(0, m.start() - 60):m.start()])[-1]
            if _VERIFICATION_CONTEXT.search(same_sentence_prefix):
                continue
            return [f"Claims a present condition ('{m.group(0)}'); historical records cannot establish current violations."]
    return []


def property_case_ids(s: Session, property_id: int) -> set[str]:
    return {c for c in s.scalars(select(SourceRecord.case_id).where(SourceRecord.property_id == property_id)) if c}


def evidence_issues(s: Session, property_id: int, evidence_ids: list[int]) -> tuple[list[str], list[SourceRecord]]:
    records = evidence_records(s, list(set(evidence_ids)))
    found = {r.id for r in records}
    issues = []
    missing = sorted(set(evidence_ids) - found)
    if missing:
        issues.append(f"Evidence ids do not exist: {missing}")
    foreign = sorted(r.id for r in records if r.property_id != property_id)
    if foreign:
        issues.append(f"Evidence ids belong to a different property: {foreign}")
    return issues, [r for r in records if r.property_id == property_id]


def validate_task_payload(s: Session, property_id: int, *, action_type: str, case_ids: list[str], title: str,
                          reason: str, priority: str, evidence_ids: list[int]) -> list[str]:
    issues = []
    if action_type not in ACTION_TYPES:
        issues.append(f"Unknown action_type '{action_type}'.")
    if priority not in PRIORITIES:
        issues.append(f"Invalid priority '{priority}'.")
    if not (title or "").strip() or not (reason or "").strip():
        issues.append("Title and reason are required.")
    if not case_ids:
        issues.append("At least one case_id is required.")
    if not evidence_ids:
        issues.append("At least one evidence id is required.")
    unknown_cases = sorted({c for c in case_ids} - property_case_ids(s, property_id))
    if unknown_cases:
        issues.append(f"case_ids not found in this property's records: {unknown_cases}")
    ev_issues, records = evidence_issues(s, property_id, evidence_ids)
    issues += ev_issues
    unrelated = sorted(r.id for r in records if r.case_id not in set(case_ids))
    if unrelated:
        issues.append(f"Evidence {unrelated} comes from cases not listed in case_ids.")
    issues += historical_wording_issues(title, reason)
    if _RECURRENCE_WORDS.search(f"{title} {reason}") and len(set(case_ids)) < 2:
        issues.append("Recurrence claims require at least two distinct case ids (rows within one case are one case).")
    return issues


def validate_finding_payload(s: Session, property_id: int, *, type: str, summary: str, evidence_ids: list[int],
                             uncertainty: str) -> list[str]:
    issues = []
    if type not in FINDING_TYPES:
        issues.append(f"Unknown finding type '{type}'.")
    if not (uncertainty or "").strip():
        issues.append("Uncertainty statement is required.")
    if type != "coverage_gap" and not evidence_ids:
        issues.append("At least one evidence id is required.")
    ev_issues, records = evidence_issues(s, property_id, evidence_ids)
    issues += ev_issues
    issues += historical_wording_issues(summary)
    if type == "recurrence" and len({r.case_id for r in records if r.case_id}) < 2:
        issues.append("A recurrence finding needs evidence from at least two distinct cases.")
    return issues


def _run_proposal(s: Session, model, run_id: int, proposal_id: str | None, prefix: str):
    if not proposal_id:
        return None
    obj = s.get(model, int(proposal_id.split("-")[1]))
    if obj is None or obj.run_id != run_id:
        raise ToolError(f"{proposal_id} is not a proposal from this run.")
    return obj


def propose_finding(s: Session, ctx, args: ProposeFindingArgs) -> dict:
    issues = validate_finding_payload(s, ctx.property_id, type=args.type, summary=args.summary,
                                      evidence_ids=args.evidence_ids, uncertainty=args.uncertainty)
    if issues:
        raise ToolError("Finding rejected: " + " ".join(issues))
    finding = _run_proposal(s, Finding, ctx.run_id, args.revises_proposal_id, "F")
    if finding is None:
        finding = s.scalars(select(Finding).where(Finding.run_id == ctx.run_id, Finding.type == args.type,
                                                  Finding.summary == args.summary)).first()
    if finding is None:
        finding = Finding(run_id=ctx.run_id, property_id=ctx.property_id)
        s.add(finding)
    finding.type, finding.summary, finding.uncertainty = args.type, args.summary.strip(), args.uncertainty.strip()
    finding.evidence_ids_json = json.dumps(sorted(set(args.evidence_ids)))
    finding.review_status, finding.issues_json = "draft", "[]"
    s.flush()
    return {"proposal_id": f"F-{finding.id}", "status": "draft"}


def overlapping_tasks(s: Session, property_id: int, action_type: str, case_ids: list[str]) -> list[Task]:
    wanted = set(case_ids)
    out = []
    for t in s.scalars(select(Task).where(Task.property_id == property_id, Task.action_type == action_type)):
        existing = set(loads(t.case_ids_json, []))
        if existing != wanted and existing & wanted:
            out.append(t)
    return out


def propose_task(s: Session, ctx, args: ProposeTaskArgs) -> dict:
    case_ids = sorted({c.strip() for c in args.case_ids})
    issues = validate_task_payload(s, ctx.property_id, action_type=args.action_type, case_ids=case_ids,
                                   title=args.title, reason=args.reason, priority=args.priority,
                                   evidence_ids=args.evidence_ids)
    if issues:
        raise ToolError("Task rejected: " + " ".join(issues))
    key = make_task_key(ctx.property_id, args.action_type, case_ids)
    proposal = _run_proposal(s, TaskProposal, ctx.run_id, args.revises_proposal_id, "T")
    if proposal is None:
        proposal = s.scalars(select(TaskProposal).where(TaskProposal.run_id == ctx.run_id,
                                                        TaskProposal.task_key == key)).first()
    if proposal is None:
        proposal = TaskProposal(run_id=ctx.run_id, property_id=ctx.property_id)
        s.add(proposal)
    proposal.task_key, proposal.action_type = key, args.action_type
    proposal.case_ids_json = json.dumps(case_ids)
    proposal.title, proposal.reason, proposal.priority = args.title.strip(), args.reason.strip(), args.priority
    proposal.evidence_ids_json = json.dumps(sorted(set(args.evidence_ids)))
    proposal.status, proposal.issues_json = "draft", "[]"
    s.flush()

    existing = s.scalars(select(Task).where(Task.task_key == key)).first()
    overlaps = overlapping_tasks(s, ctx.property_id, args.action_type, case_ids)
    result = {"proposal_id": f"T-{proposal.id}", "task_key": key, "status": "draft"}
    if existing:
        result["existing_task"] = {"task_id": existing.id, "status": existing.status}
        result["note"] = "Same scope as an existing task: committing only refreshes its evidence; status is preserved."
    if overlaps:
        result["overlapping_tasks"] = [{"task_id": t.id, "case_ids": task_view(s, t)["case_ids"], "status": t.status}
                                       for t in overlaps]
        result["note"] = "Overlaps existing work; this proposal will be held for human review instead of duplicating."
    return result
