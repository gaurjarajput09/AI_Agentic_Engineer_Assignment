
import json
import pandas as pd
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

JSON_FILE = PROJECT_DIR / "inputs" / "manual_adjustments.json"
COA_FILE = PROJECT_DIR / "inputs" / "chart_of_accounts.csv"
OUTPUT_DIR = PROJECT_DIR / "outputs"
REPORT_FILE = OUTPUT_DIR / "validation_report.csv"

# Create output folder if it does not exist
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Load journal entries
with open(JSON_FILE, "r", encoding="utf-8") as file:
    data = json.load(file)

entries = data["entries"]

# Load Chart of Accounts
coa = pd.read_csv(COA_FILE, dtype=str)
coa.columns = coa.columns.str.strip()
coa["account_code"] = coa["account_code"].str.strip()
coa["account_type"] = coa["account_type"].fillna("").str.strip()

account_lookup = dict(
    zip(coa["account_code"], coa["account_type"])
)

results = []

for entry in entries:
    debit = sum(float(line["debit"]) for line in entry["lines"])
    credit = sum(float(line["credit"]) for line in entry["lines"])
    difference = round(debit - credit, 2)

    problems = []
    account_results = []

    # Check account validity
    for line in entry["lines"]:
        code = str(line["account"]).strip()

        if code not in account_lookup:
            problems.append(f"Unknown account: {code}")
            account_results.append(f"{code}: UNKNOWN")

        elif account_lookup[code].lower() == "header":
            problems.append(f"Header account not postable: {code}")
            account_results.append(f"{code}: HEADER")

        else:
            account_results.append(f"{code}: FOUND")

    # Check debit and credit balance
    if difference != 0:
        problems.append(f"Debit-credit difference: {difference:.2f}")

    # Flag intercompany transactions for additional review
    description = entry.get("description", "")
    if "intercompany" in description.lower():
        problems.append("Intercompany transaction requires review")

    # Assign final status
    if difference != 0:
        status = "REJECT"
    elif problems:
        status = "QUARANTINE"
    else:
        status = "ACCEPT"

    results.append({
        "entry_id": entry["id"],
        "date": entry.get("date", ""),
        "description": description,
        "source": entry.get("source", ""),
        "debit_total": round(debit, 2),
        "credit_total": round(credit, 2),
        "difference": difference,
        "account_checks": "; ".join(account_results),
        "status": status,
        "reasons": "; ".join(problems)
    })

# Save the report
report = pd.DataFrame(results)
report.to_csv(REPORT_FILE, index=False)

print("Validation completed!")
print("Total entries:", len(report))
print("\nStatus summary:")
print(report["status"].value_counts().to_string())
print("\nReport saved to:")
print(REPORT_FILE)
