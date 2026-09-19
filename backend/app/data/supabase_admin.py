"""Server-side access to Supabase tables with the service-role key (bypasses RLS; never exposed to the browser)."""
import uuid
from datetime import datetime, timezone

import httpx

PROFILE_COLUMNS = "id,email,full_name,company,password_hash"
SAVED_COLUMNS = "id,user_id,hcad,address,zip,created_at"
PLAN_COLUMNS = "id,user_id,hcad,case_id,address,status,mode,model,summary,source_status,created_at,updated_at"
STEP_COLUMNS = "id,plan_id,position,title,detail,ordinance,evidence_refs,status,completed_at,updated_at"
PLAN_WITH_STEPS = f"{PLAN_COLUMNS},case_plan_steps({STEP_COLUMNS})"
TASK_COLUMNS = ("id,user_id,property_hcad,task_key,action_type,case_ids,title,reason,status,priority,evidence_refs,"
                "last_run_id,created_at,updated_at")
FEEDBACK_COLUMNS = "id,task_id,action,note,created_at"
TASK_WITH_FEEDBACK = f"{TASK_COLUMNS},investigation_task_feedback({FEEDBACK_COLUMNS})"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseError(Exception):
    pass


class Conflict(SupabaseError):
    """A unique constraint rejected the write (duplicate row)."""


class EmailTaken(Conflict):
    pass


