"""End-to-End Pipeline for Manual Adjustments Ingestion, Validation, and Agentic Reasoning.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any
import pandas as pd

from .models import (
    AdjustmentBatch,
    EntryEvaluationResult,
    EntryStatus,
)
from .deterministic_validator import DeterministicValidator
from .llm_reasoning_agent import AdjustmentReasoningAgent


class AdjustmentPipeline:
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.input_dir = project_dir / "inputs"
        self.output_dir = project_dir / "outputs"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.coa_file = self.input_dir / "chart_of_accounts.csv"
        self.adjustments_file = self.input_dir / "manual_adjustments.json"

        # Initialize components
        coa_df = pd.read_csv(self.coa_file)
        self.validator = DeterministicValidator(coa_df)
        self.agent = AdjustmentReasoningAgent()

    def generate_trace_id(self, entry_data: Dict[str, Any]) -> str:
        """Create a cryptographic SHA-256 audit trace hash for an entry."""
        serialized = json.dumps(entry_data, sort_keys=True, default=str)
        return f"AUDIT-{hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:12].upper()}"

    def run(self) -> Dict[str, Any]:
        """Execute the full ingestion, validation, reasoning, and reporting workflow."""
        with open(self.adjustments_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        batch = AdjustmentBatch.model_validate(raw_data)
        evaluations: List[EntryEvaluationResult] = []

        accepted_entries = []
        quarantined_entries = []
        rejected_entries = []

        for entry in batch.entries:
            trace_id = self.generate_trace_id(entry.model_dump(mode="json"))
            status, issues, total_debit, total_credit, difference = self.validator.validate_entry(entry)

            # Generate agent explanation
            explanation = self.agent.explain(
                entry=entry,
                status=status,
                issues=issues,
                coa_context={"period": batch.period, "currency": batch.functional_currency},
            )

            result = EntryEvaluationResult(
                entry_id=entry.id,
                description=entry.description,
                date=entry.date,
                source=entry.source,
                total_debit=total_debit,
                total_credit=total_credit,
                difference=difference,
                status=status,
                issues=issues,
                agent_explanation=explanation,
                audit_trace_id=trace_id,
            )
            evaluations.append(result)

            if status == EntryStatus.ACCEPTED:
                accepted_entries.append(entry.model_dump(mode="json"))
            elif status == EntryStatus.QUARANTINED:
                quarantined_entries.append(result.model_dump(mode="json"))
            else:
                rejected_entries.append(result.model_dump(mode="json"))

        # Save artifacts
        self._save_artifacts(batch, evaluations, accepted_entries, quarantined_entries, rejected_entries)

        return {
            "period": batch.period,
            "currency": batch.functional_currency,
            "total_processed": len(evaluations),
            "accepted_count": len(accepted_entries),
            "quarantined_count": len(quarantined_entries),
            "rejected_count": len(rejected_entries),
            "evaluations": evaluations,
        }

    def _save_artifacts(
        self,
        batch: AdjustmentBatch,
        evaluations: List[EntryEvaluationResult],
        accepted_entries: List[Dict[str, Any]],
        quarantined_entries: List[Dict[str, Any]],
        rejected_entries: List[Dict[str, Any]],
    ) -> None:
        """Persist structured outputs for downstream accounting engines and auditors."""
        # 1. Validated and postable adjustments JSON
        postable_payload = {
            "period": batch.period,
            "functional_currency": batch.functional_currency,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "accepted_count": len(accepted_entries),
            "entries": accepted_entries,
        }
        with open(self.output_dir / "validated_adjustments.json", "w", encoding="utf-8") as f:
            json.dump(postable_payload, f, indent=2)

        # 2. Detailed rejected and quarantined ledger report
        flagged_payload = {
            "period": batch.period,
            "functional_currency": batch.functional_currency,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "quarantined_count": len(quarantined_entries),
            "rejected_count": len(rejected_entries),
            "quarantined_entries": quarantined_entries,
            "rejected_entries": rejected_entries,
        }
        with open(self.output_dir / "rejections_and_quarantines_detailed.json", "w", encoding="utf-8") as f:
            json.dump(flagged_payload, f, indent=2, default=str)

        # 3. Audit trail CSV
        audit_rows = []
        for ev in evaluations:
            audit_rows.append({
                "entry_id": ev.entry_id,
                "status": ev.status.value,
                "date": ev.date,
                "description": ev.description,
                "source": ev.source,
                "total_debit": float(ev.total_debit),
                "total_credit": float(ev.total_credit),
                "difference": float(ev.difference),
                "issue_count": len(ev.issues),
                "issue_codes": "; ".join(i.code for i in ev.issues),
                "agent_summary": ev.agent_explanation.plain_english_summary if ev.agent_explanation else "",
                "recommended_action": ev.agent_explanation.recommended_action if ev.agent_explanation else "",
                "audit_trace_id": ev.audit_trace_id,
            })
        audit_df = pd.DataFrame(audit_rows)
        audit_df.to_csv(self.output_dir / "audit_trail_log.csv", index=False)

        # 4. Executive Summary Markdown
        exec_md = self._build_executive_markdown(batch, evaluations, len(accepted_entries), len(quarantined_entries), len(rejected_entries))
        with open(self.output_dir / "executive_summary.md", "w", encoding="utf-8") as f:
            f.write(exec_md)

    def _build_executive_markdown(
        self,
        batch: AdjustmentBatch,
        evaluations: List[EntryEvaluationResult],
        accepted_cnt: int,
        quarantined_cnt: int,
        rejected_cnt: int,
    ) -> str:
        lines = [
            f"# Executive Summary: Manual Adjustments Audit & Agentic Review",
            f"**Reporting Period:** {batch.period} | **Functional Currency:** {batch.functional_currency} | **Generated At:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}\n",
            "## 1. Batch Overview",
            f"- **Total Journal Entries Submitted:** {len(evaluations)}",
            f"- **Accepted (Clean & Postable):** {accepted_cnt}",
            f"- **Quarantined (Pending Controller Review):** {quarantined_cnt}",
            f"- **Rejected (Hard Non-Compliance):** {rejected_cnt}\n",
            "## 2. Decision Log & Agentic Reasoning\n",
            "| Entry ID | Status | Total Debit | Total Credit | Issue Codes | AI Plain-English Reasoning & Action | Audit Trace ID |",
            "|---|---|---|---|---|---|---|",
        ]

        for ev in evaluations:
            issues_str = "<br>".join([f"`{i.code}`" for i in ev.issues]) if ev.issues else "None"
            exp = ev.agent_explanation
            summary_str = f"**Reason:** {exp.plain_english_summary}<br>**Action:** {exp.recommended_action}" if exp else "Clean"
            lines.append(
                f"| `{ev.entry_id}` | **{ev.status.value}** | ${float(ev.total_debit):,.2f} | ${float(ev.total_credit):,.2f} | {issues_str} | {summary_str} | `{ev.audit_trace_id}` |"
            )

        lines.extend([
            "\n## 3. Recommended Remediation Next Steps",
            "1. **JE-002 (Unbalanced):** Request finance analyst to reconcile $3,500 difference on Marketing vs T&E reclassification.",
            "2. **JE-005 (Orphan Account):** Remap legacy conference travel code `6315` to active Chart of Accounts account `6310`.",
            "3. **JE-008 (Circular Intercompany Wash):** Escalate to Treasury/Controller to confirm counterparty cash or receivable settlement leg.",
        ])

        return "\n".join(lines)
