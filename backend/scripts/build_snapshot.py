"""Fetch the demo properties from the live Houston CKAN API and save a real snapshot with provenance.

Usage (from backend/):  .venv\\Scripts\\python -m scripts.build_snapshot [HCAD ...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATASET_URL, PROJECTS_RESOURCE_ID, VIOLATIONS_RESOURCE_ID, get_settings  # noqa: E402
from app.data.houston_client import HoustonClient  # noqa: E402
from app.data.repository import fetch_property_rows  # noqa: E402
from app.db import iso, utcnow  # noqa: E402

# Chosen from a bounded sample of the violations resource: a mix of single-case and multi-case parcels.
DEMO_HCADS = [
    "0422260050040",  # 2821 LUELL ST - small history
    "0551730000009",  # 1801 SAKOWITZ - many distinct cases, nuisance + dangerous building
    "0831530000023",  # 1337 CONRAD SAUER - several categories across cases
    "0372600000006",  # 3410 BREMOND ST - one case with several violation rows
    "0761540320011",  # 5918 SOUTHINGTON - one case whose rows are not marked closed
]


def main(hcads: list[str]) -> None:
    settings = get_settings()
    client = HoustonClient()
    properties = []
    for hcad in hcads:
        payload = fetch_property_rows(client, hcad, settings.max_rows_per_property)
        counts = {rid[:8]: len(res["records"]) for rid, res in payload["resources"].items()}
        complete = all(res["complete"] for res in payload["resources"].values())
        print(f"{hcad}: rows {counts} complete={complete}")
        properties.append(payload)
    client.close()
    out = {
        "generated_at": iso(utcnow()),
        "source": {"dataset_url": DATASET_URL, "violations_resource_id": VIOLATIONS_RESOURCE_ID,
                   "projects_resource_id": PROJECTS_RESOURCE_ID, "method": "datastore_search with HCAD filters"},
        "note": "Real historical records retrieved from the City of Houston CKAN API. Not a live feed.",
        "properties": properties,
    }
    settings.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    settings.snapshot_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"Wrote {settings.snapshot_path}")


if __name__ == "__main__":
    main(sys.argv[1:] or DEMO_HCADS)