class SupabaseAdmin:
    def __init__(self, url: str, service_key: str, http: httpx.Client | None = None):
        self._rest = f"{url.rstrip('/')}/rest/v1"
        headers = {"apikey": service_key, "Content-Type": "application/json"}
        if service_key.startswith("eyJ"):  # legacy JWT service_role key; new sb_secret_ keys go in apikey only
            headers["Authorization"] = f"Bearer {service_key}"
        self._http = http or httpx.Client(timeout=15)
        self._headers = headers

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, table: str, params: dict, json=None, prefer: str | None = None) -> list[dict]:
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        try:
            resp = self._http.request(method, f"{self._rest}/{table}", params=params, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise SupabaseError(f"Could not reach Supabase ({type(exc).__name__}).") from exc
        if resp.status_code == 409 or (resp.status_code >= 400 and '"23505"' in resp.text):
            if table == "profiles":
                raise EmailTaken("An account with this email already exists.")
            raise Conflict(f"Duplicate row in {table}.")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("message", "")
            except ValueError:
                detail = resp.text[:200]
            raise SupabaseError(f"Supabase returned HTTP {resp.status_code}: {detail[:200]}")
        return resp.json() if resp.content else []

    # ------------------------------------------------------------------ profiles

    def get_profile_by_email(self, email: str) -> dict | None:
        rows = self._call("GET", "profiles", {"select": PROFILE_COLUMNS, "email": f"eq.{email}", "limit": 1})
        return rows[0] if rows else None

    def create_profile(self, email: str, full_name: str | None, company: str | None, password_hash: str) -> dict:
        rows = self._call("POST", "profiles", {"select": PROFILE_COLUMNS}, prefer="return=representation", json={
            "id": str(uuid.uuid4()), "email": email, "full_name": full_name, "company": company,
            "password_hash": password_hash})
        return rows[0]

    def set_password(self, profile_id: str, password_hash: str, full_name: str | None, company: str | None) -> dict:
        body = {"password_hash": password_hash, "updated_at": _now()}
        if full_name:
            body["full_name"] = full_name
        if company:
            body["company"] = company
        rows = self._call("PATCH", "profiles", {"id": f"eq.{profile_id}", "select": PROFILE_COLUMNS},
                          prefer="return=representation", json=body)
        if not rows:
            raise SupabaseError("Profile update did not return the account.")
        return rows[0]

    # ------------------------------------------------------------------ saved properties

    def list_saved(self, user_id: str) -> list[dict]:
        return self._call("GET", "saved_properties",
                          {"select": SAVED_COLUMNS, "user_id": f"eq.{user_id}", "order": "created_at.desc"})

    def save_property(self, user_id: str, hcad: str, address: str, zip_code: str | None) -> dict:
        rows = self._call("POST", "saved_properties", {"on_conflict": "user_id,hcad", "select": SAVED_COLUMNS},
                          prefer="resolution=merge-duplicates,return=representation",
                          json={"user_id": user_id, "hcad": hcad, "address": address, "zip": zip_code})
        return rows[0]

    def delete_saved(self, user_id: str, hcad: str) -> None:
        self._call("DELETE", "saved_properties", {"user_id": f"eq.{user_id}", "hcad": f"eq.{hcad}"})

    # ------------------------------------------------------------------ case resolution plans (per account)

    def active_plans(self, user_id: str, hcad: str, case_id: str | None = None) -> list[dict]:
        params = {"select": PLAN_WITH_STEPS, "user_id": f"eq.{user_id}", "hcad": f"eq.{hcad}", "status": "eq.active",
                  "case_plan_steps.order": "position.asc"}
        if case_id is not None:
            params["case_id"] = f"eq.{case_id}"
        return self._call("GET", "case_plans", params)

    def template_plan(self, hcad: str, case_id: str, mode: str) -> dict | None:
        """Latest active plan any account saved for this case; lets a new account reuse it without a new AI call."""
        rows = self._call("GET", "case_plans", {
            "select": PLAN_WITH_STEPS, "hcad": f"eq.{hcad}", "case_id": f"eq.{case_id}", "status": "eq.active",
            "mode": f"eq.{mode}", "order": "created_at.desc", "limit": 1, "case_plan_steps.order": "position.asc"})
        return rows[0] if rows else None

    def get_plan(self, plan_id: str, user_id: str) -> dict | None:
        rows = self._call("GET", "case_plans", {"select": PLAN_WITH_STEPS, "id": f"eq.{plan_id}",
                                                "user_id": f"eq.{user_id}", "case_plan_steps.order": "position.asc"})
        return rows[0] if rows else None

    def supersede_plans(self, user_id: str, hcad: str, case_id: str) -> None:
        self._call("PATCH", "case_plans", {"user_id": f"eq.{user_id}", "hcad": f"eq.{hcad}", "case_id": f"eq.{case_id}",
                                           "status": "eq.active"}, json={"status": "superseded", "updated_at": _now()})

    def insert_plan(self, row: dict) -> dict:
        return self._call("POST", "case_plans", {"select": PLAN_COLUMNS}, prefer="return=representation", json=row)[0]

    def insert_steps(self, rows: list[dict]) -> list[dict]:
        return self._call("POST", "case_plan_steps", {"select": STEP_COLUMNS}, prefer="return=representation", json=rows)

    def delete_plan(self, plan_id: str, user_id: str) -> None:
        self._call("DELETE", "case_plans", {"id": f"eq.{plan_id}", "user_id": f"eq.{user_id}"})

    def update_step(self, step_id: str, user_id: str, status: str) -> list[dict]:
        """Filtered by user_id as well as id, so an account can only change its own steps."""
        body = {"status": status, "completed_at": _now() if status == "completed" else None, "updated_at": _now()}
        return self._call("PATCH", "case_plan_steps", {"id": f"eq.{step_id}", "user_id": f"eq.{user_id}",
                                                       "select": STEP_COLUMNS}, prefer="return=representation", json=body)

    # ------------------------------------------------------------------ investigation tasks (per account)

    def list_tasks(self, user_id: str, hcad: str | None = None, task_key: str | None = None,
                   action_type: str | None = None, task_id: str | None = None) -> list[dict]:
        params = {"select": TASK_WITH_FEEDBACK, "user_id": f"eq.{user_id}", "order": "created_at.asc",
                  "investigation_task_feedback.order": "created_at.desc"}
        for key, value in (("property_hcad", hcad), ("task_key", task_key), ("action_type", action_type), ("id", task_id)):
            if value is not None:
                params[key] = f"eq.{value}"
        return self._call("GET", "investigation_tasks", params)

    def insert_task(self, row: dict) -> dict:
        return self._call("POST", "investigation_tasks", {"select": TASK_COLUMNS}, prefer="return=representation",
                          json=row)[0]

    def patch_task(self, user_id: str, task_id: str, patch: dict) -> list[dict]:
        body = {**patch, "updated_at": _now()}
        return self._call("PATCH", "investigation_tasks", {"id": f"eq.{task_id}", "user_id": f"eq.{user_id}",
                                                           "select": TASK_COLUMNS}, prefer="return=representation",
                          json=body)

    def insert_task_feedback(self, row: dict) -> dict:
        return self._call("POST", "investigation_task_feedback", {"select": FEEDBACK_COLUMNS},
                          prefer="return=representation", json=row)[0]

    def touch_plan(self, plan_id: str, user_id: str) -> None:
        self._call("PATCH", "case_plans", {"id": f"eq.{plan_id}", "user_id": f"eq.{user_id}"}, json={"updated_at": _now()})
