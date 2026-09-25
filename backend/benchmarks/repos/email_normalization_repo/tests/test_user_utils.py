import pytest

from user_utils import normalize_email


def test_normalize_email_trims_and_lowercases() -> None:
    assert normalize_email("  User@Example.COM ") == "user@example.com"


def test_normalize_email_rejects_missing_at_sign() -> None:
    with pytest.raises(ValueError):
        normalize_email("not-an-email")
