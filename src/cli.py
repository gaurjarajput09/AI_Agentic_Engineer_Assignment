"""CLI interface for the Manual Adjustments Agentic Validation System.

Provides high-clarity terminal visualization, audit logging, and human-in-the-loop inspection.
Windows CP1252/UTF-8 safe.
"""

import sys
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Reconfigure stdout/stderr to UTF-8 if available on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .pipeline import AdjustmentPipeline
from .models import EntryStatus

console = Console(highlight=False)


def run_cli():
    project_dir = Path(__file__).resolve().parent.parent
    console.print("\n" + "=" * 70)
    console.print("  ERP Financial Statement Platform - Manual Adjustments Agent")
    console.print("=" * 70 + "\n")

    console.print("[yellow]Ingesting manual adjustments and active Chart of Accounts...[/yellow]")
    pipeline = AdjustmentPipeline(project_dir)
    results = pipeline.run()

    # Summary Panel
    summary_text = (
        f"[bold white]Reporting Period:[/bold white] {results['period']} | "
        f"[bold white]Functional Currency:[/bold white] {results['currency']}\n"
        f"[bold green]Accepted (Clean):[/bold green] {results['accepted_count']} | "
        f"[bold yellow]Quarantined (Review):[/bold yellow] {results['quarantined_count']} | "
        f"[bold red]Rejected (Violations):[/bold red] {results['rejected_count']} | "
        f"[bold white]Total Evaluated:[/bold white] {results['total_processed']}"
    )
    console.print(Panel(summary_text, title="Batch Validation Summary", expand=False))

    # Results Table
    table = Table(title="Journal Entries Validation & Reasoning Log", show_lines=True)
    table.add_column("Entry ID", style="bold cyan", width=9)
    table.add_column("Description", style="white", width=26)
    table.add_column("Debits ($)", justify="right", style="green", width=12)
    table.add_column("Credits ($)", justify="right", style="green", width=12)
    table.add_column("Status", justify="center", width=13)
    table.add_column("AI Agent Explanation & Action", style="white", width=45)
    table.add_column("Audit Trace ID", style="dim", width=17)

    for ev in results["evaluations"]:
        status_styled = {
            EntryStatus.ACCEPTED: "[bold green]ACCEPTED[/bold green]",
            EntryStatus.QUARANTINED: "[bold yellow]QUARANTINED[/bold yellow]",
            EntryStatus.REJECTED: "[bold red]REJECTED[/bold red]",
        }.get(ev.status, str(ev.status))

        reasoning = ""
        if ev.agent_explanation:
            if ev.status == EntryStatus.ACCEPTED:
                reasoning = "[dim green]Clean. Mathematical and COA checks verified.[/dim green]"
            else:
                reasoning = (
                    f"[bold]Issue:[/bold] {ev.agent_explanation.plain_english_summary}\n"
                    f"[bold cyan]Action:[/bold cyan] {ev.agent_explanation.recommended_action}"
                )

        table.add_row(
            ev.entry_id,
            ev.description,
            f"{float(ev.total_debit):,.2f}",
            f"{float(ev.total_credit):,.2f}",
            status_styled,
            reasoning,
            ev.audit_trace_id,
        )

    console.print(table)

    console.print("\n[bold green]Artifacts generated successfully in outputs folder:[/bold green]")
    console.print("  - [cyan]outputs/validated_adjustments.json[/cyan] (clean batch for GL posting)")
    console.print("  - [cyan]outputs/rejections_and_quarantines_detailed.json[/cyan] (flagged entries with AI remediation)")
    console.print("  - [cyan]outputs/audit_trail_log.csv[/cyan] (SOX-compliant audit trail with trace IDs)")
    console.print("  - [cyan]outputs/executive_summary.md[/cyan] (Controller executive briefing)\n")


if __name__ == "__main__":
    run_cli()
