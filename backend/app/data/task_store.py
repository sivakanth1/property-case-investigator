"""Where committed verification tasks and their feedback live.

Guests use the backend's SQLite file (this computer only); signed-in accounts use Supabase, so an account sees the
same tasks and statuses on any device.
"""
import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db import iso, session_scope, utcnow
from ..models import Feedback, SourceRecord, Task
from .repository import loads
from .supabase_admin import Conflict, SupabaseAdmin


def make_task_key(hcad: str, action_type: str, case_ids: list[str]) -> str:
    """Stable identity for a task: property + action + the exact set of cases it covers."""
    return f"{hcad}:{action_type}:{'|'.join(sorted({c.strip() for c in case_ids}))}"


@dataclass(frozen=True)
class TaskPayload:
    property_id: int
    hcad: str
    action_type: str
    case_ids: list[str]
    title: str
    reason: str
    priority: str
    evidence_ids: list[int]
    run_id: int | None = None

    @property
    def task_key(self) -> str:
        return make_task_key(self.hcad, self.action_type, self.case_ids)


def evidence_index() -> tuple[dict, dict]:
    """local evidence id -> stable {resource_id,row_id} reference, and the reverse."""
    with session_scope() as s:
        recs = s.scalars(select(SourceRecord))
        to_ref = {r.id: {"resource_id": r.resource_id, "row_id": r.source_row_id} for r in recs}
    return to_ref, {(v["resource_id"], v["row_id"]): k for k, v in to_ref.items()}


