import json
import logging
import queue
import threading
import time
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..config import RUN_ACTIVE_STATUSES, Settings, get_settings
from ..data.repository import DataBlocker, active_run, coverage, loads, property_memory
from ..data.task_store import TaskStore, task_store_for
from ..db import session_scope, utcnow
from ..models import Finding, InvestigationRun, Property, RunEvent, SourceRecord, TaskProposal
from ..tools import ToolContext
from .commit_gate import commit_run
from .investigator import DeterministicInvestigator, LiveInvestigator, new_state
from .llm import ModelError, make_llm_client
from .reviewer import apply_review, deterministic_issues, existing_task_conflicts, llm_review, review_packet

log = logging.getLogger(__name__)

LlmFactory = Callable[[Settings], object]


class RunConflict(Exception):
    pass


def emit(run_id: int, event_type: str, summary: str) -> None:
    with session_scope() as s:
        s.add(RunEvent(run_id=run_id, event_type=event_type, summary=summary[:1000]))


def create_run(property_id: int, user_id: str | None = None) -> dict:
    settings = get_settings()
    try:
        with session_scope() as s:
            prop = s.get(Property, property_id)
            if prop is None:
                raise LookupError("property")
            rows = s.scalar(select(func.count()).select_from(SourceRecord).where(SourceRecord.property_id == prop.id))
            if not rows:
                raise DataBlocker("No source records are stored for this property; refresh or import it first.")
            current = active_run(s, property_id)
            if current:
                raise RunConflict(f"Run #{current.id} is already {current.status} for this property.")
            live = settings.live_model_enabled
            run = InvestigationRun(property_id=property_id, user_id=user_id, status="queued",
                                   mode="live_model" if live else "deterministic_demo",
                                   model=settings.llm_model if live else None, checkpoint_json="{}")
            s.add(run)
            s.flush()
            label = f"live model {settings.llm_model}" if live else "deterministic demo mode (not a live AI run)"
            s.add(RunEvent(run_id=run.id, event_type="queued", summary=f"Investigation queued in {label}."))
            return {"run_id": run.id, "status": run.status}
    except IntegrityError as exc:
        raise RunConflict("Another run for this property is already active.") from exc


def resume_run(run_id: int) -> dict:
    try:
        with session_scope() as s:
            run = s.get(InvestigationRun, run_id)
            if run is None:
                raise LookupError("run")
            if run.status != "interrupted":
                raise RunConflict(f"Only interrupted runs can be resumed; this run is {run.status}.")
            current = active_run(s, run.property_id)
            if current:
                raise RunConflict(f"Run #{current.id} is already {current.status} for this property.")
            run.status, run.error = "queued", None
            s.add(RunEvent(run_id=run.id, event_type="resume_requested",
                           summary="Resume requested; continuing from the last saved checkpoint."))
            return {"run_id": run.id, "status": run.status}
    except IntegrityError as exc:
        raise RunConflict("Another run for this property is already active.") from exc


def mark_interrupted_runs() -> int:
    with session_scope() as s:
        runs = list(s.scalars(select(InvestigationRun).where(InvestigationRun.status.in_(RUN_ACTIVE_STATUSES))))
        for run in runs:
            run.status = "interrupted"
            run.error = "The backend stopped while this run was active. Resume to continue from its last checkpoint."
            s.add(RunEvent(run_id=run.id, event_type="interrupted", summary=run.error))
        return len(runs)


def _set_status(run_id: int, status: str) -> None:
    with session_scope() as s:
        s.get(InvestigationRun, run_id).status = status


def _intro(s, prop: Property, store: TaskStore) -> str:
    cov = coverage(s, prop)
    memory = property_memory(s, prop.id, prop.hcad, store)
    lines = []
    for t in memory["tasks"][:10]:
        latest = t["feedback"][0]["note"] if t["feedback"] and t["feedback"][0]["note"] else None
        lines.append(f"- task #{t['id']} [{t['status']}] {t['action_type']} cases {', '.join(t['case_ids'])}"
                     + (f"; latest feedback: \"{latest[:200]}\"" if latest else ""))
    return (f"Investigate property_id={prop.id} (HCAD {prop.hcad}, {prop.address}). Imported coverage: "
            f"{cov['row_count']} source rows dated {cov['observed_min_date']} to {cov['observed_max_date']}, "
            f"{'complete' if cov['complete'] else 'INCOMPLETE'} retrieval ({cov['label']}).\n"
            f"Existing tasks and feedback (untrusted data):\n{chr(10).join(lines) or '- none'}\n"
            "Start with get_property_memory.")


