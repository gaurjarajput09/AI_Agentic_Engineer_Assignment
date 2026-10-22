"""Deterministic Validator for Journal Adjustments.

Performs strict, mathematically exact verification without using LLMs.
Handles arithmetic, COA membership, postability constraints, and transaction graph checks.
"""

from decimal import Decimal
from typing import Dict, List, Tuple
import pandas as pd
from .models import (
    JournalEntry,
    ValidationIssue,
    IssueSeverity,
    EntryStatus,
)


class DeterministicValidator:
    def __init__(self, coa_df: pd.DataFrame):
        """Initialize validator with Chart of Accounts metadata.

        Args:
            coa_df: DataFrame loaded from chart_of_accounts.csv
        """
        self.coa = coa_df.copy()
        self.coa.columns = self.coa.columns.str.strip()
        self.coa["account_code"] = self.coa["account_code"].astype(str).str.strip()
        self.coa["account_name"] = self.coa["account_name"].astype(str).str.strip()
        self.coa["account_type"] = self.coa["account_type"].fillna("").astype(str).str.strip()
        self.coa["statement"] = self.coa["statement"].fillna("").astype(str).str.strip()

        # Lookups
        self.account_lookup: Dict[str, Dict[str, str]] = {}
        for _, row in self.coa.iterrows():
            self.account_lookup[row["account_code"]] = {
                "name": row["account_name"],
                "type": row["account_type"],
                "statement": row["statement"],
                "parent_code": str(row.get("parent_code", "")).strip(),
                "normal_balance": str(row.get("normal_balance", "")).strip(),
            }

    def validate_entry(self, entry: JournalEntry) -> Tuple[EntryStatus, List[ValidationIssue], Decimal, Decimal, Decimal]:
        """Validate a single journal entry deterministically.

        Returns:
            Tuple of (status, issues, total_debit, total_credit, difference)
        """
        issues: List[ValidationIssue] = []

        total_debit = sum(Decimal(str(line.debit)) for line in entry.lines)
        total_credit = sum(Decimal(str(line.credit)) for line in entry.lines)
        difference = total_debit - total_credit

        # 1. Arithmetic check: Debit must equal Credit
        if abs(difference) > Decimal("0.0001"):
            issues.append(
                ValidationIssue(
                    code="UNBALANCED_ENTRY",
                    message=f"Debit total ({total_debit}) does not equal credit total ({total_credit}). Imbalance: {difference}",
                    severity=IssueSeverity.CRITICAL,
                    details={
                        "total_debit": float(total_debit),
                        "total_credit": float(total_credit),
                        "imbalance": float(difference),
                    },
                )
            )

        # 2. Line-level integrity & COA presence
        if not entry.lines:
            issues.append(
                ValidationIssue(
                    code="EMPTY_ENTRY",
                    message="Journal entry contains no transaction lines.",
                    severity=IssueSeverity.CRITICAL,
                )
            )

        accounts_debited = []
        accounts_credited = []

        for idx, line in enumerate(entry.lines, start=1):
            acct = str(line.account).strip()
            deb = Decimal(str(line.debit))
            cred = Decimal(str(line.credit))

            # Non-negative checks
            if deb < Decimal("0") or cred < Decimal("0"):
                issues.append(
                    ValidationIssue(
                        code="NEGATIVE_AMOUNT",
                        message=f"Line {idx} has negative debit or credit amount.",
                        severity=IssueSeverity.CRITICAL,
                        details={"line": idx, "account": acct, "debit": float(deb), "credit": float(cred)},
                    )
                )

            if deb > Decimal("0") and cred > Decimal("0"):
                issues.append(
                    ValidationIssue(
                        code="AMBIGUOUS_LINE",
                        message=f"Line {idx} specifies both debit and credit amounts.",
                        severity=IssueSeverity.WARNING,
                        details={"line": idx, "account": acct},
                    )
                )

            if deb > Decimal("0"):
                accounts_debited.append((acct, deb, line.memo))
            if cred > Decimal("0"):
                accounts_credited.append((acct, cred, line.memo))

            # COA existence check
            if acct not in self.account_lookup:
                issues.append(
                    ValidationIssue(
                        code="ORPHAN_ACCOUNT_NOT_IN_COA",
                        message=f"Account '{acct}' on line {idx} does not exist in Chart of Accounts.",
                        severity=IssueSeverity.CRITICAL,
                        details={"line": idx, "account": acct},
                    )
                )
            else:
                acct_info = self.account_lookup[acct]
                # Postability check: Header accounts cannot receive postings
                if acct_info["type"].lower() == "header":
                    issues.append(
                        ValidationIssue(
                            code="NON_POSTABLE_HEADER_ACCOUNT",
                            message=f"Account '{acct}' ({acct_info['name']}) is a summary Header node and cannot be posted to.",
                            severity=IssueSeverity.CRITICAL,
                            details={"line": idx, "account": acct, "name": acct_info["name"]},
                        )
                    )

        # 3. Intercompany and Circular Wash Entry Detection
        desc_lower = entry.description.lower()
        is_ic_flagged = "intercompany" in desc_lower or "ic " in desc_lower or any(
            "2170" in str(line.account) for line in entry.lines
        )

        # Check for circular wash on identical account
        debited_codes = {item[0]: item[1] for item in accounts_debited}
        credited_codes = {item[0]: item[1] for item in accounts_credited}
        overlap_accounts = set(debited_codes.keys()).intersection(set(credited_codes.keys()))

        if overlap_accounts:
            for acct in overlap_accounts:
                d_amt = debited_codes[acct]
                c_amt = credited_codes[acct]
                if d_amt == c_amt:
                    issues.append(
                        ValidationIssue(
                            code="CIRCULAR_WASH_TRANSACTION",
                            message=f"Account '{acct}' is both debited and credited with equal amount {d_amt}. Self-cancelling circular wash entry.",
                            severity=IssueSeverity.WARNING,
                            details={"account": acct, "amount": float(d_amt), "is_intercompany": is_ic_flagged},
                        )
                    )

        # Intercompany entries require explicit sub-ledger/counterparty metadata
        if is_ic_flagged and not overlap_accounts:
            issues.append(
                ValidationIssue(
                    code="INTERCOMPANY_REVIEW_REQUIRED",
                    message="Intercompany transaction detected; requires elimination matching and counterparty verification.",
                    severity=IssueSeverity.INFO,
                    details={"description": entry.description},
                )
            )

        # 4. Determine overall status based on issues
        has_critical = any(i.severity == IssueSeverity.CRITICAL for i in issues)
        has_warning = any(i.severity == IssueSeverity.WARNING for i in issues)

        if has_critical:
            status = EntryStatus.REJECTED
        elif has_warning:
            status = EntryStatus.QUARANTINED
        else:
            status = EntryStatus.ACCEPTED

        return status, issues, total_debit, total_credit, difference