class LocalTaskStore:
    storage = "local"

    def _view(self, s, task: Task) -> dict:
        feedback = s.scalars(select(Feedback).where(Feedback.task_id == task.id).order_by(Feedback.id.desc()))
        return {
            "id": str(task.id), "property_id": task.property_id, "property_hcad": None, "task_key": task.task_key,
            "action_type": task.action_type, "case_ids": loads(task.case_ids_json, []), "title": task.title,
            "reason": task.reason, "status": task.status, "priority": task.priority,
            "evidence_ids": loads(task.evidence_ids_json, []), "last_run_id": task.last_run_id,
            "created_at": iso(task.created_at), "updated_at": iso(task.updated_at), "storage": self.storage,
            "feedback": [{"id": str(f.id), "action": f.action, "note": f.note, "created_at": iso(f.created_at)}
                         for f in feedback],
        }

    def list_tasks(self, property_id: int | None = None, hcad: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(Task)
            if property_id is not None:
                stmt = stmt.where(Task.property_id == property_id)
            return [self._view(s, t) for t in s.scalars(stmt.order_by(Task.property_id, Task.id))]

    def get_task(self, task_id: str) -> dict | None:
        with session_scope() as s:
            task = s.get(Task, int(task_id)) if task_id.isdigit() else None
            return self._view(s, task) if task else None

    def open_counts(self) -> dict[int, int]:
        with session_scope() as s:
            rows = s.execute(select(Task.property_id, func.count()).where(
                Task.status.in_(("open", "in_progress"))).group_by(Task.property_id))
            return {pid: n for pid, n in rows}

    def find_by_key(self, task_key: str) -> dict | None:
        with session_scope() as s:
            task = s.scalars(select(Task).where(Task.task_key == task_key)).first()
            return self._view(s, task) if task else None

    def overlapping(self, payload: TaskPayload) -> list[dict]:
        wanted = set(payload.case_ids)
        with session_scope() as s:
            out = []
            for t in s.scalars(select(Task).where(Task.property_id == payload.property_id,
                                                  Task.action_type == payload.action_type)):
                existing = set(loads(t.case_ids_json, []))
                if existing != wanted and existing & wanted:
                    out.append(self._view(s, t))
            return out

    def upsert(self, payload: TaskPayload) -> tuple[dict, str]:
        """Creates the task, or merges evidence into the existing one without touching its status."""
        key, now = payload.task_key, utcnow()
        with session_scope() as s:
            inserted = s.execute(sqlite_insert(Task).values(
                property_id=payload.property_id, task_key=key, action_type=payload.action_type,
                case_ids_json=json.dumps(payload.case_ids), title=payload.title, reason=payload.reason, status="open",
                priority=payload.priority, evidence_ids_json=json.dumps(sorted(set(payload.evidence_ids))),
                last_run_id=payload.run_id, created_at=now, updated_at=now
            ).on_conflict_do_nothing(index_elements=["task_key"])).rowcount
            task = s.scalars(select(Task).where(Task.task_key == key)).first()
            if not inserted:
                task.evidence_ids_json = json.dumps(sorted(set(loads(task.evidence_ids_json, [])) | set(payload.evidence_ids)))
                task.last_run_id, task.updated_at = payload.run_id, now
            return self._view(s, task), "created" if inserted else "refreshed"

    def update(self, task_id: str, action: str, status: str | None, note: str | None) -> dict:
        with session_scope() as s:
            task = s.get(Task, int(task_id)) if task_id.isdigit() else None
            if task is None:
                raise LookupError("task")
            if status:
                task.status = status
            task.updated_at = utcnow()
            s.add(Feedback(task_id=task.id, action=action, note=note))
            s.flush()
            return self._view(s, task)


class AccountTaskStore:
    storage = "account"

    def __init__(self, admin: SupabaseAdmin, user_id: str):
        self.admin, self.user_id = admin, user_id
        self._index: tuple[dict, dict] | None = None

    def _maps(self) -> tuple[dict, dict]:
        if self._index is None:
            self._index = evidence_index()
        return self._index

    def _ids(self, refs) -> list[int]:
        back = self._maps()[1]
        return sorted(back[key] for r in refs or [] if isinstance(r, dict)
                      and (key := (r.get("resource_id"), str(r.get("row_id")))) in back)

    def _refs(self, ids: list[int]) -> list[dict]:
        to_ref = self._maps()[0]
        return [to_ref[i] for i in sorted(set(ids)) if i in to_ref]

    def _view(self, row: dict, property_id: int | None = None) -> dict:
        feedback = sorted(row.get("investigation_task_feedback") or [], key=lambda f: f["created_at"], reverse=True)
        return {
            "id": str(row["id"]), "property_id": property_id, "property_hcad": row["property_hcad"],
            "task_key": row["task_key"], "action_type": row["action_type"], "case_ids": row["case_ids"] or [],
            "title": row["title"], "reason": row["reason"], "status": row["status"], "priority": row["priority"],
            "evidence_ids": self._ids(row.get("evidence_refs")), "last_run_id": row.get("last_run_id"),
            "created_at": row.get("created_at"), "updated_at": row.get("updated_at"), "storage": self.storage,
            "feedback": [{"id": str(f["id"]), "action": f["action"], "note": f.get("note"),
                          "created_at": f["created_at"]} for f in feedback],
        }

    def list_tasks(self, property_id: int | None = None, hcad: str | None = None) -> list[dict]:
        return [self._view(r, property_id) for r in self.admin.list_tasks(self.user_id, hcad=hcad)]

    def get_task(self, task_id: str) -> dict | None:
        rows = self.admin.list_tasks(self.user_id, task_id=task_id)
        return self._view(rows[0]) if rows else None

    def open_counts_by_hcad(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.admin.list_tasks(self.user_id):
            if row["status"] in ("open", "in_progress"):
                counts[row["property_hcad"]] = counts.get(row["property_hcad"], 0) + 1
        return counts

    def find_by_key(self, task_key: str) -> dict | None:
        rows = self.admin.list_tasks(self.user_id, task_key=task_key)
        return self._view(rows[0]) if rows else None

    def overlapping(self, payload: TaskPayload) -> list[dict]:
        wanted = set(payload.case_ids)
        out = []
        for row in self.admin.list_tasks(self.user_id, hcad=payload.hcad, action_type=payload.action_type):
            existing = set(row["case_ids"] or [])
            if existing != wanted and existing & wanted:
                out.append(self._view(row, payload.property_id))
        return out

    def upsert(self, payload: TaskPayload) -> tuple[dict, str]:
        existing = self.find_by_key(payload.task_key)
        if existing is None:
            try:
                row = self.admin.insert_task({
                    "user_id": self.user_id, "property_hcad": payload.hcad, "task_key": payload.task_key,
                    "action_type": payload.action_type, "case_ids": payload.case_ids, "title": payload.title,
                    "reason": payload.reason, "status": "open", "priority": payload.priority,
                    "evidence_refs": self._refs(payload.evidence_ids), "last_run_id": str(payload.run_id or "") or None})
                return self._view(row, payload.property_id), "created"
            except Conflict:  # another request created it first
                existing = self.find_by_key(payload.task_key)
        merged = sorted(set(existing["evidence_ids"]) | set(payload.evidence_ids))
        rows = self.admin.patch_task(self.user_id, existing["id"], {
            "evidence_refs": self._refs(merged), "last_run_id": str(payload.run_id or "") or None})
        return self._view(rows[0] if rows else existing, payload.property_id), "refreshed"

    def update(self, task_id: str, action: str, status: str | None, note: str | None) -> dict:
        rows = self.admin.patch_task(self.user_id, task_id, {"status": status} if status else {})
        if not rows:
            raise LookupError("task")
        self.admin.insert_task_feedback({"task_id": task_id, "user_id": self.user_id, "action": action, "note": note})
        return self.get_task(task_id)


TaskStore = LocalTaskStore | AccountTaskStore


def task_store_for(user: dict | None, admin: SupabaseAdmin | None) -> TaskStore:
    return AccountTaskStore(admin, user["id"]) if user and admin else LocalTaskStore()
