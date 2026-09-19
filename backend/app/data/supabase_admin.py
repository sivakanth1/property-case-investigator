"""Server-side access to Supabase tables with the service-role key (bypasses RLS; never exposed to the browser)."""
import uuid
from datetime import datetime, timezone

import httpx

PROFILE_COLUMNS = "id,email,full_name,company,password_hash"
SAVED_COLUMNS = "id,user_id,hcad,address,zip,created_at"


class SupabaseError(Exception):
    pass


class EmailTaken(SupabaseError):
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
        if table == "profiles" and (resp.status_code == 409 or (resp.status_code >= 400 and '"23505"' in resp.text)):
            raise EmailTaken("An account with this email already exists.")
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
        body = {"password_hash": password_hash, "updated_at": datetime.now(timezone.utc).isoformat()}
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
