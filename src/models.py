"""Data models for Manual Adjustments Validation and Agentic Reasoning.
"""

from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class EntryStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


class IssueSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: IssueSeverity
    details: Dict[str, Any] = Field(default_factory=dict)


class JournalLine(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    account: str
    debit: Decimal
    credit: Decimal
    memo: Optional[str] = ""


class JournalEntry(BaseModel):
    id: str
    description: str
    date: str
    source: str
    lines: List[JournalLine]


class AdjustmentBatch(BaseModel):
    period: str
    functional_currency: str
    entries: List[JournalEntry]


class AgentExplanation(BaseModel):
    entry_id: str
    plain_english_summary: str
    accounting_impact: str
    recommended_action: str
    suggested_correction: Optional[Dict[str, Any]] = None
    confidence_score: float = Field(ge=0.0, le=1.0)


class EntryEvaluationResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    entry_id: str
    description: str
    date: str
    source: str
    total_debit: Decimal
    total_credit: Decimal
    difference: Decimal
    status: EntryStatus
    issues: List[ValidationIssue]
    agent_explanation: Optional[AgentExplanation] = None
    audit_trace_id: str
