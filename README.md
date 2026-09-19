# Property Case Investigator

A local web app that investigates **historical** City of Houston code-enforcement records for a property, proposes
evidence-backed **verification tasks**, and remembers human feedback on later runs. It does not monitor live
enforcement, predict risk, or change any city record.

## How it works

```
React (Vite, :5173) ──/api proxy──► FastAPI (:8000) ──► single in-process investigation worker
                                        │                    │
                                        │         investigator (live model or labeled deterministic demo)
                                        │                    │ tools: get_property_memory, get_property_cases,
                                        │                    │        get_case_details, propose_finding, propose_task
                                        │         reviewer (model review + deterministic checks, one revision cycle)
                                        │                    │
                                        │         deterministic commit gate ──► tasks (unique task_key)
                                        ▼
                                   SQLite: properties, source_records, source_snapshots, investigation_runs,
                                           run_events, findings, task_proposals, tasks, feedback, case_plans,
                                           case_plan_steps, auth_sessions
Houston CKAN datastore_search (HCAD filters) ──► cached source_records with retrieval date + completeness
Supabase (via backend service-role key only): profiles (email, name, scrypt password hash) and saved portfolio
```

- Every finding and task cites source rows (evidence ids → resource id + row id + original fields).
- Rows sharing a case ID count as one case; recurrence is counted by distinct case IDs.
- Task identity is computed in code from `property + action_type + sorted case IDs`. A repeat proposal only refreshes
  evidence and never resets a `verified` or `dismissed` status. Overlapping scopes are held for human review.
- Runs are checkpointed after each step. A backend restart marks active runs `interrupted`; **Resume** continues from
  the checkpoint without duplicating committed tasks.
- Budget per run: 8 investigator tool calls, one reviewer revision cycle, 90 seconds.

## Run it

Requirements: Python 3.11+ and Node 20+ (tested with Python 3.13 and Node 22 on Windows).

