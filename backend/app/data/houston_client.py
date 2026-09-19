import json
import time
from typing import Any

import httpx

from ..config import CKAN_API_BASE


class SourceUnavailable(Exception):
    """The Houston CKAN API could not be reached or returned an error."""


class HoustonClient:
    """Structured datastore_search access only; no SQL endpoint is used."""

    def __init__(self, http: httpx.Client | None = None, timeout: float = 20.0, retries: int = 2, backoff: float = 0.6):
        self._http = http or httpx.Client(timeout=timeout, headers={"User-Agent": "PropertyCaseInvestigator/0.2 (local MVP)"})
        self._retries = retries
        self._backoff = backoff

    def close(self) -> None:
        self._http.close()

    def _search(self, params: dict[str, Any]) -> dict:
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = self._http.get(CKAN_API_BASE + "datastore_search", params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise SourceUnavailable(f"Houston API returned HTTP {resp.status_code}")
                resp.raise_for_status()
                body = resp.json()
                if not body.get("success"):
                    raise SourceUnavailable(f"Houston API error: {str(body.get('error'))[:200]}")
                return body["result"]
            except (httpx.TransportError, SourceUnavailable, ValueError) as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                raise SourceUnavailable(f"Houston API returned HTTP {exc.response.status_code}") from exc
            if attempt < self._retries:
                time.sleep(self._backoff * (2**attempt))
        raise SourceUnavailable(str(last_error) or "Houston API unavailable") from last_error

    def search(self, resource_id: str, *, filters: dict | None = None, q: str | None = None,
               limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        params: dict[str, Any] = {"resource_id": resource_id, "limit": limit, "offset": offset}
        if filters:
            params["filters"] = json.dumps(filters)
        if q:
            params["q"] = q
        result = self._search(params)
        return result.get("records", []), int(result.get("total") or 0)

    def fetch_all(self, resource_id: str, filters: dict, cap: int, page_size: int = 100) -> tuple[list[dict], int, bool]:
        """Returns (records, total_reported, complete)."""
        records: list[dict] = []
        total = 0
        offset = 0
        while offset < cap:
            page, total = self.search(resource_id, filters=filters, limit=min(page_size, cap - offset), offset=offset)
            records.extend(page)
            offset += len(page)
            if not page or offset >= total:
                break
        return records, total, len(records) >= total
