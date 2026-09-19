from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ActionType = Literal["verify_current_condition", "review_case_history", "reconcile_records"]
Priority = Literal["low", "medium", "high"]
TaskStatus = Literal["open", "in_progress", "verified", "dismissed"]
FindingType = Literal["case_history", "recurrence", "source_status", "data_quality", "coverage_gap"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- tool arguments

class GetPropertyCasesArgs(Strict):
    property_id: int


class GetCaseDetailsArgs(Strict):
    property_id: int
    case_id: str = Field(min_length=1, max_length=32)


class GetPropertyMemoryArgs(Strict):
    property_id: int


class ProposeFindingArgs(Strict):
    property_id: int
    type: FindingType
    summary: str = Field(min_length=10, max_length=600)
    evidence_ids: list[int] = Field(default_factory=list, max_length=40)
    uncertainty: str = Field(min_length=3, max_length=300)
    revises_proposal_id: str | None = Field(default=None, pattern=r"^F-\d+$",
                                            description="Only when correcting your own earlier finding after review.")


class ProposeTaskArgs(Strict):
    property_id: int
    action_type: ActionType
    case_ids: list[str] = Field(min_length=1, max_length=20)
    title: str = Field(min_length=5, max_length=140)
    reason: str = Field(min_length=10, max_length=600)
    priority: Priority
    evidence_ids: list[int] = Field(min_length=1, max_length=40)
    revises_proposal_id: str | None = Field(default=None, pattern=r"^T-\d+$",
                                            description="Only when correcting your own earlier task after review.")


# ---------------------------------------------------------------- API requests

class InvestigationCreate(Strict):
    property_id: int


class ImportRequest(Strict):
    hcad: str = Field(pattern=r"^\d{6,20}$")


class TaskUpdate(Strict):
    status: TaskStatus | None = None
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _needs_change(self):
        if self.status is None and not (self.note and self.note.strip()):
            raise ValueError("Provide a status, a note, or both.")
        return self


EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class _EmailBody(Strict):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=320)

    @field_validator("email", mode="before")
    @classmethod
    def _trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class SignUpRequest(_EmailBody):
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=120)
    company: str | None = Field(default=None, max_length=120)


class SignInRequest(_EmailBody):
    password: str = Field(min_length=1, max_length=128)


class SavePropertyRequest(Strict):
    hcad: str = Field(pattern=r"^\d{6,20}$")
    address: str = Field(min_length=1, max_length=200)
    zip: str | None = Field(default=None, max_length=16)


class PlanRequest(Strict):
    regenerate: bool = False


class StepUpdate(Strict):
    status: Literal["pending", "completed"]


class ProposalDecision(Strict):
    note: str | None = Field(default=None, max_length=1000)


class BulkRefreshRequest(Strict):
    hcads: list[str] = Field(min_length=1, max_length=10000)

