import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from ..db import session_scope
from ..schemas import (
    GetCaseDetailsArgs, GetPropertyCasesArgs, GetPropertyMemoryArgs, ProposeFindingArgs, ProposeTaskArgs,
)
from .case_tools import get_case_details, get_property_cases
from .errors import ToolError
from .memory_tools import get_property_memory
from .proposal_tools import propose_finding, propose_task

MAX_RESULT_CHARS = 5000


@dataclass(frozen=True)
class ToolContext:
    run_id: int
    property_id: int
    hcad: str = ""
    store: object = None  # task store the run reads memory from and commits to


@dataclass(frozen=True)
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    fn: Callable
    description: str


TOOLS: dict[str, ToolSpec] = {t.name: t for t in (
    ToolSpec("get_property_memory", GetPropertyMemoryArgs, get_property_memory,
             "Load previous findings, tasks and human feedback for this property. Call this first."),
    ToolSpec("get_property_cases", GetPropertyCasesArgs, get_property_cases,
             "Grouped cases (by case_id) with dates, categories, statuses, evidence ids, coverage and distinct-case recurrence."),
    ToolSpec("get_case_details", GetCaseDetailsArgs, get_case_details,
             "Detailed violation and project rows (with evidence ids and truncated notes) for one case_id."),
    ToolSpec("propose_finding", ProposeFindingArgs, propose_finding,
             "Save a draft evidence-backed finding. evidence_ids must come from tool results for this property."),
    ToolSpec("propose_task", ProposeTaskArgs, propose_task,
             "Save a draft verification task. action_type: verify_current_condition | review_case_history | "
             "reconcile_records. priority: low | medium | high with an evidence-backed reason."),
)}


def _clean_schema(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _clean_schema(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_clean_schema(v) for v in node]
    return node


def tool_parameters(spec: ToolSpec) -> dict:
    return _clean_schema(spec.args_model.model_json_schema())


def openai_tool_specs() -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": tool_parameters(t)}} for t in TOOLS.values()]


def _error(kind: str, message: str, details: Any = None) -> dict:
    err = {"type": kind, "message": message}
    if details is not None:
        err["details"] = details
    return {"ok": False, "error": err}


def execute_tool(ctx: ToolContext, name: str, raw_args: str | dict | None) -> dict:
    spec = TOOLS.get(name)
    if spec is None:
        return _error("unknown_tool", f"Unknown tool '{name}'. Available: {sorted(TOOLS)}")
    try:
        data = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        if not isinstance(data, dict):
            raise ValueError("arguments must be a JSON object")
        args = spec.args_model.model_validate(data)
    except (ValueError, ValidationError) as exc:
        details = exc.errors(include_url=False, include_input=False) if isinstance(exc, ValidationError) else str(exc)
        return _error("invalid_arguments", f"Arguments for {name} failed validation.", details)
    if args.property_id != ctx.property_id:
        return _error("property_access_denied", f"This run may only access property_id {ctx.property_id}.")
    try:
        with session_scope() as s:
            return {"ok": True, "result": spec.fn(s, ctx, args)}
    except ToolError as exc:
        return _error("tool_error", str(exc))


def bounded_json(result: dict) -> str:
    text = json.dumps(result, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return json.dumps({"ok": result.get("ok"), "truncated": True, "partial": text[:MAX_RESULT_CHARS]})