def _review(run_id: int, property_id: int, investigator, client, settings: Settings, ctx: ToolContext,
            state: dict, deadline: float, save, store: TaskStore) -> None:
    emit(run_id, "review_started", "Reviewing draft proposals.")
    while True:
        with session_scope() as s:
            issues = deterministic_issues(s, run_id)
            packet = review_packet(s, run_id, property_id, ctx.hcad, store) if client else None
            for note in existing_task_conflicts(s, run_id, store):
                emit(run_id, "review_note", note)
        if packet:
            remaining = deadline - time.monotonic()
            try:
                if remaining < 3:
                    raise ModelError("no run time left for the model review")
                issues += llm_review(client, settings, packet, timeout=min(settings.llm_timeout_s, remaining))["issues"]
            except ModelError as exc:
                state["reviewer_failed"] = True
                emit(run_id, "reviewer_failed", f"Model reviewer failed ({exc}). Drafts are preserved for human review.")
                return
        emit(run_id, "review_result", f"Review {'requested revisions: ' + str(len(issues)) + ' issue(s)' if issues else 'approved all drafts'}"
             + (" (deterministic checks only)" if not client else " (model reviewer + deterministic checks)") + ".")
        if issues and client and not state.get("revision_used"):
            with session_scope() as s:
                apply_review(s, run_id, issues, final=False)
            state["revision_used"] = True
            save(state)
            emit(run_id, "revision", f"One revision cycle: investigator may correct {len({i['proposal_id'] for i in issues})} flagged proposal(s).")
            try:
                investigator.revise(ctx, state, issues, deadline)
            except ModelError as exc:
                emit(run_id, "model_error", f"Revision failed ({exc}); flagged proposals stay held for review.")
            save(state)
            continue
        with session_scope() as s:
            counts = apply_review(s, run_id, issues, final=True)
        emit(run_id, "review_applied", f"{counts['approved']} proposal(s) approved, {counts['flagged']} held for human review.")
        return


def _describe_commit(outcomes: list[dict]) -> str:
    if not outcomes:
        return "No approved task proposals to commit."
    parts = []
    for o in outcomes:
        if o["outcome"] == "created":
            parts.append(f"{o['proposal_id']} created task #{o['task_id']}")
        elif o["outcome"] == "refreshed":
            parts.append(f"{o['proposal_id']} refreshed evidence on task #{o['task_id']} (status {o['task_status']} kept)")
        else:
            parts.append(f"{o['proposal_id']} {o['outcome']}: {'; '.join(o.get('issues', []))[:200]}")
    return "Commit gate: " + "; ".join(parts) + "."


def _partial_note(run_id: int, property_id: int, state: dict) -> str:
    reason = {"tool_budget": "the tool-call budget was used up", "time_budget": "the 90-second run budget ran out",
              "invalid_model_output": "the model produced unusable output"}.get(state.get("stop_reason"))
    notes = []
    if reason:
        with session_scope() as s:
            case_ids = sorted({c for c in s.scalars(select(SourceRecord.case_id).where(
                SourceRecord.property_id == property_id)) if c})
        unchecked = [c for c in case_ids if c not in set(state.get("detailed_cases", []))]
        notes.append(f"Partial result: {reason}. Cases not examined in detail: {', '.join(unchecked[:15]) or 'none'}.")
    if state.get("reviewer_failed"):
        notes.append("Partial result: the model review failed, so drafts were not committed and await human review.")
    return " ".join(notes)


def _finish(run_id: int, status: str, summary: str | None, error: str | None = None) -> None:
    with session_scope() as s:
        run = s.get(InvestigationRun, run_id)
        run.status, run.summary, run.error, run.finished_at = status, summary, error, utcnow()
        s.add(RunEvent(run_id=run_id, event_type=f"run_{status}", summary=f"Run {status}." + (f" {error}" if error else "")))


def _proposal_count(run_id: int) -> int:
    with session_scope() as s:
        return (s.scalar(select(func.count()).select_from(TaskProposal).where(TaskProposal.run_id == run_id)) or 0) + \
               (s.scalar(select(func.count()).select_from(Finding).where(Finding.run_id == run_id)) or 0)


