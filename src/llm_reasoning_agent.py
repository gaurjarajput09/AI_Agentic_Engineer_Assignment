"""LLM Reasoning & Explanation Agent for Journal Adjustments.

Translates technical/deterministic accounting errors into audit-grade, plain-English
explanations and concrete remediation steps for finance controllers and auditors.
Supports live LLMs (OpenAI/Anthropic-compatible) with an autonomous domain-aware fallback.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List
from .models import (
    JournalEntry,
    ValidationIssue,
    AgentExplanation,
    EntryStatus,
)

logger = logging.getLogger(__name__)


class AdjustmentReasoningAgent:
    """Agent responsible for reasoning over financial violations and generating human-interpretable explanations."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model

    def explain(
        self,
        entry: JournalEntry,
        status: EntryStatus,
        issues: List[ValidationIssue],
        coa_context: Optional[Dict[str, Any]] = None,
    ) -> AgentExplanation:
        """Generate a contextual financial explanation and recommended action."""
        if not issues and status == EntryStatus.ACCEPTED:
            return AgentExplanation(
                entry_id=entry.id,
                plain_english_summary="Entry passes all deterministic arithmetic, COA membership, and postability checks.",
                accounting_impact="No adverse impact. Entry is balanced and ready for automated ledger posting.",
                recommended_action="Approve and post to General Ledger.",
                suggested_correction=None,
                confidence_score=1.0,
            )

        # If live API key is configured, attempt LLM call
        if self.api_key:
            try:
                explanation = self._call_llm(entry, status, issues, coa_context)
                if explanation:
                    return explanation
            except Exception as e:
                logger.warning(f"Live LLM call failed ({e}); falling back to deterministic reasoning engine.")

        # Autonomous domain reasoning fallback (guarantees offline reproducibility)
        return self._synthesize_domain_explanation(entry, status, issues, coa_context)

    def _call_llm(
        self,
        entry: JournalEntry,
        status: EntryStatus,
        issues: List[ValidationIssue],
        coa_context: Optional[Dict[str, Any]],
    ) -> Optional[AgentExplanation]:
        """Call external LLM API if key is present."""
        import urllib.request

        system_prompt = (
            "You are an expert Chief Accounting Officer and financial systems auditor. "
            "Analyze the journal entry and validation issues. Explain the failure in plain English, "
            "assess the accounting impact on financial statements, and suggest the exact remediation. "
            "Return valid JSON matching: "
            "{'plain_english_summary': str, 'accounting_impact': str, 'recommended_action': str, "
            "'suggested_correction': dict or null, 'confidence_score': float}"
        )

        user_content = {
            "entry": entry.model_dump(),
            "status": status.value,
            "issues": [i.model_dump() for i in issues],
            "coa_context": coa_context or {},
        }

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            data=json.dumps({
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_content)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            }).encode("utf-8"),
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = json.loads(data["choices"][0]["message"]["content"])
            return AgentExplanation(
                entry_id=entry.id,
                plain_english_summary=content.get("plain_english_summary", ""),
                accounting_impact=content.get("accounting_impact", ""),
                recommended_action=content.get("recommended_action", ""),
                suggested_correction=content.get("suggested_correction"),
                confidence_score=float(content.get("confidence_score", 0.95)),
            )

    def _synthesize_domain_explanation(
        self,
        entry: JournalEntry,
        status: EntryStatus,
        issues: List[ValidationIssue],
        coa_context: Optional[Dict[str, Any]] = None,
    ) -> AgentExplanation:
        """Domain-expert reasoning engine that synthesizes audit-grade explanations without external API dependency."""
        issue_codes = {i.code for i in issues}
        summaries = []
        impacts = []
        actions = []
        correction = None

        # Case 1: Unbalanced entry (Debit != Credit)
        if "UNBALANCED_ENTRY" in issue_codes:
            unbalanced_issue = next(i for i in issues if i.code == "UNBALANCED_ENTRY")
            deb = unbalanced_issue.details.get("total_debit", 0.0)
            cred = unbalanced_issue.details.get("total_credit", 0.0)
            diff = unbalanced_issue.details.get("imbalance", 0.0)

            summaries.append(
                f"Entry is mathematically unbalanced: total debits (${deb:,.2f}) exceed total credits (${cred:,.2f}) by ${diff:,.2f}."
            )
            impacts.append(
                f"Posting this entry would breach the fundamental accounting equation (Assets = Liabilities + Equity) "
                f"and corrupt the general ledger balance by ${diff:,.2f}, resulting in an out-of-balance trial balance."
            )
            actions.append(
                f"Reject entry. Return to submitter ({entry.source}) to provide an offsetting credit line of ${diff:,.2f} "
                f"or adjust debit/credit line amounts."
            )
            correction = {
                "action": "ADD_OR_ADJUST_LINE",
                "recommended_offset": float(diff),
                "target_account": entry.lines[-1].account,
            }

        # Case 2: Orphan account not in COA
        if "ORPHAN_ACCOUNT_NOT_IN_COA" in issue_codes:
            orphan_issue = next(i for i in issues if i.code == "ORPHAN_ACCOUNT_NOT_IN_COA")
            acct = orphan_issue.details.get("account", "unknown")

            summaries.append(
                f"Account '{acct}' referenced on line {orphan_issue.details.get('line', '')} does not exist in the active Chart of Accounts."
            )
            impacts.append(
                f"The financial reporting pipeline cannot map '{acct}' to any financial statement node (BS or P&L), "
                f"causing statement generation to abort or drop transactions from financial rollups."
            )

            # Heuristic candidate matching
            suggested_account = None
            if acct.startswith("63"):
                suggested_account = "6310"  # Travel and Entertainment parent
                actions.append(
                    f"Quarantine / Reject. Remap legacy account '{acct}' to active parent '{suggested_account}' (Travel and Entertainment), "
                    f"or request GL team to formally add '{acct}' to chart_of_accounts.csv."
                )
            else:
                actions.append(
                    f"Reject entry. Request accounting team to reclassify to a recognized active GL account."
                )

            correction = {
                "action": "REMAP_ACCOUNT",
                "invalid_account": acct,
                "suggested_account": suggested_account or "6000-series",
            }

        # Case 3: Circular wash transaction on intercompany
        if "CIRCULAR_WASH_TRANSACTION" in issue_codes:
            wash_issue = next(i for i in issues if i.code == "CIRCULAR_WASH_TRANSACTION")
            acct = wash_issue.details.get("account", "2170")
            amt = wash_issue.details.get("amount", 0.0)

            summaries.append(
                f"Circular wash detected: Account '{acct}' (Intercompany Payable) is simultaneously debited and credited for ${amt:,.2f}."
            )
            impacts.append(
                f"While mathematically net-zero, this circular transaction obscures actual cross-entity settlement flows, "
                f"artificially inflates gross turnover, and indicates an improper single-sided intercompany adjustment."
            )
            actions.append(
                f"Quarantine for Controller review. Proper intercompany settlement requires an offsetting cash transfer (Account 1110) "
                f"or cross-entity receivable matching, rather than a self-clearing wash on the liability account."
            )
            correction = {
                "action": "REQUIRE_COUNTERPARTY_OR_CASH_LEG",
                "account": acct,
                "suggested_counterpart": "1110 Cash or Intercompany Receivable",
            }

        # Case 4: Header account posting
        if "NON_POSTABLE_HEADER_ACCOUNT" in issue_codes:
            header_issue = next(i for i in issues if i.code == "NON_POSTABLE_HEADER_ACCOUNT")
            acct = header_issue.details.get("account", "")
            acct_name = header_issue.details.get("name", "")
            summaries.append(f"Account '{acct}' ({acct_name}) is a parent summary header and cannot accept direct postings.")
            impacts.append("Posting to a header node breaks sub-ledger reconciliation and double-counts child leaf accounts.")
            actions.append(f"Reclassify line item to a leaf-level child account under '{acct}'.")

        # Compile comprehensive explanation
        plain_english = " | ".join(summaries) if summaries else "Validation flagged one or more compliance review items."
        accounting_impact = " | ".join(impacts) if impacts else "Financial statements may be distorted if posted without review."
        recommended_action = " | ".join(actions) if actions else "Review with financial controller before approving."

        return AgentExplanation(
            entry_id=entry.id,
            plain_english_summary=plain_english,
            accounting_impact=accounting_impact,
            recommended_action=recommended_action,
            suggested_correction=correction,
            confidence_score=0.98,
        )
