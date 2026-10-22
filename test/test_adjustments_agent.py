"""Unit and Integration Tests for Manual Adjustments Agentic Validation System.
"""

from decimal import Decimal
from pathlib import Path
import pandas as pd
import pytest

from src.models import (
    JournalEntry,
    JournalLine,
    EntryStatus,
    IssueSeverity,
)
from src.deterministic_validator import DeterministicValidator
from src.llm_reasoning_agent import AdjustmentReasoningAgent
from src.pipeline import AdjustmentPipeline

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def coa_df():
    coa_path = PROJECT_ROOT / "inputs" / "chart_of_accounts.csv"
    return pd.read_csv(coa_path)


@pytest.fixture
def validator(coa_df):
    return DeterministicValidator(coa_df)


@pytest.fixture
def reasoning_agent():
    return AdjustmentReasoningAgent()


def test_balanced_clean_entry(validator, reasoning_agent):
    entry = JournalEntry(
        id="TEST-001",
        description="Clean bonus accrual",
        date="2024-12-31",
        source="Test Source",
        lines=[
            JournalLine(account="6100", debit=Decimal("50000.00"), credit=Decimal("0.00"), memo="Salaries"),
            JournalLine(account="2120", debit=Decimal("0.00"), credit=Decimal("50000.00"), memo="Accrued expenses"),
        ],
    )
    status, issues, d_tot, c_tot, diff = validator.validate_entry(entry)
    assert status == EntryStatus.ACCEPTED
    assert len(issues) == 0
    assert d_tot == Decimal("50000.00")
    assert c_tot == Decimal("50000.00")
    assert diff == Decimal("0.00")

    explanation = reasoning_agent.explain(entry, status, issues)
    assert "passes all deterministic" in explanation.plain_english_summary
    assert explanation.confidence_score == 1.0


def test_unbalanced_entry_rejected(validator, reasoning_agent):
    entry = JournalEntry(
        id="TEST-002",
        description="Unbalanced reclass",
        date="2024-12-31",
        source="Test Source",
        lines=[
            JournalLine(account="6300", debit=Decimal("28500.00"), credit=Decimal("0.00")),
            JournalLine(account="6310", debit=Decimal("0.00"), credit=Decimal("25000.00")),
        ],
    )
    status, issues, d_tot, c_tot, diff = validator.validate_entry(entry)
    assert status == EntryStatus.REJECTED
    assert any(i.code == "UNBALANCED_ENTRY" for i in issues)
    assert diff == Decimal("3500.00")

    explanation = reasoning_agent.explain(entry, status, issues)
    assert "mathematically unbalanced" in explanation.plain_english_summary
    assert "$3,500.00" in explanation.plain_english_summary
    assert explanation.suggested_correction["action"] == "ADD_OR_ADJUST_LINE"


def test_orphan_account_rejected(validator, reasoning_agent):
    entry = JournalEntry(
        id="TEST-003",
        description="Orphan account entry",
        date="2024-12-31",
        source="Test Source",
        lines=[
            JournalLine(account="9999", debit=Decimal("1000.00"), credit=Decimal("0.00")),
            JournalLine(account="1110", debit=Decimal("0.00"), credit=Decimal("1000.00")),
        ],
    )
    status, issues, _, _, _ = validator.validate_entry(entry)
    assert status == EntryStatus.REJECTED
    assert any(i.code == "ORPHAN_ACCOUNT_NOT_IN_COA" for i in issues)

    explanation = reasoning_agent.explain(entry, status, issues)
    assert "does not exist in the active Chart of Accounts" in explanation.plain_english_summary


def test_header_account_non_postable(validator):
    # Account 1000 is Total Assets (Header)
    entry = JournalEntry(
        id="TEST-004",
        description="Posting to summary header",
        date="2024-12-31",
        source="Test Source",
        lines=[
            JournalLine(account="1000", debit=Decimal("100.00"), credit=Decimal("0.00")),
            JournalLine(account="1110", debit=Decimal("0.00"), credit=Decimal("100.00")),
        ],
    )
    status, issues, _, _, _ = validator.validate_entry(entry)
    assert status == EntryStatus.REJECTED
    assert any(i.code == "NON_POSTABLE_HEADER_ACCOUNT" for i in issues)


def test_circular_intercompany_wash(validator, reasoning_agent):
    entry = JournalEntry(
        id="TEST-005",
        description="Intercompany wash entry",
        date="2024-12-31",
        source="Test Source",
        lines=[
            JournalLine(account="2170", debit=Decimal("320000.00"), credit=Decimal("0.00"), memo="IC down"),
            JournalLine(account="2170", debit=Decimal("0.00"), credit=Decimal("320000.00"), memo="IC up"),
        ],
    )
    status, issues, _, _, _ = validator.validate_entry(entry)
    assert status == EntryStatus.QUARANTINED
    assert any(i.code == "CIRCULAR_WASH_TRANSACTION" for i in issues)

    explanation = reasoning_agent.explain(entry, status, issues)
    assert "Circular wash detected" in explanation.plain_english_summary
    assert "Controller review" in explanation.recommended_action


def test_full_pipeline_run():
    pipeline = AdjustmentPipeline(PROJECT_ROOT)
    results = pipeline.run()

    assert results["total_processed"] == 10
    assert results["accepted_count"] == 7
    assert results["rejected_count"] == 2  # JE-002 (unbalanced), JE-005 (orphan account 6315)
    assert results["quarantined_count"] == 1  # JE-008 (circular intercompany wash)

    # Check generated files
    assert (PROJECT_ROOT / "outputs" / "validated_adjustments.json").exists()
    assert (PROJECT_ROOT / "outputs" / "rejections_and_quarantines_detailed.json").exists()
    assert (PROJECT_ROOT / "outputs" / "audit_trail_log.csv").exists()
    assert (PROJECT_ROOT / "outputs" / "executive_summary.md").exists()
