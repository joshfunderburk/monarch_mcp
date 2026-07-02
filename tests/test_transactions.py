"""Tests for transaction payload flattening."""

from monarch.tools.transactions import _flatten_transaction


def _raw_txn(**overrides):
    txn = {
        "id": "tx1",
        "date": "2026-06-15",
        "amount": -42.5,
        "pending": False,
        "merchant": {"id": "m1", "name": "Costco"},
        "category": {"id": "c1", "name": "Groceries"},
        "account": {"id": "a1", "displayName": "Checking"},
        "notes": None,
        "needsReview": False,
        "reviewStatus": "reviewed",
        "isRecurring": False,
        "isSplitTransaction": False,
        "tags": [],
        "attachments": [],
        "hideFromReports": False,
    }
    txn.update(overrides)
    return txn


def test_flatten_keeps_key_fields_and_drops_false_flags():
    assert _flatten_transaction(_raw_txn()) == {
        "id": "tx1",
        "date": "2026-06-15",
        "amount": -42.5,
        "merchant": "Costco",
        "merchant_id": "m1",
        "category": "Groceries",
        "category_id": "c1",
        "account": "Checking",
        "account_id": "a1",
        "review_status": "reviewed",
    }


def test_flatten_keeps_true_flags_tags_and_attachments():
    flat = _flatten_transaction(
        _raw_txn(
            pending=True,
            needsReview=True,
            tags=[{"id": "t1", "name": "vacation"}],
            attachments=[{"id": "att1"}],
        )
    )
    assert flat["pending"] is True
    assert flat["needs_review"] is True
    assert flat["tags"] == ["vacation"]
    assert flat["tag_ids"] == ["t1"]
    assert flat["attachment_count"] == 1
