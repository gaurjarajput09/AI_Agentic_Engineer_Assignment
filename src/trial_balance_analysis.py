
import pandas as pd
from pathlib import Path

# Project folders
PROJECT_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_DIR / "inputs"
OUTPUT_DIR = PROJECT_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# Load input files
current_tb = pd.read_csv(INPUT_DIR / "trial_balance.csv")
prior_tb = pd.read_csv(INPUT_DIR / "prior_period_tb.csv")
fx_rates = pd.read_csv(INPUT_DIR / "fx_rates.csv")

# Clean column names and text values
for df in [current_tb, prior_tb, fx_rates]:
    df.columns = df.columns.str.strip()

for df in [current_tb, prior_tb]:
    df["account_code"] = df["account_code"].astype(str).str.strip()
    df["currency"] = df["currency"].astype(str).str.strip().str.upper()
    df["debit"] = pd.to_numeric(df["debit"], errors="raise")
    df["credit"] = pd.to_numeric(df["credit"], errors="raise")

fx_rates["currency"] = fx_rates["currency"].astype(str).str.strip().str.upper()
fx_rates["rate_type"] = fx_rates["rate_type"].astype(str).str.strip().str.lower()

# Use period-end rates for balance-sheet balances
end_rates = fx_rates[fx_rates["rate_type"] == "period_end"]
rate_lookup = dict(zip(end_rates["currency"], end_rates["rate"]))

# USD is the functional currency
if "USD" not in rate_lookup:
    rate_lookup["USD"] = 1.0

# Check that every currency has a period-end rate
currencies = set(current_tb["currency"]) | set(prior_tb["currency"])
missing = currencies - set(rate_lookup)

# Report currencies that have no period-end FX rate
if missing:
    missing_rows = []

    for source_name, df in [
        ("current_trial_balance", current_tb),
        ("prior_period_trial_balance", prior_tb),
    ]:
        for currency in sorted(missing):
            if currency in df["currency"].values:
                missing_rows.append({
                    "source": source_name,
                    "currency": currency,
                    "status": "MISSING_PERIOD_END_FX_RATE",
                    "action_required": "Confirm the correct rate from the source data"
                })

    fx_report = pd.DataFrame(missing_rows)
    fx_report.to_csv(OUTPUT_DIR / "fx_validation_report.csv", index=False)

    print("WARNING: Missing period-end FX rates:", sorted(missing))
    print("Missing-rate details saved to outputs/fx_validation_report.csv")
    print("Rows with missing rates will be excluded from this provisional comparison.")

    # Keep only currencies with valid period-end rates.
    current_tb = current_tb[current_tb["currency"].isin(rate_lookup)].copy()
    prior_tb = prior_tb[prior_tb["currency"].isin(rate_lookup)].copy()

    
# Calculate balances after filtering currencies with valid FX rates
for df in [current_tb, prior_tb]:
    df["local_balance"] = df["debit"] - df["credit"]
    df["fx_rate"] = df["currency"].map(rate_lookup)
    df["balance_usd"] = df["local_balance"] * df["fx_rate"]

# Summarize current-period balances
current_summary = (
    current_tb.groupby("account_code", as_index=False)["balance_usd"]
    .sum()
    .rename(columns={"balance_usd": "current_balance_usd"})
)

# Summarize prior-period balances
prior_summary = (
    prior_tb.groupby("account_code", as_index=False)["balance_usd"]
    .sum()
    .rename(columns={"balance_usd": "prior_balance_usd"})
)

# Compare the two periods
comparison = current_summary.merge(
    prior_summary,
    on="account_code",
    how="outer"
).fillna(0)

comparison["change_usd"] = (
    comparison["current_balance_usd"] - comparison["prior_balance_usd"]
)

comparison = comparison.round(2)

# Save the comparison report
output_file = OUTPUT_DIR / "trial_balance_comparison.csv"
comparison.to_csv(output_file, index=False)

print("\nProvisional trial balance comparison completed.")
print("\nFirst 10 account comparisons:")
print(comparison.head(10).to_string(index=False))
print("\nReport saved to:")
print(output_file)
print("\nIMPORTANT: GBP rows were excluded because the period-end FX rate is missing.")
print("This report is provisional and must not be treated as a complete financial statement.")
