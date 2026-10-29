import pytest

from app.identity.passwords import PasswordHashError, ScryptPasswordHasher


def test_password_hash_round_trip_and_unique_salts() -> None:
    hasher = ScryptPasswordHasher(log_n=14)
    password = "correct horse battery staple"

    first = hasher.hash(password)
    second = hasher.hash(password)

    assert first.startswith("scrypt$ln=14,r=8,p=1$")
    assert first != second
    assert password not in first
    assert hasher.verify(password, first) is True
    assert hasher.verify("incorrect password", first) is False


def test_malformed_or_unsafe_hashes_fail_closed() -> None:
    hasher = ScryptPasswordHasher(log_n=14)

    assert hasher.verify("valid password here", "not-a-password-hash") is False
    assert hasher.verify(
        "valid password here",
        "scrypt$ln=30,r=8,p=1$c2FsdHNhbHRzYWx0c2FsdA$ZGlnaWVzdA",
    ) is False


def test_password_length_is_bounded() -> None:
    hasher = ScryptPasswordHasher(log_n=14)

    with pytest.raises(PasswordHashError, match="12 to 1024"):
        hasher.hash("too-short")
    with pytest.raises(PasswordHashError, match="12 to 1024"):
        hasher.hash("x" * 1_025)


def test_needs_rehash_detects_parameter_changes_and_malformed_values() -> None:
    original = ScryptPasswordHasher(log_n=14)
    stronger = ScryptPasswordHasher(log_n=15)
    encoded = original.hash("long enough password")

    assert original.needs_rehash(encoded) is False
    assert stronger.needs_rehash(encoded) is True
    assert original.needs_rehash("malformed") is True