```bash
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The script creates `backend/.venv`, installs dependencies, copies `backend/.env.example` to `backend/.env` if needed,
and opens two terminals (backend on 127.0.0.1:8000, frontend on http://localhost:5173). On first start the backend
seeds the five demo properties from `sample_data/houston_snapshot.json`.

Manual equivalent:

```bash
cd backend && .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
cd frontend && npm.cmd run dev
```

## AI model (Featherless.ai)

All AI features use Featherless.ai. Without a key they run in **deterministic demo mode**, labeled everywhere as
"not AI".

1. In Featherless, create an API key (Account → API Keys).
2. In `backend/.env` (created by `start.ps1`, or copy `backend/.env.example`) set:

   ```
   FEATHERLESS_API_KEY=<your key>
   FEATHERLESS_MODEL=Qwen/Qwen3-32B
   ```

3. Restart the backend. The navbar badge changes to "Featherless AI · Qwen/Qwen3-32B".

Model choice (verified 2026-09-19): Featherless documents tool calling for the Qwen 3 family, but
`Qwen3-30B-A3B-Instruct-2507` returned HTTP 500 whenever tools were sent; `Qwen/Qwen3-32B` returned correct native
tool calls. The app appends Qwen's `/no_think` switch, strips any `<think>` text, retries once when Featherless
returns an empty "no_response" body, and in `LLM_TOOL_MODE=auto` falls back to a plain JSON tool protocol if a model
rejects tool definitions. The key stays server-side; it is never sent to the browser or logged.

**Cost control:** resolution steps are generated once per case and reused; an investigation makes at most about 10
model calls (8 tool calls, review, one revision). Qwen3-32B uses 2 of the 4 concurrency units on the Chat plan, so
avoid starting several AI actions at the same time.

## Live Houston data and resolution steps

- **Search** on the Properties page queries the live City of Houston CKAN API (grouped by HCAD, never merged).
  Opening a result fetches every record for that parcel. Opening a property again re-fetches it when the cached copy
  is older than `LIVE_REFRESH_MINUTES` (default 30); if the API is down the cached real records are shown and labeled.
- **Resolution steps:** each open case (not marked `CLOSED` in the source) has a *Get AI resolution steps* button;
  closed cases show it disabled. The first click asks Featherless for a checklist grounded in the case's violation
  categories, descriptions and ordinance numbers. The backend then validates it: the first step must be a
  verification step, invented ordinances are removed, and evidence ids are limited to the case. The checklist is
  saved (`case_plans`, `case_plan_steps`) and shown again every time the property is opened, so the steps do not change
  between visits. Steps are a to-do list you tick off. *Regenerate* replaces the list and archives the old one.

## Accounts (email + password, stored in Supabase)

Sign-up and sign-in go through the backend (`/api/auth/signup`, `/api/auth/signin`). Passwords are hashed with
scrypt and stored in `profiles.password_hash`; the backend reaches Supabase with the service-role key, and the browser
never talks to Supabase directly. Setup: [SUPABASE_SETUP.md](SUPABASE_SETUP.md). Without the key, choose **Continue as
a local guest**: the portfolio is kept in the browser, and investigations and tasks work the same.

## Real data provenance

`sample_data/houston_snapshot.json` was generated on 2026-09-19 by `backend/scripts/build_snapshot.py` using CKAN
`datastore_search` with an `HCAD` filter on both resources (Violations `1446a3ec-2633-4cf1-b15d-6dae9a07c4ed`,
All Projects `496e91d0-1695-4d1f-930b-7c103806613d`). All pages were fetched; every property is complete.

| HCAD | Address | Distinct cases | Source rows | Record dates |
|---|---|---|---|---|
| 0422260050040 | 2821 LUELL ST | 3 | 6 | 2004-03-03 → 2016-04-29 |
| 0551730000009 | 1801 SAKOWITZ | 14 | 49 | 2004-10-04 → 2018-05-24 |
| 0831530000023 | 1337 CONRAD SAUER | 22 | 60 | 2005-08-31 → 2018-07-08 |
| 0372600000006 | 3410 BREMOND | 13 | 58 | 2005-09-07 → 2018-02-13 |
| 0761540320011 | 5918 SOUTHINGTON | 1 | 9 | 2018-05-09 |

Earlier dates come from the All Projects resource; violation rows fall between 2014 and 2018. Rebuild with
`cd backend && .venv\Scripts\python -m scripts.build_snapshot` (optionally pass HCADs). Other parcels can be imported
from the Properties page; ambiguous address matches are listed per HCAD and never merged.

## Tests

```bash
cd backend && .venv\Scripts\python -m pytest
```

```bash
cd frontend && npm.cmd run build
```

`backend/tests/test_acceptance.py` covers the eight acceptance checks (one case per case ID, no duplicate tasks,
feedback persisting across a restart and respected on rerun, rejection of invalid or cross-property evidence,
labeled cache or explicit blocker on API failure, labeled deterministic mode, resume without duplicates, and
ambiguous candidates never merged) plus API status codes. `test_llm_loop.py` drives the live-model loop with a
scripted fake client: native tool calls, JSON-protocol fallback, reviewer failure, the revision cycle, budget
exhaustion, denied cross-property access, and timeouts. Rows marked `TEST FIXTURE` are fabricated for tests only.

## Three-minute demo

1. **Properties**: point out the historical-data notice, snapshot date and coverage on 1801 SAKOWITZ.
2. Click **Investigate**. The activity log shows each tool call (memory → cases → case detail → proposals → review → commit gate).
3. Open finding F-1 and expand its evidence: the original source fields, row IDs and a link to the source row.
4. **Tasks**: open the "Verify current condition" task, click *Fill "inspection completed"*, and **Save feedback**.
5. Restart the backend (close and reopen its terminal), then click **Start investigation** again. The summary says it
   respected the verified task instead of recreating it; the commit gate only refreshes open tasks.
6. Point to the mode badge: deterministic demo unless a Featherless key is configured.

## API

`GET /api/health` · `GET /api/properties?query=` · `GET /api/properties/{id}` · `GET /api/properties/{id}/cases` ·
`POST /api/properties/{id}/refresh` · `GET /api/source/candidates?query=` · `POST /api/properties/import` ·
`GET /api/evidence?ids=` · `POST /api/investigations` (202) · `GET /api/investigations?property_id=` ·
`GET /api/investigations/{id}` · `POST /api/investigations/{id}/resume` · `GET /api/tasks?property_id=` ·
`PATCH /api/tasks/{id}` · `POST /api/proposals/{id}/approve` · `POST /api/proposals/{id}/reject`.
Errors return `{"error": {"code", "message"}}` with 404, 409 or 422.

## Known limitations

- Source data is historical (violations dataset ends 2018-08-22); nothing here establishes current conditions.
- Tasks and feedback are shared per property (single-workspace MVP); Supabase accounts only scope the saved portfolio.
- The deterministic demo policy is scripted; it is a plumbing rehearsal, not an AI result.
- Live-model quality depends on the model. In the verified Qwen3-32B run, the saved tasks and findings were correct,
  but the free-text summary wrongly said every case was closed; summaries are not machine-checked.
- Featherless occasionally returns an empty "no_response" error; the app retries once, then keeps a partial result.
- No geocoding, maps, notifications or city filings, by design.
- `_legacy_backup/` holds the pre-refactor prototype for reference and can be deleted once no longer needed.
