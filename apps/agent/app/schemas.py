
from typing import Literal

from pydantic import BaseModel, Field


class IncidentInput(BaseModel):
    incident_id: int
    title: str
    severity: str
    status: str
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class RCAResult(BaseModel):
    incident_id: int
    root_cause: str
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[str]
    evidence_gaps: list[str]
    proposed_action: Literal["set_fault_normal", "no_action"]
    action_reason: str
    requires_approval: bool = True
