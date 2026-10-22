
def get_status(debit, credit, account_exists=True, is_header=False,
               requires_review=False):
    """Simple rules used to test journal-entry validation."""

    difference = round(debit - credit, 2)

    if difference != 0:
        return "REJECT"

    if not account_exists or is_header or requires_review:
        return "QUARANTINE"

    return "ACCEPT"


def test_balanced_entry_is_accepted():
    assert get_status(100, 100) == "ACCEPT"


def test_unbalanced_entry_is_rejected():
    assert get_status(100, 90) == "REJECT"


def test_unknown_account_is_quarantined():
    assert get_status(100, 100, account_exists=False) == "QUARANTINE"


def test_header_account_is_quarantined():
    assert get_status(100, 100, is_header=True) == "QUARANTINE"


def test_intercompany_entry_requires_review():
    assert get_status(100, 100, requires_review=True) == "QUARANTINE"