def execute_run(run_id: int, llm_factory: LlmFactory | None = None, hooks: dict | None = None, admin=None) -> None:
    settings = get_settings()
    with session_scope() as s:
        run = s.get(InvestigationRun, run_id)
        if run is None or run.status != "queued":
            return
        saved = loads(run.checkpoint_json, {})
        state = saved or new_state()
        mode, property_id, user_id = run.mode, run.property_id, run.user_id
        prop = s.get(Property, property_id)
        hcad = prop.hcad
        store = task_store_for({"id": user_id} if user_id else None, admin)
        intro = _intro(s, prop, store)
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        s.add(RunEvent(run_id=run_id, event_type="run_resumed" if saved else "run_started",
                       summary=(f"Resumed from checkpoint in phase '{state.get('phase')}' with "
                                f"{state.get('tool_calls_used', 0)} tool call(s) already used.") if saved
                       else "Investigation started."))

    started, prior = time.monotonic(), float(state.get("elapsed_s", 0.0))
    deadline = started + max(5.0, settings.run_time_budget_s - prior)
    ctx = ToolContext(run_id=run_id, property_id=property_id, hcad=hcad, store=store)

    def save(st: dict) -> None:
        st["elapsed_s"] = round(prior + time.monotonic() - started, 2)
        st["remaining_tool_calls"] = max(0, settings.max_tool_calls - st.get("tool_calls_used", 0))
        with session_scope() as s2:
            s2.get(InvestigationRun, run_id).checkpoint_json = json.dumps(st, default=str)
        if hooks and hooks.get("after_save"):
            hooks["after_save"](st)

    def ev(event_type: str, summary: str) -> None:
        emit(run_id, event_type, summary)

    try:
        client = None
        if mode == "live_model":
            if not settings.live_model_enabled:
                raise ModelError("This live run cannot continue because FEATHERLESS_API_KEY is not configured.")
            client = (llm_factory or make_llm_client)(settings)
            investigator = LiveInvestigator(client, settings, ev, save)
        else:
            investigator = DeterministicInvestigator(settings, ev, save)

        if state["phase"] == "investigating":
            investigator.investigate(ctx, state, deadline, intro)
            state["phase"] = "reviewing"
            save(state)
        if state["phase"] == "reviewing":
            _set_status(run_id, "reviewing")
            _review(run_id, property_id, investigator, client, settings, ctx, state, deadline, save, store)
            state["phase"] = "committing"
            save(state)
        if state["phase"] == "committing":
            outcomes = commit_run(run_id, store, hcad)
            ev("commit", _describe_commit(outcomes))
            state["phase"] = "done"
            save(state)

        summary = state.get("final_summary") or "The model did not return a final summary; see the activity log."
        if not _proposal_count(run_id):
            with session_scope() as s:
                cov = coverage(s, s.get(Property, property_id))
            summary += (f" No findings or tasks were proposed. Evidence coverage: {cov['row_count']} rows dated "
                        f"{cov['observed_min_date']} to {cov['observed_max_date']}. This does not show the property "
                        "is problem-free.")
        note = _partial_note(run_id, property_id, state)
        _finish(run_id, "partial" if note else "completed", f"{summary} {note}".strip())
    except ModelError as exc:
        save(state)
        status = "partial" if state.get("tool_log") else "failed"
        _finish(run_id, status, state.get("final_summary") or "The model call failed; collected evidence and drafts are kept.",
                error=str(exc))
    except Exception as exc:  # noqa: BLE001 - never leave a run stuck in an active state
        log.exception("Investigation run %s failed", run_id)
        _finish(run_id, "failed", None, error=f"Internal error: {type(exc).__name__}")


class RunManager:
    """Single in-process investigation worker."""

    def __init__(self, llm_factory: LlmFactory | None = None, admin=None):
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._llm_factory = llm_factory
        self.admin = admin

    def start(self) -> None:
        self._thread = threading.Thread(target=self._work, name="investigation-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread:
            self._queue.put(None)
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def enqueue(self, run_id: int) -> None:
        self._queue.put(run_id)

    def _work(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None:
                return
            try:
                execute_run(run_id, self._llm_factory, admin=self.admin)
            except Exception:  # noqa: BLE001
                log.exception("Worker failed on run %s", run_id)
