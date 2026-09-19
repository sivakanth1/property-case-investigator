import re
from datetime import date, datetime
from typing import Any

from ..config import PROJECTS_RESOURCE_ID, VIOLATIONS_RESOURCE_ID

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y")
_CATEGORY_PREFIX = re.compile(r"^\s*DON\s*-\s*\d+\s*-\s*", re.IGNORECASE)
_WS = re.compile(r"\s+")


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def normalize_address(address: str | None) -> str:
    return _WS.sub(" ", (address or "").strip()).upper()


def display_address(address: str | None) -> str:
    return _WS.sub(" ", (address or "").strip())


def clean_notes(value: str | None) -> str | None:
    """Display form only; the original text stays in raw_json."""
    if not value:
        return None
    cleaned = value.replace("_x000d_\n", "\n").replace("_x000D_\n", "\n")
    cleaned = cleaned.replace("_x000d_", "\n").replace("_x000D_", "\n").replace("\r\n", "\n")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() or None


def parse_date(value: Any) -> date | None:
    text = text_or_none(value)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[:19] if "%H" in fmt else text[:10], fmt).date()
        except ValueError:
            continue
    return None


def iso_date(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def normalize_category(value: str | None) -> str | None:
    text = text_or_none(value)
    return _CATEGORY_PREFIX.sub("", text).strip() if text else None


def case_id_of(resource_id: str, raw: dict) -> str | None:
    key = "NPPRJID" if resource_id == VIOLATIONS_RESOURCE_ID else "NPPRJId"
    return text_or_none(raw.get(key))


def normalized_fields(resource_id: str, raw: dict) -> dict:
    """Shared schema across both Houston resources. Missing values stay None."""
    is_violation = resource_id == VIOLATIONS_RESOURCE_ID
    fields = {
        "case_id": case_id_of(resource_id, raw),
        "hcad": text_or_none(raw.get("HCAD")),
        "address": display_address(raw.get("Merged_Situs")) or None,
        "zip": text_or_none(raw.get("Zip") if is_violation else raw.get("ZipCode")),
        "service_request_id": text_or_none(raw.get("Sr_Request_Num")),
        "created_date": iso_date(raw.get("RecordCreateDate")),
        "source_status": text_or_none(raw.get("Project_Status")),
        "received_method": text_or_none(raw.get("Received_Method")),
        "notes": clean_notes(text_or_none(raw.get("Comment311upd"))),
    }
    if is_violation:
        fields.update(
            violation_id=text_or_none(raw.get("ViolationSubId")),
            category=normalize_category(raw.get("Violation_Category")),
            ordinance=text_or_none(raw.get("Ordno")),
            short_description=text_or_none(raw.get("ShortDescription")),
            deadline_date=iso_date(raw.get("DeadLineDate")),
            checkback_date=iso_date(raw.get("CheckBackDate")),
        )
    elif resource_id == PROJECTS_RESOURCE_ID:
        fields.update(count_of_violations=text_or_none(raw.get("Count_of_Violations")))
    return fields
